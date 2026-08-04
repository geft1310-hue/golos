#!/usr/bin/env python3
"""Визуальная проверка всплывающего окна: прогоняет все состояния подряд.

Микрофон включается по-настоящему, так что эквалайзер должен реагировать на голос.
Заодно проверяется главное свойство окна — что оно НЕ забирает фокус: во время
показа можно продолжать печатать в другом окне.
"""

from __future__ import annotations

import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from golos.audio import AudioCapture, AudioError  # noqa: E402
from golos.ui.overlay import Overlay  # noqa: E402


def main() -> int:
    overlay = Overlay()
    capture = AudioCapture()

    try:
        capture.start()
    except AudioError as exc:
        print(f"микрофон недоступен: {exc}", file=sys.stderr)
        return 1

    print("1/4  Запись — говорите, полосы должны двигаться (6 с)")
    print("     Попробуйте в это же время попечатать в другом окне:")
    print("     буквы должны идти туда, а не пропадать.")
    overlay.show_recording(capture)

    def transcribing() -> None:
        print("2/4  Распознаю… (справа индикатор кусков 2/3)")
        capture.stop()
        overlay.show_transcribing()
        overlay.set_progress(2, 3)

    def done() -> None:
        print("3/4  Готово — с началом текста")
        overlay.show_done("Привет, это проверка распознавания речи. Сколько сейчас времени?")

    def error() -> None:
        print("4/4  Ошибка")
        overlay.show_error("Микрофон недоступен")

    def finish() -> None:
        overlay.hide()
        Gtk.main_quit()

    # (когда от старта, что делать)
    for delay_ms, action in [
        (6000, transcribing),
        (8500, done),
        (11500, error),
        (16000, finish),
    ]:
        GLib.timeout_add(delay_ms, lambda a=action: (a(), False)[1])

    Gtk.main()
    capture.stop_stream()
    print("\nокно закрылось без ошибок")
    return 0


if __name__ == "__main__":
    sys.exit(main())
