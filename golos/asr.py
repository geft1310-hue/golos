"""Распознавание речи: обёртка над onnx-asr с ленивой загрузкой и нарезкой.

Зачем обёртка. Во-первых, чтобы вся остальная программа не знала про onnx-asr и
модель можно было поменять в настройках. Во-вторых — из-за памяти: на целевой машине
swap исчерпан, поэтому модель (~215 МБ весов, ~600-800 МБ RSS) не живёт в памяти
постоянно. Она поднимается по нажатию горячей клавиши, параллельно с записью речи,
и выгружается после простоя.

Нарезка. GigaAM обучалась на фрагментах до 25 с и на длинных записях деградирует.
Молча обрезать речь нельзя — это потеря сказанного, поэтому длинная запись режется
по паузам и склеивается обратно.
"""

from __future__ import annotations

import ctypes
import gc
import hashlib
import json
import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path

import numpy as np

from golos.config import Config

log = logging.getLogger(__name__)

ProgressCb = Callable[[int, int], None]

# --- параметры поиска пауз ---
_FRAME_MS = 20
_MIN_PAUSE_MS = 300
_HARD_CUT_OVERLAP_S = 0.3
# Паузу ищем во второй половине допустимого окна: берём самую позднюю, чтобы кусков
# получилось меньше — каждый кусок это отдельный проход модели, а ядер всего два.
_SEARCH_FROM = 0.5


class ModelFilesMissingError(RuntimeError):
    """Весов нет на диске — нужна первая загрузка."""


def find_pauses(
    pcm: np.ndarray, sample_rate: int, min_pause_ms: int = _MIN_PAUSE_MS
) -> list[tuple[int, int]]:
    """Возвращает паузы как пары (начало, конец) в отсчётах.

    Порог адаптивный: считается от фактического шумового пола этой записи, а не
    задан константой. Иначе тихий микрофон даёт «сплошную речь», а шумная комната —
    «сплошную тишину».
    """
    frame = max(1, sample_rate * _FRAME_MS // 1000)
    n_frames = len(pcm) // frame
    if n_frames < 2:
        return []

    frames = pcm[: n_frames * frame].reshape(n_frames, frame)
    rms = np.sqrt((frames.astype(np.float32) ** 2).mean(axis=1))

    noise_floor = float(np.percentile(rms, 10))
    loud = float(np.percentile(rms, 95))
    threshold = max(noise_floor * 3.0, noise_floor + (loud - noise_floor) * 0.15, 1e-5)

    silent = rms < threshold
    min_frames = max(1, min_pause_ms // _FRAME_MS)

    pauses: list[tuple[int, int]] = []
    start: int | None = None
    for i, is_silent in enumerate([*silent.tolist(), False]):
        if is_silent and start is None:
            start = i
        elif not is_silent and start is not None:
            if i - start >= min_frames:
                pauses.append((start * frame, i * frame))
            start = None
    return pauses


def split_audio(pcm: np.ndarray, sample_rate: int, max_seconds: float) -> list[np.ndarray]:
    """Режет запись на куски не длиннее max_seconds, по возможности — по паузам."""
    max_len = int(max_seconds * sample_rate)
    if len(pcm) <= max_len:
        return [pcm]

    pauses = find_pauses(pcm, sample_rate)
    overlap = int(_HARD_CUT_OVERLAP_S * sample_rate)

    chunks: list[np.ndarray] = []
    pos = 0
    while len(pcm) - pos > max_len:
        window_start = pos + int(max_len * _SEARCH_FROM)
        window_end = pos + max_len
        # Самая поздняя пауза внутри окна: режем как можно дальше, но не за пределом.
        candidates = [p for p in pauses if window_start <= (p[0] + p[1]) // 2 <= window_end]
        if candidates:
            cut = sum(candidates[-1]) // 2
            chunks.append(pcm[pos:cut])
            pos = cut
        else:
            # Сплошная речь без пауз. Режем жёстко, но с перехлёстом, чтобы слово на
            # стыке не пропало целиком — оно попадёт в оба куска хотя бы частично.
            log.warning("нет пауз в окне — жёсткий рез, слово на стыке может пострадать")
            chunks.append(pcm[pos : pos + max_len])
            pos = pos + max_len - overlap

    tail = pcm[pos:]
    if len(tail) > sample_rate // 10:  # огрызки короче 0.1 с не распознаём
        chunks.append(tail)
    return chunks


def verify_model_files(model_dir: Path) -> tuple[bool, str]:
    """S5: сверяет sha256 файлов модели с манифестом, записанным при скачивании.

    Вызывается один раз при старте в фоне, а НЕ при каждой загрузке модели: подсчёт
    хеша 215 МБ на этом процессоре занимает секунды и в горячем пути недопустим.
    """
    manifest_path = model_dir / "manifest.json"
    if not manifest_path.is_file():
        return False, "нет manifest.json — модель скачана не нами"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        expected = manifest["files"]
    except (OSError, ValueError, KeyError) as exc:
        return False, f"манифест не читается: {exc}"

    for rel, want in expected.items():
        path = model_dir / rel
        if not path.is_file():
            return False, f"файл пропал: {rel}"
        h = hashlib.sha256()
        with path.open("rb") as f:
            for block in iter(lambda: f.read(1024 * 1024), b""):
                h.update(block)
        if h.hexdigest() != want:
            return False, f"изменился файл: {rel}"
    return True, f"ок, коммит {manifest.get('revision', '?')[:12]}"


class Recognizer:
    """Ленивая обёртка над моделью. Потокобезопасна."""

    def __init__(self, config: Config):
        self._config = config
        self._model = None
        self._lock = threading.Lock()
        self._last_used = 0.0

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    def apply_config(self, config: Config) -> None:
        """Принимает новые настройки. Если сменилась модель или её параметры —
        выгружает текущую: новая поднимется при следующем нажатии клавиши."""
        old = self._config
        self._config = config
        changed = (
            config.model != old.model
            or config.quantization != old.quantization
            or config.threads != old.threads
        )
        if changed:
            self.unload()

    def ensure_loaded(self) -> None:
        """Идемпотентно поднимает модель. Второй вызов во время загрузки просто ждёт
        на том же локе и не запускает загрузку повторно."""
        with self._lock:
            if self._model is not None:
                self._last_used = time.monotonic()
                return

            cfg = self._config
            model_dir = cfg.model_dir
            if not (model_dir / "manifest.json").is_file():
                raise ModelFilesMissingError(str(model_dir))

            from golos.models import spec  # noqa: PLC0415

            model_spec = spec(cfg.model)

            import onnx_asr  # noqa: PLC0415  — тяжёлый импорт, только когда реально нужен
            import onnxruntime as ort  # noqa: PLC0415

            t0 = time.perf_counter()
            sess_options = ort.SessionOptions()
            sess_options.intra_op_num_threads = cfg.threads
            sess_options.inter_op_num_threads = 1
            # Для whisper передаём repo_id: короткого имени нет в model_repos onnx-asr.
            # Для gigaam работает родное имя; квантование берём из каталога моделей.
            model = onnx_asr.load_model(
                model_spec["repo_id"] if model_spec.get("whisper") else cfg.model,
                str(model_dir),
                quantization=model_spec.get("quantization", cfg.quantization) or None,
                sess_options=sess_options,
            )
            load_s = time.perf_counter() - t0

            # Прогрев секундой тишины: без него первое настоящее распознавание заметно
            # медленнее — onnxruntime доallocates свои арены именно на первом прогоне.
            t0 = time.perf_counter()
            model.recognize(np.zeros(16_000, dtype=np.float32), sample_rate=16_000)
            warm_s = time.perf_counter() - t0

            self._model = model
            self._last_used = time.monotonic()
            log.info("модель загружена за %.2f с, прогрев %.2f с", load_s, warm_s)

    def unload(self) -> None:
        with self._lock:
            if self._model is None:
                return
            self._model = None
            gc.collect()
            _malloc_trim()
            log.info("модель выгружена")

    def maybe_unload(self) -> bool:
        """Выгружает модель, если она простаивала дольше idle_unload_seconds."""
        timeout = self._config.idle_unload_seconds
        if timeout <= 0 or self._model is None:
            return False
        if time.monotonic() - self._last_used < timeout:
            return False
        self.unload()
        return True

    def transcribe(
        self,
        pcm: np.ndarray,
        sample_rate: int,
        progress: ProgressCb | None = None,
    ) -> str:
        """Распознаёт запись целиком, при необходимости нарезая её на куски.

        pcm — float32 в родной частоте устройства; ресемплинг и сведение каналов
        делает сам onnx-asr, поэтому промежуточных wav-файлов нет нигде.
        """
        self.ensure_loaded()
        from golos.models import spec  # noqa: PLC0415

        model_spec = spec(self._config.model)
        # Предел окна у каждой модели свой (GigaAM ~25 с, Whisper ~30 с).
        max_chunk = min(self._config.max_chunk_seconds, model_spec["max_chunk_seconds"])
        chunks = split_audio(pcm, sample_rate, max_chunk)
        total = len(chunks)

        parts: list[str] = []
        for i, chunk in enumerate(chunks, start=1):
            if progress is not None:
                progress(i, total)
            # Последовательно, не параллельно: 2 физических ядра и исчерпанный swap —
            # параллельные проходы здесь дадут не ускорение, а своп-трэшинг.
            with self._lock:
                model = self._model
                if model is None:  # выгрузили между кусками — поднимаем заново
                    break
                # Автодетект языка Whisper на коротких фразах ошибается — фиксируем.
                language = model_spec.get("language")
                if language:
                    text = model.recognize(
                        chunk, sample_rate=sample_rate, channel="mean", language=language
                    )
                else:
                    text = model.recognize(chunk, sample_rate=sample_rate, channel="mean")
            if text:
                parts.append(text.strip())

        self._last_used = time.monotonic()
        return " ".join(p for p in parts if p)


def _malloc_trim() -> None:
    """onnxruntime не всегда возвращает освобождённую память операционной системе:
    glibc держит её в своей куче. malloc_trim(0) просит glibc отдать её обратно.
    Не критично, если не сработает, — поэтому всё в try."""
    try:
        libc = ctypes.CDLL("libc.so.6")
        libc.malloc_trim(0)
    except (OSError, AttributeError):
        pass
