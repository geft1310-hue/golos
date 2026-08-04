#!/usr/bin/env python3
"""Бенчмарк GigaAM v3 на целевой машине — гейт перед разработкой UI.

Машина: i7-2640M (Sandy Bridge, AVX без AVX2/VNNI), 2 ядра / 4 потока, swap исчерпан.
Заявленные onnx-asr цифры (RTFx 59) сняты на 9800X3D с AVX-512 и сюда не переносятся.

Матрица: {gigaam-v3-e2e-ctc, gigaam-v3-ctc} x {2, 4 потока}, только int8.
fp32 исключён осознанно: 885 МБ весов + активации не помещаются в доступную память.

Каждая конфигурация гоняется в отдельном подпроцессе — иначе ru_maxrss даёт
накопительный максимум по всему процессу, а не по конкретной конфигурации.
"""

from __future__ import annotations

import argparse
import json
import os
import resource
import statistics
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
from sample_audio import read_wav_mono_f32

MODELS = ["gigaam-v3-e2e-ctc", "gigaam-v3-ctc"]
THREAD_COUNTS = [2, 4]
QUANTIZATION = "int8"

MODELS_DIR = Path.home() / ".local/share/golos/models"

# Критерии приёмки из плана
MAX_RTF = 0.35
MAX_RSS_MB = 1024
MAX_COLD_LOAD_S = 3.0


def evict_page_cache(paths: list[Path]) -> None:
    """Выбрасывает файлы модели из page cache, чтобы померить действительно холодную
    загрузку. posix_fadvise(DONTNEED) работает без root, в отличие от drop_caches."""
    for p in paths:
        try:
            fd = os.open(p, os.O_RDONLY)
            try:
                os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
            finally:
                os.close(fd)
        except OSError:
            pass


def peak_rss_mb() -> float:
    # На Linux ru_maxrss в килобайтах
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024


def dir_size_mb(path: Path) -> float:
    if not path.exists():
        return 0.0
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) / 1024**2


def run_single(model_name: str, threads: int, wav_path: Path) -> dict:
    """Один прогон в собственном процессе. Печатает JSON в stdout."""
    import onnx_asr
    import onnxruntime as ort

    waveform, sample_rate = read_wav_mono_f32(wav_path)
    duration = len(waveform) / sample_rate

    # Веса должен был заранее положить tools/fetch_models.py.
    # Папку модели здесь НЕ создаём: onnx-asr считает существующую папку признаком
    # оффлайн-режима (resolver.py: `if self.local_dir.exists(): self.offline = True`),
    # и пустая заранее созданная папка приводит к ModelFileNotFoundError вместо загрузки.
    model_dir = MODELS_DIR / model_name
    if not (model_dir / "manifest.json").is_file():
        raise SystemExit(
            f"нет весов в {model_dir}; сначала: python tools/fetch_models.py {model_name}"
        )

    # --- первая загрузка в этом процессе (page cache как есть) ---
    size_before = dir_size_mb(model_dir)
    download_start = time.perf_counter()
    sess_options = ort.SessionOptions()
    sess_options.intra_op_num_threads = threads
    model = onnx_asr.load_model(
        model_name, str(model_dir), quantization=QUANTIZATION, sess_options=sess_options
    )
    first_load_s = time.perf_counter() - download_start
    downloaded_mb = dir_size_mb(model_dir) - size_before
    del model

    # --- холодная загрузка: сбрасываем page cache файлов модели ---
    model_files = [f for f in model_dir.rglob("*") if f.is_file()]
    evict_page_cache(model_files)

    sess_options = ort.SessionOptions()
    sess_options.intra_op_num_threads = threads
    t0 = time.perf_counter()
    model = onnx_asr.load_model(
        model_name, str(model_dir), quantization=QUANTIZATION, sess_options=sess_options
    )
    cold_load_s = time.perf_counter() - t0
    del model

    # --- тёплая загрузка: файлы уже в page cache ---
    sess_options = ort.SessionOptions()
    sess_options.intra_op_num_threads = threads
    t0 = time.perf_counter()
    model = onnx_asr.load_model(
        model_name, str(model_dir), quantization=QUANTIZATION, sess_options=sess_options
    )
    warm_load_s = time.perf_counter() - t0

    # --- прогрев секундой тишины (как в реальном приложении) ---
    t0 = time.perf_counter()
    model.recognize(np.zeros(sample_rate, dtype=np.float32), sample_rate=sample_rate)
    warmup_s = time.perf_counter() - t0

    # --- три прогона, берём медиану ---
    times, text = [], ""
    for _ in range(3):
        t0 = time.perf_counter()
        text = model.recognize(waveform, sample_rate=sample_rate)
        times.append(time.perf_counter() - t0)

    median_s = statistics.median(times)

    return {
        "model": model_name,
        "threads": threads,
        "audio_seconds": round(duration, 2),
        "downloaded_mb": round(downloaded_mb, 1),
        "first_load_s": round(first_load_s, 2),
        "cold_load_s": round(cold_load_s, 2),
        "warm_load_s": round(warm_load_s, 2),
        "warmup_s": round(warmup_s, 2),
        "infer_s": round(median_s, 2),
        "rtf": round(median_s / duration, 3),
        "peak_rss_mb": round(peak_rss_mb(), 1),
        "text": text if isinstance(text, str) else str(text),
    }


def record_sample(path: Path, seconds: int) -> None:
    print(f"Запись {seconds} секунд. Говорите после сигнала...", flush=True)
    time.sleep(0.5)
    print(">>> ГОВОРИТЕ <<<", flush=True)
    subprocess.run(
        ["timeout", str(seconds), "parecord", "--channels=1", "--rate=16000",
         "--file-format=wav", str(path)],
        check=False,
    )
    print(f"Готово: {path}", flush=True)


def verdict(row: dict) -> str:
    problems = []
    if row["rtf"] > MAX_RTF:
        problems.append(f"RTF {row['rtf']} > {MAX_RTF}")
    if row["peak_rss_mb"] > MAX_RSS_MB:
        problems.append(f"RSS {row['peak_rss_mb']}МБ > {MAX_RSS_MB}МБ")
    if row["cold_load_s"] > MAX_COLD_LOAD_S:
        problems.append(f"загрузка {row['cold_load_s']}с > {MAX_COLD_LOAD_S}с")
    return "ПРОШЁЛ" if not problems else "не прошёл: " + ", ".join(problems)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wav", nargs="?", type=Path, help="wav с речью (~10 с)")
    parser.add_argument("--record", type=int, metavar="SEC",
                        help="записать образец с микрофона указанной длины")
    parser.add_argument("--single", nargs=2, metavar=("MODEL", "THREADS"),
                        help="внутренний режим: один прогон, JSON в stdout")
    parser.add_argument("--models", nargs="+", metavar="NAME", default=MODELS,
                        help=f"какие модели мерить (по умолчанию {' '.join(MODELS)})")
    args = parser.parse_args()

    if args.single:
        result = run_single(args.single[0], int(args.single[1]), args.wav)
        print("@@JSON@@" + json.dumps(result, ensure_ascii=False))
        return 0

    wav_path = args.wav or Path("bench/sample.wav")
    if args.record:
        record_sample(wav_path, args.record)
    if not wav_path.exists():
        print(f"Нет файла {wav_path}. Запишите образец:\n"
              f"  python bench/benchmark.py {wav_path} --record 10", file=sys.stderr)
        return 1

    waveform, sample_rate = read_wav_mono_f32(wav_path)
    duration = len(waveform) / sample_rate
    rms = float(np.sqrt(np.mean(waveform**2)))
    peak = float(np.max(np.abs(waveform)))
    print(f"Образец: {wav_path} — {duration:.1f} с, {sample_rate} Гц, "
          f"RMS {rms:.4f}, пик {peak:.3f}")
    if rms < 0.001:
        print("  ВНИМАНИЕ: похоже на тишину, микрофон мог не записать", file=sys.stderr)
    if peak > 0.99:
        print("  ВНИМАНИЕ: клиппинг", file=sys.stderr)
    print()

    rows = []
    for model_name in args.models:
        for threads in THREAD_COUNTS:
            print(f"→ {model_name}, {threads} потока...", flush=True)
            proc = subprocess.run(
                [sys.executable, __file__, str(wav_path), "--single", model_name, str(threads)],
                capture_output=True, text=True, check=False,
            )
            line = next((ln for ln in proc.stdout.splitlines()
                         if ln.startswith("@@JSON@@")), None)
            if line is None:
                print(f"  ОШИБКА:\n{proc.stdout}\n{proc.stderr}", file=sys.stderr)
                continue
            row = json.loads(line[len("@@JSON@@"):])
            rows.append(row)
            print(f"  RTF {row['rtf']}  ожидание {row['infer_s']}с  "
                  f"RSS {row['peak_rss_mb']}МБ  холодная загрузка {row['cold_load_s']}с")

    if not rows:
        print("Ни одна конфигурация не отработала.", file=sys.stderr)
        return 1

    print("\n" + "=" * 100)
    print(f"{'модель':<22}{'пот.':>5}{'RTF':>8}{'ожид,с':>9}{'холод,с':>9}"
          f"{'тёпл,с':>8}{'RSS,МБ':>9}   вердикт")
    print("-" * 100)
    for row in sorted(rows, key=lambda r: r["rtf"]):
        print(f"{row['model']:<22}{row['threads']:>5}{row['rtf']:>8}{row['infer_s']:>9}"
              f"{row['cold_load_s']:>9}{row['warm_load_s']:>8}{row['peak_rss_mb']:>9}"
              f"   {verdict(row)}")
    print("=" * 100)
    print(f"\nКритерии: RTF ≤ {MAX_RTF}, RSS ≤ {MAX_RSS_MB} МБ, "
          f"холодная загрузка ≤ {MAX_COLD_LOAD_S} с\n")

    print("Распознанный текст (проверить пунктуацию и цифры глазами):")
    for row in rows:
        print(f"  [{row['model']}/{row['threads']}] {row['text']}")

    print(f"\nСкачано в {MODELS_DIR}: {dir_size_mb(MODELS_DIR):.1f} МБ "
          f"(ожидается ~225 МБ на модель, не 4.46 ГБ)")

    passed = [r for r in rows if verdict(r) == "ПРОШЁЛ"]
    if passed:
        best = min(passed, key=lambda r: r["rtf"])
        print(f"\nГЕЙТ ПРОЙДЕН. Лучшая: {best['model']}, {best['threads']} потока, "
              f"RTF {best['rtf']}")
        return 0

    print("\nГЕЙТ НЕ ПРОЙДЕН — включать лестницу отступления из плана.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
