"""Значок в трее и его меню.

AyatanaAppIndicator3 — то, что понимает Cinnamon; классический Gtk.StatusIcon в
современных панелях либо не показывается, либо показывается криво.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
try:
    gi.require_version("AyatanaAppIndicator3", "0.1")
    from gi.repository import AyatanaAppIndicator3 as AppIndicator

    HAVE_INDICATOR = True
except (ImportError, ValueError):  # pragma: no cover — зависит от системы
    AppIndicator = None
    HAVE_INDICATOR = False

from gi.repository import Gtk  # noqa: E402

log = logging.getLogger(__name__)

ASSETS = Path(__file__).resolve().parent.parent.parent / "assets"


class Tray:
    """Значок с меню. Если индикатора в системе нет, программа всё равно работает —
    просто без значка, поэтому все вызовы здесь безопасны при HAVE_INDICATOR=False."""

    def __init__(
        self,
        on_settings: Callable[[], None],
        on_copy_last: Callable[[], None],
        on_toggle_pause: Callable[[bool], None],
        on_quit: Callable[[], None],
        hotkey_label: str,
    ):
        self._on_toggle_pause = on_toggle_pause
        self._indicator = None

        self._menu = Gtk.Menu()

        self._hint = Gtk.MenuItem(label=f"Диктовка: удерживайте {hotkey_label}")
        self._hint.set_sensitive(False)
        self._menu.append(self._hint)
        self._menu.append(Gtk.SeparatorMenuItem())

        self._copy_item = Gtk.MenuItem(label="Скопировать последний текст")
        self._copy_item.connect("activate", lambda _: on_copy_last())
        self._copy_item.set_sensitive(False)
        self._menu.append(self._copy_item)

        settings_item = Gtk.MenuItem(label="Настройки…")
        settings_item.connect("activate", lambda _: on_settings())
        self._menu.append(settings_item)

        self._pause_item = Gtk.CheckMenuItem(label="Пауза (не слушать клавишу)")
        self._pause_item.connect("toggled", self._on_pause_toggled)
        self._menu.append(self._pause_item)

        self._menu.append(Gtk.SeparatorMenuItem())
        quit_item = Gtk.MenuItem(label="Выход")
        quit_item.connect("activate", lambda _: on_quit())
        self._menu.append(quit_item)

        self._menu.show_all()

        if not HAVE_INDICATOR:
            log.warning("AyatanaAppIndicator3 недоступен — работаем без значка в трее")
            return

        self._indicator = AppIndicator.Indicator.new_with_path(
            "golos",
            "golos",
            AppIndicator.IndicatorCategory.APPLICATION_STATUS,
            str(ASSETS),
        )
        self._indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)
        self._indicator.set_title("Golos — голосовая диктовка")
        self._indicator.set_menu(self._menu)

    def _on_pause_toggled(self, item) -> None:  # noqa: ANN001
        self._on_toggle_pause(item.get_active())

    def set_recording(self, recording: bool) -> None:
        if self._indicator is None:
            return
        self._indicator.set_icon_full(
            "golos-active" if recording else "golos",
            "идёт запись" if recording else "готов",
        )

    def set_has_history(self, has_history: bool) -> None:
        self._copy_item.set_sensitive(has_history)

    def set_hotkey_label(self, label: str) -> None:
        self._hint.set_label(f"Диктовка: удерживайте {label}")

    def set_paused(self, paused: bool) -> None:
        """Синхронизация галочки, когда паузу включили не из меню."""
        if self._pause_item.get_active() != paused:
            self._pause_item.handler_block_by_func(self._on_pause_toggled)
            self._pause_item.set_active(paused)
            self._pause_item.handler_unblock_by_func(self._on_pause_toggled)
