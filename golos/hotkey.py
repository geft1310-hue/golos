"""Глобальный перехват одной клавиши через X11 XGrabKey.

ПОЧЕМУ ИМЕННО ТАК (S1). Напрашивающийся вариант — библиотека pynput — вешает
глобальный слушатель, который получает КАЖДОЕ нажатие в системе: пароли, переписку,
номера карт. Для программы, которой нужна ровно одна клавиша, это несоразмерно.
XGrabKey просит у X-сервера конкретный keycode, и всё остальное к нам не приходит.

ЧЕСТНАЯ ОГОВОРКА. XGrabKey — не полная изоляция. Когда пассивный захват срабатывает,
X начинает активный захват клавиатуры, и пока клавиша удерживается, прочие нажатия
могут приходить нам, а не в активное окно. Экспозиция падает с «весь ввод всегда» до
«только время удержания», но не до нуля. Отсюда правила ниже, которые важнее удобства:

  * событие с чужим keycode отбрасывается первой же строкой обработчика;
  * содержимое событий не логируется НИКОГДА, ни на каком уровне, включая debug;
  * в логи попадают только факты «нажато»/«отпущено», без данных.

АВТОПОВТОР. Классическая ловушка push-to-talk: при удержании обычной клавиши X шлёт
не одно нажатие, а череду release+press, и запись обрывается на первом же повторе.
Для правого Ctrl автоповтор выключен (проверено через xset q), но пользователь может
назначить любую клавишу, поэтому отпускание подтверждается с задержкой в 30 мс:
если за это время пришло нажатие того же keycode — это был автоповтор, а не отпускание.
"""

from __future__ import annotations

import logging
import select
import threading
import time
from collections.abc import Callable

from Xlib import X, XK, display as xdisplay, error as xerror

log = logging.getLogger(__name__)

# Пауза, за которую отличаем автоповтор от настоящего отпускания.
_AUTOREPEAT_GUARD_S = 0.03
# Как часто просыпаемся, чтобы проверить флаг остановки и таймеры.
_POLL_S = 0.05


class HotkeyError(RuntimeError):
    """Клавишу не удалось захватить — обычно её уже занял кто-то другой."""


def keycode_label(keycode: int) -> str:
    """Человеческое имя клавиши для окна настроек."""
    try:
        disp = xdisplay.Display()
    except Exception:  # noqa: BLE001
        return f"код {keycode}"
    try:
        keysym = disp.keycode_to_keysym(keycode, 0)
        name = XK.keysym_to_string(keysym) if keysym else None
        if not name:
            for attr in dir(XK):
                if attr.startswith("XK_") and getattr(XK, attr) == keysym:
                    name = attr[3:]
                    break
        return name or f"код {keycode}"
    finally:
        disp.close()


class HotkeyListener:
    """Слушает удержание одной клавиши в отдельном потоке со своим соединением к X.

    Отдельное соединение принципиально: Xlib не потокобезопасен при разделении одного
    Display между потоками, а GTK держит своё собственное.
    """

    def __init__(
        self,
        keycode: int,
        on_press: Callable[[], None],
        on_release: Callable[[float], None],
    ):
        self._keycode = keycode
        self._on_press = on_press
        self._on_release = on_release

        self._display = None
        self._root = None
        self._thread: threading.Thread | None = None
        self._running = threading.Event()
        self._grabbed = False
        self._paused = False

        self._pressed_at: float | None = None
        self._pending_release_at: float | None = None

    # ------------------------------------------------------------ жизненный цикл

    def start(self) -> None:
        if self._thread is not None:
            return
        self._display = xdisplay.Display()
        self._root = self._display.screen().root
        # Маску на корневое окно СОЗНАТЕЛЬНО не выставляем. X доставляет события
        # захваченной клавиши окну захвата и без неё, а KeyPressMask на root — это
        # запрос «присылайте нам нажатия», то есть ровно то расширение доступа,
        # которого мы избегаем по S1.
        self._grab()

        self._running.set()
        self._thread = threading.Thread(target=self._loop, name="hotkey", daemon=True)
        self._thread.start()
        log.info("перехват клавиши включён (keycode %d)", self._keycode)

    def stop(self) -> None:
        self._running.clear()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=1.0)
        self._ungrab()
        if self._display is not None:
            try:
                self._display.close()
            except Exception:  # noqa: BLE001
                pass
            self._display = None

    def set_paused(self, paused: bool) -> None:
        """Пауза из меню в трее: снимаем захват совсем, а не игнорируем события.

        Разница существенная: снятый захват означает, что программа физически
        не получает эту клавишу, а не «получает, но делает вид».
        """
        if paused == self._paused:
            return
        self._paused = paused
        if paused:
            self._ungrab()
            self._pressed_at = None
            self._pending_release_at = None
        else:
            self._grab()
        log.info("перехват клавиши %s", "приостановлен" if paused else "возобновлён")

    @property
    def is_paused(self) -> bool:
        return self._paused

    @property
    def keycode(self) -> int:
        return self._keycode

    def rebind(self, keycode: int) -> None:
        """Смена клавиши из настроек."""
        self._ungrab()
        self._keycode = keycode
        self._pressed_at = None
        self._pending_release_at = None
        if not self._paused:
            self._grab()

    # -------------------------------------------------------------------- захват

    def _grab(self) -> None:
        if self._grabbed or self._root is None or self._display is None:
            return
        catcher = xerror.CatchError(xerror.BadAccess)
        # X.AnyModifier — чтобы захват не разваливался при включённых NumLock/CapsLock:
        # с ними X считает комбинацию другой и без AnyModifier клавиша просто не сработает.
        self._root.grab_key(
            self._keycode,
            X.AnyModifier,
            True,
            X.GrabModeAsync,
            X.GrabModeAsync,
            onerror=catcher,
        )
        self._display.sync()
        if catcher.get_error():
            raise HotkeyError(
                f"клавишу (код {self._keycode}) уже использует другая программа — "
                "выберите другую в настройках"
            )
        self._grabbed = True

    def _ungrab(self) -> None:
        if not self._grabbed or self._root is None or self._display is None:
            return
        try:
            self._root.ungrab_key(self._keycode, X.AnyModifier)
            self._display.sync()
        except Exception as exc:  # noqa: BLE001
            log.warning("не удалось снять захват клавиши: %s", exc)
        self._grabbed = False

    # --------------------------------------------------------------- цикл событий

    def _loop(self) -> None:
        display = self._display
        assert display is not None
        fd = display.fileno()

        while self._running.is_set():
            # select, а не блокирующий next_event: иначе поток невозможно остановить
            # и некому подтвердить отложенное отпускание.
            select.select([fd], [], [], _POLL_S)

            while display.pending_events():
                self._handle(display.next_event())

            self._check_pending_release()

    def _handle(self, event) -> None:  # noqa: ANN001
        # S1: первая же строка — всё чужое отбрасывается молча и не логируется.
        if event.type not in (X.KeyPress, X.KeyRelease):
            return
        if event.detail != self._keycode:
            return
        if self._paused:
            return

        if event.type == X.KeyPress:
            if self._pending_release_at is not None:
                # Это был автоповтор: отпускания не было, отменяем его подтверждение.
                self._pending_release_at = None
                return
            if self._pressed_at is not None:
                return  # уже пишем, повторное нажатие игнорируем
            self._pressed_at = time.monotonic()
            self._safe_call(self._on_press)
        else:
            if self._pressed_at is None:
                return
            self._pending_release_at = time.monotonic()

    def _check_pending_release(self) -> None:
        """Подтверждает отпускание, если за защитный интервал не пришёл автоповтор."""
        if self._pending_release_at is None or self._pressed_at is None:
            return
        if time.monotonic() - self._pending_release_at < _AUTOREPEAT_GUARD_S:
            return

        held = self._pending_release_at - self._pressed_at
        self._pressed_at = None
        self._pending_release_at = None
        self._safe_call(lambda: self._on_release(held))

    @staticmethod
    def _safe_call(func: Callable[[], None]) -> None:
        """Исключение в обработчике не должно убивать поток перехвата: иначе горячая
        клавиша молча перестаёт работать до перезапуска программы."""
        try:
            func()
        except Exception:  # noqa: BLE001
            log.exception("ошибка в обработчике горячей клавиши")
