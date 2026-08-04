"""Окно настроек. Намеренно простое: шесть строк и ни одной технической.

Подписи написаны обычными словами, а не терминами: «модель», «квантование» и
«intra_op_num_threads» пользователю ничего не говорят, поэтому их здесь нет.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

from golos.audio import list_input_devices  # noqa: E402
from golos.config import CONFIG_DIR, Config  # noqa: E402
from golos.hotkey import keycode_label  # noqa: E402

log = logging.getLogger(__name__)

AUTOSTART_PATH = Path.home() / ".config/autostart/golos.desktop"
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

MODEL_CHOICES = [
    ("gigaam-v3-e2e-ctc", "С запятыми и заглавными буквами (рекомендуется)"),
    ("gigaam-v3-ctc", "Чуть быстрее, но без знаков препинания"),
]


class SettingsDialog(Gtk.Dialog):
    """Возвращает изменённый Config через колбэк on_apply."""

    def __init__(self, config: Config, on_apply):  # noqa: ANN001
        super().__init__(title="Настройки Golos", flags=0)
        self.set_default_size(460, -1)
        self.set_resizable(False)
        self._config = config
        self._on_apply = on_apply
        self._keycode = config.hotkey_keycode

        self.add_button("Отмена", Gtk.ResponseType.CANCEL)
        self.add_button("Сохранить", Gtk.ResponseType.OK)

        grid = Gtk.Grid(row_spacing=12, column_spacing=14)
        grid.set_margin_start(18)
        grid.set_margin_end(18)
        grid.set_margin_top(18)
        grid.set_margin_bottom(12)

        row = 0

        # --- клавиша ---
        self._key_button = Gtk.Button(label=config.hotkey_label)
        self._key_button.connect("clicked", self._on_pick_key)
        grid.attach(_label("Клавиша для диктовки:"), 0, row, 1, 1)
        grid.attach(self._key_button, 1, row, 1, 1)
        row += 1

        hint = Gtk.Label(label="Удерживайте её, пока говорите. Отпустили — текст в буфере.")
        hint.set_xalign(0.0)
        hint.get_style_context().add_class("dim-label")
        grid.attach(hint, 0, row, 2, 1)
        row += 1

        # --- микрофон ---
        self._device_combo = Gtk.ComboBoxText()
        self._device_combo.append("", "Как в системе")
        for index, name in list_input_devices():
            self._device_combo.append(str(index), name)
        self._device_combo.set_active_id(
            "" if config.input_device is None else str(config.input_device)
        )
        grid.attach(_label("Микрофон:"), 0, row, 1, 1)
        grid.attach(self._device_combo, 1, row, 1, 1)
        row += 1

        # --- модель ---
        self._model_combo = Gtk.ComboBoxText()
        for name, title in MODEL_CHOICES:
            self._model_combo.append(name, title)
        self._model_combo.set_active_id(config.model)
        grid.attach(_label("Распознавание:"), 0, row, 1, 1)
        grid.attach(self._model_combo, 1, row, 1, 1)
        row += 1

        # --- выгрузка из памяти ---
        self._unload_check = Gtk.CheckButton(label="Освобождать память после простоя")
        self._unload_check.set_active(config.idle_unload_seconds > 0)
        self._unload_check.connect("toggled", self._sync_sensitivity)
        self._unload_spin = Gtk.SpinButton.new_with_range(1, 120, 1)
        self._unload_spin.set_value(max(1, config.idle_unload_seconds // 60))
        grid.attach(self._unload_check, 0, row, 1, 1)
        grid.attach(_with_suffix(self._unload_spin, "минут"), 1, row, 1, 1)
        row += 1

        # --- очистка буфера ---
        self._clear_check = Gtk.CheckButton(label="Очищать буфер обмена через")
        self._clear_check.set_active(config.clipboard_clear_seconds > 0)
        self._clear_check.connect("toggled", self._sync_sensitivity)
        self._clear_spin = Gtk.SpinButton.new_with_range(5, 600, 5)
        self._clear_spin.set_value(config.clipboard_clear_seconds or 60)
        grid.attach(self._clear_check, 0, row, 1, 1)
        grid.attach(_with_suffix(self._clear_spin, "секунд"), 1, row, 1, 1)
        row += 1

        # --- автозапуск ---
        self._autostart_check = Gtk.CheckButton(label="Запускать при входе в систему")
        self._autostart_check.set_active(AUTOSTART_PATH.is_file())
        grid.attach(self._autostart_check, 0, row, 2, 1)
        row += 1

        footer = Gtk.Label()
        footer.set_markup(
            "<small>Речь распознаётся на этом компьютере. "
            "Ни звук, ни текст никуда не отправляются.</small>"
        )
        footer.set_xalign(0.0)
        grid.attach(footer, 0, row, 2, 1)

        self.get_content_area().add(grid)
        self._sync_sensitivity()
        self.show_all()

    def _sync_sensitivity(self, *_args) -> None:
        self._unload_spin.set_sensitive(self._unload_check.get_active())
        self._clear_spin.set_sensitive(self._clear_check.get_active())

    # ------------------------------------------------------------- выбор клавиши

    def _on_pick_key(self, _button) -> None:  # noqa: ANN001
        """Ловит следующее нажатие обычным обработчиком окна.

        Именно обычным, а не глобальным перехватом: чтобы назначить клавишу, не нужно
        слушать всю систему — достаточно событий собственного окна, пока оно в фокусе.
        """
        dialog = Gtk.Dialog(title="Нажмите клавишу", transient_for=self, flags=0)
        dialog.set_default_size(320, 120)
        label = Gtk.Label(label="\nНажмите клавишу, которой будете диктовать.\n"
                                "Esc — отмена.\n")
        dialog.get_content_area().add(label)
        dialog.show_all()

        captured: dict[str, int] = {}

        def on_key(_widget, event) -> bool:  # noqa: ANN001
            if event.keyval == 0xFF1B:  # Escape
                dialog.response(Gtk.ResponseType.CANCEL)
                return True
            captured["keycode"] = event.hardware_keycode
            dialog.response(Gtk.ResponseType.OK)
            return True

        dialog.connect("key-press-event", on_key)
        response = dialog.run()
        dialog.destroy()

        if response == Gtk.ResponseType.OK and "keycode" in captured:
            self._keycode = captured["keycode"]
            self._key_button.set_label(keycode_label(self._keycode))

    # ---------------------------------------------------------------- сохранение

    def collect(self) -> Config:
        config = self._config
        config.hotkey_keycode = self._keycode
        config.hotkey_label = self._key_button.get_label()

        device_id = self._device_combo.get_active_id()
        config.input_device = int(device_id) if device_id else None

        config.model = self._model_combo.get_active_id() or config.model
        config.idle_unload_seconds = (
            int(self._unload_spin.get_value()) * 60 if self._unload_check.get_active() else 0
        )
        config.clipboard_clear_seconds = (
            int(self._clear_spin.get_value()) if self._clear_check.get_active() else 0
        )
        config.validate()
        return config

    def apply(self) -> None:
        config = self.collect()
        config.save()
        set_autostart(self._autostart_check.get_active())
        self._on_apply(config)


def set_autostart(enabled: bool) -> None:
    """Пишет или удаляет ярлык автозапуска в ~/.config/autostart."""
    if not enabled:
        AUTOSTART_PATH.unlink(missing_ok=True)
        return

    AUTOSTART_PATH.parent.mkdir(parents=True, exist_ok=True)
    run_sh = PROJECT_ROOT / "run.sh"
    command = str(run_sh) if run_sh.is_file() else f"{sys.executable} -m golos"
    # Своя иконка, если она на месте; иначе — системная, которая есть в любой теме.
    icon_file = PROJECT_ROOT / "assets/golos.svg"
    icon = str(icon_file) if icon_file.is_file() else "audio-input-microphone"
    AUTOSTART_PATH.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Golos\n"
        "Comment=Голосовая диктовка\n"
        f"Exec={command}\n"
        f"Icon={icon}\n"
        "Terminal=false\n"
        "X-GNOME-Autostart-enabled=true\n",
        encoding="utf-8",
    )
    os.chmod(AUTOSTART_PATH, 0o600)
    log.info("автозапуск включён: %s", AUTOSTART_PATH)


def _label(text: str) -> Gtk.Label:
    label = Gtk.Label(label=text)
    label.set_xalign(0.0)
    return label


def _with_suffix(widget: Gtk.Widget, suffix: str) -> Gtk.Box:
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    box.pack_start(widget, False, False, 0)
    box.pack_start(Gtk.Label(label=suffix), False, False, 0)
    return box


# Каталог конфигурации создаётся заранее, чтобы «Сохранить» не падало на первом запуске.
CONFIG_DIR.mkdir(parents=True, exist_ok=True)
