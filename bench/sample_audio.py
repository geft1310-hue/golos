"""Образец речи для проверок.

Файла `bench/sample.wav` в репозитории нет и не будет: это запись живого голоса,
и выкладывать её в открытый доступ нельзя. Поэтому каждый, кто прогоняет проверки,
записывает свой образец одной командой — она печатается ниже, если файла нет.

Здесь же лежит единственный на все проверки читатель wav: раньше он был скопирован
в трёх местах в трёх слегка разных вариантах.
"""

from __future__ import annotations

import sys
import wave
from pathlib import Path

import numpy as np

BENCH_DIR = Path(__file__).resolve().parent
SAMPLE_PATH = BENCH_DIR / "sample.wav"

HOWTO = (
    "Нет файла с образцом речи: {path}\n"
    "\n"
    "Он не входит в репозиторий — это чей-то живой голос. Запишите свой,\n"
    "10 секунд обычной речи (лучше с числами и сложными словами, чтобы было\n"
    "видно качество распознавания):\n"
    "\n"
    "    .venv/bin/python bench/benchmark.py bench/sample.wav --record 10\n"
)


def read_wav_mono_f32(path: Path) -> tuple[np.ndarray, int]:
    """Читает wav стандартным модулем wave — без зависимости от soundfile."""
    with wave.open(str(path), "rb") as w:
        n_channels = w.getnchannels()
        sample_width = w.getsampwidth()
        sample_rate = w.getframerate()
        frames = w.readframes(w.getnframes())

    if sample_width == 2:
        data = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 2**15
    elif sample_width == 4:
        data = np.frombuffer(frames, dtype="<i4").astype(np.float32) / 2**31
    elif sample_width == 1:
        data = (np.frombuffer(frames, dtype=np.uint8).astype(np.float32) - 128) / 128
    else:
        raise ValueError(f"неподдерживаемая разрядность: {sample_width} байт")

    if n_channels > 1:
        data = data.reshape(-1, n_channels).mean(axis=1)

    return np.ascontiguousarray(data), sample_rate


def require_sample(path: Path | None = None) -> tuple[np.ndarray, int]:
    """Отдаёт образец речи или завершает работу с понятным объяснением.

    Именно завершает, а не бросает исключение: у проверок нет вызывающего, который
    сумел бы обработать эту ситуацию лучше, а трассировка стека вместо инструкции
    никому не помогает.
    """
    path = path or SAMPLE_PATH
    if not path.is_file():
        print(HOWTO.format(path=path), file=sys.stderr)
        raise SystemExit(1)
    return read_wav_mono_f32(path)
