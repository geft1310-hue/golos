#!/usr/bin/env python3
"""Проверка audio.py без сохранения на диск (S4).

Записывает несколько секунд, печатает ряд уровней и полосы эквалайзера —
чтобы увидеть, что звук идёт и что картинка будет реагировать на голос.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from golos.audio import AudioCapture, AudioError, list_input_devices  # noqa: E402


def bar(value: float, width: int = 30) -> str:
    filled = int(round(value * width))
    return "█" * filled + "·" * (width - filled)


def main() -> int:
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 4.0

    print("устройства ввода:")
    for index, name in list_input_devices():
        print(f"  [{index}] {name}")

    cap = AudioCapture()
    try:
        cap.start()
    except AudioError as exc:
        print(f"ОШИБКА: {exc}", file=sys.stderr)
        return 1

    print(f"\nчастота {cap.sample_rate} Гц. Говорите {seconds:.0f} секунд...\n")

    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        time.sleep(0.1)
        spec = cap.spectrum(12)
        print(f"\r{cap.elapsed:5.1f}с  ур.{cap.level:6.4f} |{bar(min(cap.level * 8, 1.0))}| "
              f"эк.{''.join(' ▁▂▃▄▅▆▇█'[int(v * 8)] for v in spec)}", end="", flush=True)

    pcm, rate = cap.stop()
    print("\n")

    if len(pcm) == 0:
        print("ОШИБКА: ничего не записалось", file=sys.stderr)
        return 1

    rms = float(np.sqrt(np.mean(pcm**2)))
    peak = float(np.abs(pcm).max())
    print(f"записано {len(pcm) / rate:.2f} с при {rate} Гц, RMS {rms:.4f}, пик {peak:.3f}")
    if rms < 0.001:
        print("  похоже на тишину — микрофон не пишет или выключен", file=sys.stderr)
    if peak > 0.99:
        print("  клиппинг — микрофон слишком громкий", file=sys.stderr)

    print(f"поток закрыт: {'да' if not cap.is_recording else 'НЕТ'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
