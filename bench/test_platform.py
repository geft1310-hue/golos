#!/usr/bin/env python3
"""Определение сеанса: X11, Wayland или вообще без экрана.

Зачем отдельная проверка. Под Wayland перехват клавиши не выдаёт ошибку — он просто
не срабатывает ни разу. Человек видит запущенную программу, которая молчит в ответ
на нажатия, и пишет «не работает». Поэтому распознавание сеанса должно быть верным
в каждом случае, включая неочевидный: в сеансе Wayland переменная DISPLAY тоже обычно
задана (её ставит XWayland), и проверка по одной лишь DISPLAY приняла бы Wayland за X11.

Экран для этой проверки не нужен — она про переменные окружения. Годится для CI.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from golos.platform_check import HEADLESS, WAYLAND, X11, session_kind, session_problem

# (что в окружении, какой сеанс ожидаем, должна ли программа отказаться запускаться)
CASES = [
    ("обычный X11",
     {"XDG_SESSION_TYPE": "x11", "DISPLAY": ":0"}, X11, False),
    ("обычный Wayland",
     {"XDG_SESSION_TYPE": "wayland", "WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0"},
     WAYLAND, True),
    ("Wayland без XDG_SESSION_TYPE — ловится по WAYLAND_DISPLAY",
     {"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0"}, WAYLAND, True),
    ("только DISPLAY, ничего больше",
     {"DISPLAY": ":0"}, X11, False),
    ("SSH без экрана",
     {}, HEADLESS, True),
    ("Wayland, но человек попросил не мешать",
     {"XDG_SESSION_TYPE": "wayland", "WAYLAND_DISPLAY": "wayland-0",
      "GOLOS_IGNORE_SESSION_CHECK": "1"}, WAYLAND, False),
    ("обход выключен явным нулём — снова отказ",
     {"XDG_SESSION_TYPE": "wayland", "GOLOS_IGNORE_SESSION_CHECK": "0"}, WAYLAND, True),
]

TOUCHED = ("XDG_SESSION_TYPE", "WAYLAND_DISPLAY", "DISPLAY", "GOLOS_IGNORE_SESSION_CHECK")


def main() -> int:
    saved = {k: os.environ.get(k) for k in TOUCHED}
    failures = 0
    try:
        for name, env, want_kind, want_refuse in CASES:
            for k in TOUCHED:
                os.environ.pop(k, None)
            os.environ.update(env)

            kind = session_kind()
            refuses = session_problem() is not None
            ok = kind == want_kind and refuses == want_refuse
            failures += not ok
            mark = "✓" if ok else "✗"
            verdict = "отказ с объяснением" if refuses else "запускается"
            print(f"  {mark} {name}\n      сеанс {kind}, {verdict}")
            if not ok:
                print(f"      ОЖИДАЛОСЬ: сеанс {want_kind}, "
                      f"{'отказ' if want_refuse else 'запуск'}")
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    print()
    if failures:
        print(f"ВЫВОД: не сошлось случаев: {failures} из {len(CASES)}")
        return 1
    print(f"ВЫВОД: все {len(CASES)} случаев распознаются верно")
    return 0


if __name__ == "__main__":
    sys.exit(main())
