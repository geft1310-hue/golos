"""Проверка, что окружение вообще позволяет программе работать.

Смысл модуля ровно один: не дать Golos молча притвориться работающим.

Под Wayland запрос глобальной горячей клавиши через X11 внешне проходит успешно —
XWayland отдаёт свой собственный, изолированный ввод. Значок появляется, ошибок нет,
но нажатий программа не получает никогда. Человек видит запущенное приложение,
которое ничего не делает, и не понимает почему. Поэтому лучше сразу сказать правду
и объяснить, что делать.

Обойти проверку можно переменной GOLOS_IGNORE_SESSION_CHECK=1 — на случай нестандартной
сборки, где X11-ввод всё-таки доступен. Это осознанная лазейка для тех, кто понимает,
что делает.
"""

from __future__ import annotations

import os

X11, WAYLAND, HEADLESS, UNKNOWN = "x11", "wayland", "headless", "unknown"

_OVERRIDE_ENV = "GOLOS_IGNORE_SESSION_CHECK"

WAYLAND_MESSAGE = (
    "Golos работает только в сессии X11, а сейчас у вас Wayland.\n"
    "\n"
    "Дело не в недоработке: в Wayland ни одна программа не может перехватить "
    "клавишу на всю систему. Это сделано специально, ради безопасности, и "
    "обойти это нельзя.\n"
    "\n"
    "Что делать: выйдите из системы, на экране входа нажмите шестерёнку рядом "
    "с кнопкой «Войти» и выберите вариант со словом Xorg или X11 — например "
    "«Ubuntu на Xorg» или «Plasma (X11)». Дальше входите как обычно, пароль "
    "тот же. Вернуть Wayland можно там же в любой момент."
)

HEADLESS_MESSAGE = (
    "Нет графического экрана: не заданы ни DISPLAY, ни WAYLAND_DISPLAY.\n"
    "\n"
    "Так бывает, если запускать Golos по SSH или из системной службы. "
    "Программе нужен обычный рабочий стол — запустите её в своей сессии."
)


def session_kind() -> str:
    """Что за графическая сессия сейчас: x11, wayland, headless или unknown.

    Порядок проверок важен. XDG_SESSION_TYPE ставит менеджер входа, и это самый
    надёжный признак. WAYLAND_DISPLAY идёт вторым: в сессии Wayland переменная
    DISPLAY тоже обычно задана (её ставит XWayland), поэтому по одному DISPLAY
    отличить одно от другого невозможно.
    """
    declared = os.environ.get("XDG_SESSION_TYPE", "").strip().lower()
    if declared == "wayland":
        return WAYLAND
    if declared == "x11":
        return X11

    if os.environ.get("WAYLAND_DISPLAY"):
        return WAYLAND
    if os.environ.get("DISPLAY"):
        return X11
    return HEADLESS


def session_problem() -> str | None:
    """Текст проблемы для показа человеку — или None, если запускаться можно.

    Возвращает именно текст, а не показывает окно само: как сообщить (окно GTK
    или строка в терминале) решает вызывающий, ему виднее, поднялся ли уже GTK.
    """
    if os.environ.get(_OVERRIDE_ENV, "").strip() not in ("", "0", "false", "no"):
        return None

    kind = session_kind()
    if kind == WAYLAND:
        return WAYLAND_MESSAGE
    if kind == HEADLESS:
        return HEADLESS_MESSAGE
    return None
