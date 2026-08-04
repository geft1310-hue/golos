#!/usr/bin/env python3
"""Замер памяти: в покое → модель загружена → модель выгружена.

Проверяет открытый риск из плана: ONNX Runtime не всегда возвращает освобождённую
память операционной системе. Если после unload() RSS не падает, ленивая выгрузка
бессмысленна — на машине с исчерпанным swap это принципиально.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from golos.asr import Recognizer  # noqa: E402
from golos.config import Config  # noqa: E402


def rss_mb() -> float:
    for line in Path("/proc/self/status").read_text().splitlines():
        if line.startswith("VmRSS:"):
            return int(line.split()[1]) / 1024
    return 0.0


def main() -> int:
    config = Config.load()
    recognizer = Recognizer(config)

    base = rss_mb()
    print(f"1. интерпретатор без модели     {base:7.1f} МБ")

    t0 = time.monotonic()
    recognizer.ensure_loaded()
    load_s = time.monotonic() - t0
    loaded = rss_mb()
    print(f"2. модель загружена ({load_s:.1f} с)      {loaded:7.1f} МБ   (+{loaded - base:.1f})")

    # Реальное распознавание: арены ORT выделяются именно здесь, а не при загрузке.
    speech = np.zeros(16000 * 5, dtype=np.float32)
    recognizer.transcribe(speech, 16000)
    after_work = rss_mb()
    print(f"3. после распознавания          {after_work:7.1f} МБ   (+{after_work - loaded:.1f})")

    recognizer.unload()
    time.sleep(0.5)
    unloaded = rss_mb()
    print(f"4. модель выгружена             {unloaded:7.1f} МБ   ({unloaded - after_work:+.1f})")

    returned = after_work - unloaded
    held = unloaded - base
    print()
    print(f"вернули системе: {returned:.1f} МБ из {after_work - base:.1f} МБ")
    print(f"осталось сверх пустого процесса: {held:.1f} МБ")
    print("ВЫВОД:", "выгрузка работает" if held < 150 else "ПАМЯТЬ НЕ ВЕРНУЛАСЬ — схема не работает")
    return 0


if __name__ == "__main__":
    sys.exit(main())
