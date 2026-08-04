"""Точка входа: связывает клавишу, микрофон, распознавание и буфер обмена.

Потоковая схема. Главный поток занят циклом GTK и только им — любая тяжёлая работа
в нём означает подвисший интерфейс. Всё остальное живёт в отдельных потоках и
возвращается в GTK строго через GLib.idle_add:

    поток перехвата клавиши  ──нажатие/отпускание──┐
    поток PortAudio (звук)   ──блоки, громкость────┤
    поток загрузки модели    ──готова──────────────┼──► GLib.idle_add ──► GTK
    поток распознавания      ──текст───────────────┘

Приём, ради которого всё это затевалось: загрузка модели стартует ОДНОВРЕМЕННО
с началом записи. Пока человек говорит (обычно 3+ секунды), модель успевает
подняться с диска за ~1.8 с. К моменту отпускания клавиши она уже готова, и
выгрузка по простою обходится почти бесплатно.
"""

from __future__ import annotations

import logging
import signal
import sys
import threading

import gi
import numpy as np

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

from golos.asr import ModelFilesMissingError, Recognizer, verify_model_files  # noqa: E402
from golos.audio import AudioCapture, AudioError  # noqa: E402
from golos.clipboard import ClipboardManager  # noqa: E402
from golos.config import Config, ensure_dirs  # noqa: E402
from golos.hotkey import HotkeyError, HotkeyListener  # noqa: E402
from golos.platform_check import HEADLESS, session_kind, session_problem  # noqa: E402
from golos.ui.overlay import Overlay  # noqa: E402
from golos.ui.settings import SettingsDialog  # noqa: E402
from golos.ui.tray import Tray  # noqa: E402

log = logging.getLogger("golos")

IDLE, RECORDING, TRANSCRIBING = "idle", "recording", "transcribing"
_UNLOAD_CHECK_S = 30
_MIN_SAMPLES_S = 0.25


class GolosApp:
    def __init__(self, config: Config):
        self._config = config
        self._state = IDLE

        self._recognizer = Recognizer(config)
        self._capture = AudioCapture(config.input_device)
        self._clipboard = ClipboardManager(config.clipboard_clear_seconds)
        self._overlay = Overlay()
        self._tray = Tray(
            on_settings=self._open_settings,
            on_copy_last=self._copy_last,
            on_toggle_pause=self._set_paused,
            on_quit=self.quit,
            hotkey_label=config.hotkey_label,
        )
        self._hotkey = HotkeyListener(
            config.hotkey_keycode,
            on_press=lambda: GLib.idle_add(self._start_recording),
            on_release=lambda held: GLib.idle_add(self._stop_recording, held),
        )

    # ------------------------------------------------------------------- запуск

    def start(self) -> bool:
        try:
            self._hotkey.start()
        except HotkeyError as exc:
            _error_dialog(str(exc))
            return False

        GLib.timeout_add_seconds(_UNLOAD_CHECK_S, self._check_idle_unload)
        threading.Thread(target=self._verify_model, name="verify", daemon=True).start()
        log.info("готов: удерживайте %s", self._config.hotkey_label)
        return True

    def _verify_model(self) -> None:
        """S5: сверка контрольных сумм при старте, в фоне.

        Именно при старте, а не при каждой загрузке модели: хеширование 215 МБ на этом
        процессоре занимает секунды, и в момент нажатия клавиши это было бы заметно.
        """
        model_dir = self._config.model_dir
        if not (model_dir / "manifest.json").is_file():
            GLib.idle_add(
                self._overlay.show_error,
                "Нет файлов модели. Запустите ./install.sh",
            )
            return
        ok, message = verify_model_files(model_dir)
        if ok:
            log.info("проверка целостности модели: %s", message)
        else:
            log.error("проверка целостности модели не прошла: %s", message)
            GLib.idle_add(self._overlay.show_error, f"Файлы модели изменились: {message}")

    # -------------------------------------------------------------- запись речи

    def _start_recording(self) -> bool:
        if self._state != IDLE:
            return False

        try:
            self._capture.start()
        except AudioError as exc:
            log.error("микрофон недоступен: %s", exc)
            self._overlay.show_error("Микрофон недоступен")
            return False

        self._state = RECORDING
        self._overlay.show_recording(self._capture)
        self._tray.set_recording(True)

        # Модель поднимается параллельно речи — к отпусканию клавиши она уже готова.
        threading.Thread(target=self._preload, name="preload", daemon=True).start()
        return False

    def _preload(self) -> None:
        try:
            self._recognizer.ensure_loaded()
        except ModelFilesMissingError:
            pass  # об этом уже сообщила проверка при старте
        except Exception:
            log.exception("не удалось загрузить модель заранее")

    def _stop_recording(self, held: float) -> bool:
        if self._state != RECORDING:
            return False
        self._tray.set_recording(False)

        # Случайное касание клавиши не должно будить модель и мигать окошком.
        if held < self._config.min_press_seconds:
            self._capture.cancel()
            self._state = IDLE
            self._overlay.hide()
            return False

        pcm, sample_rate = self._capture.stop()
        if len(pcm) < sample_rate * _MIN_SAMPLES_S:
            self._state = IDLE
            self._overlay.hide()
            return False

        self._state = TRANSCRIBING
        self._overlay.show_transcribing()
        threading.Thread(
            target=self._transcribe, args=(pcm, sample_rate), name="asr", daemon=True
        ).start()
        return False

    def _transcribe(self, pcm: np.ndarray, sample_rate: int) -> None:
        try:
            text = self._recognizer.transcribe(
                pcm,
                sample_rate,
                progress=lambda i, n: GLib.idle_add(self._overlay.set_progress, i, n),
            )
        except ModelFilesMissingError:
            GLib.idle_add(self._fail, "Файлы модели не найдены")
            return
        except Exception as exc:
            log.exception("ошибка распознавания")
            GLib.idle_add(self._fail, f"Не удалось распознать: {type(exc).__name__}")
            return
        finally:
            # S4: речь не должна оставаться в памяти дольше необходимого.
            pcm.fill(0)

        GLib.idle_add(self._finish, text)

    def _finish(self, text: str) -> bool:
        self._state = IDLE
        if not text.strip():
            self._overlay.show_error("Ничего не распознано")
            return False
        self._clipboard.copy(text)
        self._tray.set_has_history(True)
        self._overlay.show_done(text)
        log.info("распознано символов: %d", len(text))  # сам текст в лог не пишем
        return False

    def _fail(self, message: str) -> bool:
        self._state = IDLE
        self._overlay.show_error(message)
        return False

    # ------------------------------------------------------------------- прочее

    def _check_idle_unload(self) -> bool:
        if self._state == IDLE:
            self._recognizer.maybe_unload()
        return True

    def _copy_last(self) -> None:
        self._clipboard.copy_last()

    def _set_paused(self, paused: bool) -> None:
        self._hotkey.set_paused(paused)
        self._tray.set_paused(paused)

    def _open_settings(self) -> None:
        dialog = SettingsDialog(self._config, self._apply_settings)
        if dialog.run() == Gtk.ResponseType.OK:
            dialog.apply()
        dialog.destroy()

    def _apply_settings(self, config: Config) -> None:
        old_keycode = self._hotkey.keycode
        self._config = config
        self._clipboard.set_clear_after(config.clipboard_clear_seconds)
        self._capture = AudioCapture(config.input_device)
        self._tray.set_hotkey_label(config.hotkey_label)

        if config.hotkey_keycode != old_keycode:
            try:
                self._hotkey.rebind(config.hotkey_keycode)
            except HotkeyError as exc:
                _error_dialog(str(exc))

        # Смена модели требует перезагрузки: старую выгружаем, новая поднимется
        # при следующем нажатии клавиши — в фоне, как обычно.
        self._recognizer.apply_config(config)

    def quit(self) -> None:
        log.info("выход")
        self._hotkey.stop()
        if self._capture.is_recording:
            self._capture.cancel()
        self._clipboard.wipe()  # S3: история — только в памяти, при выходе стирается
        self._recognizer.unload()
        self._overlay.destroy()
        Gtk.main_quit()


def _error_dialog(message: str) -> None:
    dialog = Gtk.MessageDialog(
        message_type=Gtk.MessageType.ERROR, buttons=Gtk.ButtonsType.OK, text="Golos"
    )
    dialog.format_secondary_text(message)
    dialog.run()
    dialog.destroy()


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # Проверяем окружение до всего остального. Под Wayland перехват клавиши не
    # выдаст ошибку — он просто не сработает ни разу, и человек будет смотреть на
    # запущенную программу, которая молчит. Лучше объяснить сразу.
    problem = session_problem()
    if problem is not None:
        print(f"Golos: {problem}", file=sys.stderr)
        if session_kind() != HEADLESS:
            _error_dialog(problem)  # без экрана окно показать всё равно негде
        return 2

    ensure_dirs()
    config = Config.load()

    app = GolosApp(config)
    if not app.start():
        return 1

    # Ctrl+C в терминале должен закрывать программу штатно, а не оставлять
    # висеть захват клавиши и открытый микрофон.
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, lambda: (app.quit(), True)[1])
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, lambda: (app.quit(), True)[1])

    Gtk.main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
