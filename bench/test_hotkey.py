#!/usr/bin/env python3
"""Проверка перехвата горячей клавиши — и эксперимент по безопасности (S1).

Проверяем две вещи.

1. Что удержание даёт РОВНО одно «нажато» и одно «отпущено», а не гирлянду от
   автоповтора. Это то, обо что обычно ломается push-to-talk.

2. Сколько ЧУЖИХ нажатий доходит до нас, пока клавиша удерживается. X при активном
   захвате может доставлять нам и посторонние клавиши — надо знать точно, а не
   предполагать. Считаем только количество: сами коды клавиш здесь не печатаются и
   никуда не пишутся сознательно, иначе тест безопасности сам стал бы утечкой.
"""

from __future__ import annotations

import select
import sys
import time
from pathlib import Path

from Xlib import X
from Xlib import display as xdisplay
from Xlib import error as xerror

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from golos.config import Config
from golos.hotkey import keycode_label


def main() -> int:
    keycode = int(sys.argv[1]) if len(sys.argv) > 1 else Config().hotkey_keycode
    print(f"Слежу за клавишей: код {keycode} ({keycode_label(keycode)})")

    disp = xdisplay.Display()
    root = disp.screen().root

    catcher = xerror.CatchError(xerror.BadAccess)
    root.grab_key(keycode, X.AnyModifier, True, X.GrabModeAsync, X.GrabModeAsync, onerror=catcher)
    disp.sync()
    if catcher.get_error():
        print("ОШИБКА: клавишу уже занимает другая программа", file=sys.stderr)
        return 1

    print()
    print("Что делать:")
    print("  1. Откройте текстовый редактор и поставьте курсор в него.")
    print("  2. Зажмите клавишу на 3 секунды и, НЕ ОТПУСКАЯ её, наберите несколько букв.")
    print("  3. Отпустите. Посмотрите, появились ли буквы в редакторе.")
    print("  4. Повторите пару раз, потом нажмите Ctrl+C здесь.")
    print()

    presses = releases = foreign = 0
    held_since: float | None = None
    fd = disp.fileno()

    try:
        while True:
            select.select([fd], [], [], 0.2)
            while disp.pending_events():
                event = disp.next_event()
                if event.type not in (X.KeyPress, X.KeyRelease):
                    continue

                if event.detail != keycode:
                    # Чужая клавиша. Считаем факт, код НЕ сохраняем и НЕ печатаем.
                    if event.type == X.KeyPress:
                        foreign += 1
                        print(f"    ... до нас дошло чужое нажатие (всего {foreign})")
                    continue

                if event.type == X.KeyPress:
                    if held_since is not None:
                        print("    ПОВТОРНОЕ нажатие без отпускания — похоже на автоповтор")
                        continue
                    presses += 1
                    held_since = time.monotonic()
                    print(f"[{presses}] нажато")
                else:
                    if held_since is None:
                        print("    отпускание без нажатия — автоповтор")
                        continue
                    releases += 1
                    print(f"[{releases}] отпущено, удержание {time.monotonic() - held_since:.2f} с")
                    held_since = None
    except KeyboardInterrupt:
        print()
    finally:
        root.ungrab_key(keycode, X.AnyModifier)
        disp.sync()
        disp.close()

    print("=" * 60)
    print(f"нажатий: {presses}, отпусканий: {releases}")
    if presses == releases and presses > 0:
        print("Автоповтор не мешает: на каждое удержание ровно одна пара событий.")
    elif presses == 0:
        print("Событий не было — клавиша не та или захват не сработал.")
    else:
        print("РАСХОЖДЕНИЕ: нажатий и отпусканий разное количество.")

    print(f"чужих нажатий дошло до нас: {foreign}")
    if foreign == 0:
        print("Посторонний ввод до программы не доходит даже во время удержания.")
    else:
        print("Во время удержания посторонние нажатия ДОХОДЯТ — это надо описать в README.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
