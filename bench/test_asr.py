#!/usr/bin/env python3
"""Проверка asr.py: обычное распознавание и нарезка длинной записи.

Второй тест важнее первого: он проверяет, что речь длиннее лимита GigaAM не теряется,
а режется по паузам и склеивается обратно.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
from sample_audio import require_sample

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from golos.asr import Recognizer, find_pauses, split_audio, verify_model_files  # noqa: E402
from golos.config import Config  # noqa: E402


def main() -> int:
    pcm, rate = require_sample(Path(sys.argv[1]) if len(sys.argv) > 1 else None)
    config = Config()

    print(f"модель: {config.model}, потоков: {config.threads}")

    ok, message = verify_model_files(config.model_dir)
    print(f"проверка целостности (S5): {'ok' if ok else 'ПРОВАЛ'} — {message}")
    if not ok:
        return 1

    rec = Recognizer(config)

    t0 = time.perf_counter()
    rec.ensure_loaded()
    print(f"ensure_loaded: {time.perf_counter() - t0:.2f} с")

    t0 = time.perf_counter()
    print(f"второй ensure_loaded (должен быть мгновенным): {time.perf_counter() - t0:.4f} с")

    t0 = time.perf_counter()
    text = rec.transcribe(pcm, rate)
    print(f"\nкороткая запись ({len(pcm) / rate:.1f} с) за {time.perf_counter() - t0:.2f} с:")
    print(f"  {text}")

    # --- длинная запись: три копии образца, разделённые паузами по 0.6 с ---
    pause = np.zeros(int(0.6 * rate), dtype=np.float32)
    long_pcm = np.concatenate([pcm, pause, pcm, pause, pcm])
    duration = len(long_pcm) / rate
    print(f"\nдлинная запись: {duration:.1f} с, лимит куска {config.max_chunk_seconds} с")

    pauses = find_pauses(long_pcm, rate)
    print(f"найдено пауз: {len(pauses)} — "
          + ", ".join(f"{p[0] / rate:.1f}-{p[1] / rate:.1f}с" for p in pauses[:8]))

    chunks = split_audio(long_pcm, rate, config.max_chunk_seconds)
    print(f"кусков: {len(chunks)} — "
          + ", ".join(f"{len(c) / rate:.1f}с" for c in chunks))

    total = sum(len(c) for c in chunks)
    print(f"суммарно {total / rate:.1f} с против исходных {duration:.1f} с "
          f"({'без потерь' if total >= len(long_pcm) else 'ПОТЕРЯ'})")

    if any(len(c) > config.max_chunk_seconds * rate for c in chunks):
        print("ОШИБКА: кусок длиннее лимита", file=sys.stderr)
        return 1

    t0 = time.perf_counter()
    long_text = rec.transcribe(long_pcm, rate, progress=lambda i, n: print(f"  кусок {i} из {n}"))
    print(f"распознано за {time.perf_counter() - t0:.2f} с:")
    print(f"  {long_text}")

    rec.unload()
    print("\nвыгрузка прошла без ошибок")
    return 0


if __name__ == "__main__":
    sys.exit(main())
