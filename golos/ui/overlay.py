"""Всплывающее окошко: индикатор записи, эквалайзер, статус распознавания.

Главное требование к этому окну — НЕ забирать фокус. Пользователь диктует в другое
приложение, и если окошко перехватит фокус, текст после вставки некуда будет печатать,
а в некоторых редакторах собьётся и позиция курсора. Отсюда набор подсказок оконному
менеджеру ниже: окно показывается, но остаётся неактивным.
"""

from __future__ import annotations

import logging
import math

import gi
import numpy as np

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

log = logging.getLogger(__name__)

BARS = 12
_FPS_MS = 50  # 20 кадров в секунду: глазу достаточно, а процессор нужен распознаванию
_ATTACK = 0.55  # быстрый подъём полосы
_DECAY = 0.16  # медленный спад: иначе полосы дёргаются и картинка выглядит нервной
_SILENCE_LEVEL = 0.004
_SILENCE_AFTER_S = 1.5

_WIDTH = 440
_HEIGHT = 68
_MARGIN_BOTTOM = 48
_RADIUS = 16

_CSS = b"""
#golos-overlay label { color: #f0f0f2; font-size: 13px; }
#golos-overlay #status { font-weight: bold; }
#golos-overlay #hint { color: #ffb454; font-size: 12px; }
#golos-overlay #timer { color: #a0a0a8; font-size: 12px; }
"""


class Overlay:
    """Одно окно на все состояния: запись → распознавание → результат."""

    def __init__(self):
        self._capture = None
        self._bars = np.zeros(BARS, dtype=np.float32)
        self._tick_source: int | None = None
        self._hide_source: int | None = None
        self._accent = (0.36, 0.78, 0.42)
        self._silent_since: float | None = None

        self._window = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        self._window.set_name("golos-overlay")
        self._window.set_decorated(False)
        self._window.set_accept_focus(False)  # не отбирать фокус у чужого окна
        self._window.set_focus_on_map(False)
        self._window.set_keep_above(True)
        self._window.set_skip_taskbar_hint(True)
        self._window.set_skip_pager_hint(True)
        self._window.set_resizable(False)
        self._window.set_type_hint(Gdk.WindowTypeHint.NOTIFICATION)
        self._window.set_default_size(_WIDTH, _HEIGHT)
        self._window.set_app_paintable(True)
        self._window.connect("draw", self._draw_background)
        self._window.connect("delete-event", lambda *_: self._window.hide_on_delete())

        self._transparent = self._setup_rgba()

        provider = Gtk.CssProvider()
        provider.load_from_data(_CSS)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        box.set_margin_start(20)
        box.set_margin_end(20)
        box.set_margin_top(12)
        box.set_margin_bottom(12)

        self._status = Gtk.Label(label="Запись")
        self._status.set_name("status")
        self._status.set_xalign(0.0)
        self._status.set_size_request(120, -1)
        self._status.set_ellipsize(3)  # PANGO_ELLIPSIZE_END

        # Эквалайзер и строка-подсказка занимают одно и то же место и никогда не видны
        # одновременно. set_no_show_all не даёт show_all() показать сразу оба.
        self._equalizer = Gtk.DrawingArea()
        self._equalizer.set_size_request(200, 30)
        self._equalizer.connect("draw", self._draw_bars)
        self._equalizer.set_no_show_all(True)

        self._preview = Gtk.Label(label="")
        self._preview.set_name("hint")
        self._preview.set_xalign(0.0)
        self._preview.set_ellipsize(3)
        self._preview.set_no_show_all(True)

        self._right = Gtk.Label(label="")
        self._right.set_name("timer")
        self._right.set_xalign(1.0)
        self._right.set_size_request(52, -1)

        box.pack_start(self._status, False, False, 0)
        box.pack_start(self._equalizer, True, True, 0)
        box.pack_start(self._preview, True, True, 0)
        box.pack_start(self._right, False, False, 0)
        self._window.add(box)

    # ------------------------------------------------------------------ внешний вид

    def _setup_rgba(self) -> bool:
        """Полупрозрачный фон возможен только при работающем композиторе.

        Проверяем в рантайме, а не предполагаем: без композитора прозрачный фон
        отрисуется чёрным прямоугольником, и окошко будет выглядеть сломанным.
        """
        screen = self._window.get_screen()
        visual = screen.get_rgba_visual()
        if visual is not None and screen.is_composited():
            self._window.set_visual(visual)
            return True
        log.info("композитор недоступен — окошко будет непрозрачным")
        return False

    def _draw_background(self, _widget, cr) -> bool:  # noqa: ANN001
        width = self._window.get_allocated_width()
        height = self._window.get_allocated_height()

        cr.set_operator(1 if not self._transparent else 0)  # SOURCE / CLEAR
        if self._transparent:
            cr.set_source_rgba(0, 0, 0, 0)
            cr.paint()
            cr.set_operator(2)  # OVER

        radius = _RADIUS if self._transparent else 0
        cr.new_sub_path()
        cr.arc(width - radius, radius, radius, -math.pi / 2, 0)
        cr.arc(width - radius, height - radius, radius, 0, math.pi / 2)
        cr.arc(radius, height - radius, radius, math.pi / 2, math.pi)
        cr.arc(radius, radius, radius, math.pi, 3 * math.pi / 2)
        cr.close_path()

        cr.set_source_rgba(0.10, 0.10, 0.12, 0.94 if self._transparent else 1.0)
        cr.fill_preserve()
        cr.set_source_rgba(1, 1, 1, 0.10)
        cr.set_line_width(1)
        cr.stroke()
        return False

    def _draw_bars(self, widget, cr) -> bool:  # noqa: ANN001
        width = widget.get_allocated_width()
        height = widget.get_allocated_height()
        gap = 3
        bar_w = max(2, (width - gap * (BARS - 1)) / BARS)

        red, green, blue = self._accent
        for i, value in enumerate(self._bars):
            bar_h = max(2.0, float(value) * height)
            x = i * (bar_w + gap)
            y = (height - bar_h) / 2
            # Тусклая «подложка» на всю высоту, чтобы полосы читались и в тишине.
            cr.set_source_rgba(1, 1, 1, 0.07)
            _rounded_bar(cr, x, (height - 2) / 2, bar_w, 2)
            cr.fill()
            cr.set_source_rgba(red, green, blue, 0.55 + 0.45 * float(value))
            _rounded_bar(cr, x, y, bar_w, bar_h)
            cr.fill()
        return False

    def _position(self) -> None:
        """Снизу по центру того монитора, где сейчас курсор."""
        screen = self._window.get_screen()
        display = screen.get_display()
        try:
            pointer = display.get_default_seat().get_pointer()
            monitor = display.get_monitor_at_point(*pointer.get_position()[1:3])
        except Exception:  # noqa: BLE001
            monitor = display.get_primary_monitor() or display.get_monitor(0)

        area = monitor.get_workarea()
        width, height = self._window.get_size()
        self._window.move(
            area.x + (area.width - width) // 2,
            area.y + area.height - height - _MARGIN_BOTTOM,
        )

    # -------------------------------------------------------------------- состояния

    def show_recording(self, capture) -> None:  # noqa: ANN001
        self._cancel_hide()
        self._capture = capture
        self._accent = (0.36, 0.78, 0.42)
        self._silent_since = None
        self._status.set_name("status")
        self._status.set_text("● Запись")
        self._right.set_text("0:00")
        self._show()
        if self._tick_source is None:
            self._tick_source = GLib.timeout_add(_FPS_MS, self._tick)

    def show_transcribing(self) -> None:
        self._stop_ticking()
        self._capture = None
        self._accent = (0.40, 0.60, 0.95)
        self._bars[:] = 0.0
        self._status.set_name("status")
        self._status.set_text("Распознаю…")
        self._right.set_text("")
        self._equalizer.queue_draw()
        self._show()

    def set_progress(self, index: int, total: int) -> None:
        if total > 1:
            self._right.set_text(f"{index}/{total}")

    def show_done(self, text: str) -> None:
        self._stop_ticking()
        self._accent = (0.36, 0.78, 0.42)
        self._status.set_name("status")
        self._status.set_text("✓ Скопировано")
        preview = text.strip().replace("\n", " ")
        self._right.set_text("")
        self._show(preview=preview[:60] + ("…" if len(preview) > 60 else ""))
        self._hide_after(1800)

    def show_error(self, message: str) -> None:
        self._stop_ticking()
        self._accent = (0.90, 0.40, 0.35)
        self._bars[:] = 0.0
        self._status.set_name("status")
        self._status.set_text("⚠ Ошибка")
        self._right.set_text("")
        self._show(preview=message)
        self._hide_after(4000)

    def show_progress_message(self, message: str) -> None:
        """Для первой загрузки модели: без этого выглядит как зависание."""
        self._stop_ticking()
        self._accent = (0.40, 0.60, 0.95)
        self._status.set_name("status")
        self._status.set_text("Подготовка")
        self._right.set_text("")
        self._show(preview=message)

    def hide(self) -> None:
        self._cancel_hide()
        self._stop_ticking()
        self._capture = None
        self._window.hide()

    def destroy(self) -> None:
        self.hide()
        self._window.destroy()

    # ----------------------------------------------------------------- внутреннее

    def _show(self, *, preview: str | None = None) -> None:
        """Показывает окно в одном из двух режимов: полосы или строка текста."""
        if preview is None:
            self._equalizer.set_visible(True)
            self._preview.set_visible(False)
        else:
            self._preview.set_text(preview)
            self._equalizer.set_visible(False)
            self._preview.set_visible(True)

        self._window.show_all()
        self._position()
        self._window.present()

    def _tick(self) -> bool:
        capture = self._capture
        if capture is None:
            self._tick_source = None
            return False

        target = capture.spectrum(BARS)
        rate = np.where(target > self._bars, _ATTACK, _DECAY)
        self._bars += (target - self._bars) * rate
        self._equalizer.queue_draw()

        elapsed = capture.elapsed
        self._right.set_text(f"{int(elapsed) // 60}:{int(elapsed) % 60:02d}")

        # «Звук идёт или нет» — то, ради чего эквалайзер и задумывался. Если микрофон
        # молчит дольше полутора секунд, честно об этом говорим, а не рисуем ровную линию.
        if capture.level < _SILENCE_LEVEL:
            if self._silent_since is None:
                self._silent_since = elapsed
            elif elapsed - self._silent_since > _SILENCE_AFTER_S:
                self._status.set_text("● Не слышу")
        else:
            self._silent_since = None
            self._status.set_text("● Запись")
        return True

    def _stop_ticking(self) -> None:
        if self._tick_source is not None:
            GLib.source_remove(self._tick_source)
            self._tick_source = None

    def _hide_after(self, ms: int) -> None:
        self._cancel_hide()
        self._hide_source = GLib.timeout_add(ms, self._on_hide_timeout)

    def _on_hide_timeout(self) -> bool:
        self._hide_source = None
        self._window.hide()
        return False

    def _cancel_hide(self) -> None:
        if self._hide_source is not None:
            GLib.source_remove(self._hide_source)
            self._hide_source = None


def _rounded_bar(cr, x: float, y: float, width: float, height: float) -> None:  # noqa: ANN001
    radius = min(width / 2, 2.0)
    cr.new_sub_path()
    cr.arc(x + width - radius, y + radius, radius, -math.pi / 2, 0)
    cr.arc(x + width - radius, y + height - radius, radius, 0, math.pi / 2)
    cr.arc(x + radius, y + height - radius, radius, math.pi / 2, math.pi)
    cr.arc(x + radius, y + radius, radius, math.pi, 3 * math.pi / 2)
    cr.close_path()
