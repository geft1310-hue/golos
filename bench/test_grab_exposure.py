#!/usr/bin/env python3
"""S1: куда уходят посторонние нажатия, пока горячая клавиша удерживается.

Это тот самый эксперимент из плана, только автоматический. Вопрос стоит так:
пассивный грэб клавиши при срабатывании переходит в активный грэб клавиатуры, и
теоретически ВСЕ нажатия на время удержания достаются программе, а не окну, в
котором человек печатает. Если это так — приложение в момент диктовки видит чужой
ввод, и об этом надо честно написать в README.

Проверка: открываем своё окно с полем ввода, забираем фокус, синтезируем через
XTEST последовательность «зажать горячую клавишу → нажать букву → отпустить» и
смотрим на два независимых факта:
  1. дошла ли буква до поля ввода (значит, чужой ввод идёт мимо нас);
  2. увидел ли её наш перехватчик.

Буква синтезируется в собственное окно, поэтому в чужие программы ничего не попадёт.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import gi
from Xlib import X, XK, display
from Xlib.ext import xtest

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from golos.hotkey import HotkeyListener  # noqa: E402

HOTKEY_KEYCODE = 105  # правый Ctrl — то, что стоит по умолчанию
PROBE_KEYSYM = XK.string_to_keysym("a")


class Probe:
    """Считает, что увидел перехватчик, и что дошло до поля ввода."""

    def __init__(self):
        self.presses = 0
        self.releases = 0
        self.foreign_seen_by_us = 0
        self.entry_text = ""


def install_spy(listener: HotkeyListener, probe: Probe) -> None:
    """Временно снимает фильтр S1, чтобы УВИДЕТЬ, что вообще прилетает.

    В боевом коде чужие события отбрасываются первой строкой обработчика и никуда
    не попадают. Здесь нам нужно ровно обратное: посчитать, приходят ли они вообще.
    Считаем только количество и только в этом тесте — сами keycode не сохраняем.
    """
    original = listener._handle

    def spy(event):  # noqa: ANN001
        if event.type == X.KeyPress and event.detail != listener.keycode:
            probe.foreign_seen_by_us += 1
        original(event)

    listener._handle = spy


def main() -> int:
    probe = Probe()

    window = Gtk.Window(title="Golos: проверка S1")
    entry = Gtk.Entry()
    window.add(entry)
    window.set_keep_above(True)
    window.show_all()
    entry.grab_focus()

    listener = HotkeyListener(
        HOTKEY_KEYCODE,
        on_press=lambda: setattr(probe, "presses", probe.presses + 1),
        on_release=lambda held: setattr(probe, "releases", probe.releases + 1),
    )
    install_spy(listener, probe)
    listener.start()

    fake = display.Display()
    probe_keycode = fake.keysym_to_keycode(PROBE_KEYSYM)

    def scenario() -> None:
        time.sleep(1.0)  # дать окну реально получить фокус у оконного менеджера

        print("удерживаю горячую клавишу и «печатаю» букву…")
        xtest.fake_input(fake, X.KeyPress, HOTKEY_KEYCODE)
        fake.sync()
        time.sleep(0.4)

        for _ in range(3):
            xtest.fake_input(fake, X.KeyPress, probe_keycode)
            fake.sync()
            time.sleep(0.05)
            xtest.fake_input(fake, X.KeyRelease, probe_keycode)
            fake.sync()
            time.sleep(0.15)

        xtest.fake_input(fake, X.KeyRelease, HOTKEY_KEYCODE)
        fake.sync()
        time.sleep(0.5)

        GLib.idle_add(finish)

    def finish() -> bool:
        probe.entry_text = entry.get_text()
        Gtk.main_quit()
        return False

    threading.Thread(target=scenario, daemon=True).start()
    Gtk.main()
    listener.stop()
    window.destroy()

    reached_window = len(probe.entry_text)
    print()
    print(f"перехватчик: нажатий {probe.presses}, отпусканий {probe.releases}")
    print(f"букв дошло до поля ввода:        {reached_window} из 3")
    print(f"чужих нажатий увидел перехватчик: {probe.foreign_seen_by_us}")
    print()
    if reached_window == 3 and probe.foreign_seen_by_us == 0:
        print("ВЫВОД: чужой ввод идёт мимо программы даже во время удержания.")
    elif probe.foreign_seen_by_us > 0:
        print("ВЫВОД: во время удержания чужие нажатия ПРИХОДЯТ программе.")
        print("       В боевом коде они отбрасываются, но написать об этом в README надо.")
    else:
        print("ВЫВОД: буквы не дошли ни до поля, ни до нас — окно не получило фокус,")
        print("       результат недостоверен, повторить.")
    print()
    print(f"автоповтор: удержание дало ровно {probe.presses} нажатие и "
          f"{probe.releases} отпускание" if probe.presses == probe.releases == 1
          else f"ВНИМАНИЕ: ожидалось 1/1, получено {probe.presses}/{probe.releases}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
