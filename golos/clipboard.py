"""Буфер обмена и история распознанного — только в памяти.

S2. Текст кладётся через Gtk.Clipboard.set_text() и НИКОГДА через store().
store() — это явная просьба к менеджеру буфера сохранить содержимое у себя, чтобы
оно пережило закрытие программы. Для надиктованного текста это означает, что сказанное
вслух окажется в истории буфера обмена, то есть на диске. Ровно то, чего быть не должно.

S3. История последних распознаваний живёт в оперативной памяти, на диск не пишется
никогда и стирается при выходе.
"""

from __future__ import annotations

import logging

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

log = logging.getLogger(__name__)

HISTORY_LIMIT = 20


class ClipboardManager:
    """Обёртка над буфером обмена GTK. Все методы вызывать только из потока GTK."""

    def __init__(self, clear_after_seconds: int = 0):
        self._clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        self._history: list[str] = []
        self._clear_after = clear_after_seconds
        self._clear_source: int | None = None

    def set_clear_after(self, seconds: int) -> None:
        self._clear_after = max(0, int(seconds))

    def copy(self, text: str) -> None:
        """Кладёт текст в буфер и запоминает его в истории (в памяти)."""
        if not text:
            return

        self._clipboard.set_text(text, -1)
        # store() здесь НЕ вызывается сознательно — см. S2 в шапке файла.

        self._history.append(text)
        del self._history[:-HISTORY_LIMIT]

        self._cancel_clear()
        if self._clear_after > 0:
            self._clear_source = GLib.timeout_add_seconds(
                self._clear_after, self._clear_if_ours, text
            )

    @property
    def last(self) -> str | None:
        return self._history[-1] if self._history else None

    @property
    def history(self) -> list[str]:
        return list(self._history)

    def copy_last(self) -> None:
        text = self.last
        if text:
            self.copy(text)

    # ----------------------------------------------------------------- очистка

    def _cancel_clear(self) -> None:
        if self._clear_source is not None:
            GLib.source_remove(self._clear_source)
            self._clear_source = None

    def _clear_if_ours(self, text: str) -> bool:
        """Очищает буфер, но только если там всё ещё наш текст.

        Проверка обязательна: за это время пользователь мог скопировать что-то своё,
        и стирать чужое — грубая ошибка.
        """
        self._clear_source = None
        current = self._clipboard.wait_for_text()
        if current == text:
            self._clipboard.set_text("", -1)
            log.info("буфер обмена очищен по таймеру")
        return False  # GLib: больше не повторять

    def wipe(self) -> None:
        """Полная очистка при выходе (S3): история — из памяти, наш текст — из буфера."""
        self._cancel_clear()
        last = self.last
        self._history.clear()
        if last and self._clipboard.wait_for_text() == last:
            self._clipboard.set_text("", -1)
