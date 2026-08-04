"""Захват звука с микрофона на время удержания горячей клавиши.

Два принципа.

Приватность (S4): поток открывается только на время записи и закрывается сразу после.
Микрофон не держится постоянно — ни аппаратно, ни в списке «кто вас слушает».
Записанное никогда не пишется на диск, а буфер обнуляется после передачи в распознавание.

Реальное время: колбэк PortAudio нельзя задерживать, иначе в записи будут щелчки и
пропуски. Поэтому в колбэке только копирование блока и одно вычисление RMS —
ни обращений к GTK, ни блокировок, ни аллокаций сверх необходимого. Всё остальное
(спектр для эквалайзера) считается в UI-таймере.
"""

from __future__ import annotations

import logging
import threading
import time

import numpy as np

log = logging.getLogger(__name__)

# Частоты пробуем в этом порядке. Все они поддерживаются onnx-asr, который сам
# приведёт запись к нужным модели 16 кГц.
_RATE_CANDIDATES = (48_000, 44_100, 16_000)
_BLOCK = 1024

# Подъём высоких полос: в человеческом голосе энергии выше 2 кГц заметно меньше, чем
# на низах, и без него правая половина эквалайзера стояла бы почти пустой.
#
# Именно множитель, а не прибавка в децибелах. Прибавка поднимала бы и тишину тоже:
# она не зависит от сигнала, поэтому переживала бы вычитание шума и правые полосы
# в тишине стояли бы почти полными (замерено: средняя высота 0.52 вместо 0.09).
# Множитель усиливает то, что есть, и не создаёт того, чего нет: ноль остаётся нулём.
_TILT_PER_BAND = 0.06

# Шкала эквалайзера подстраивается под микрофон и комнату сама (см. _rescale).
# Жёстко зашитые границы тут были бы ошибкой: на замерах этой машины тишина комнаты
# держалась на -60..-80 дБ, а речь на -40..-49 дБ, но у встроенного микрофона ноутбука,
# у петлички и у USB-конденсатора эти числа различаются на десятки децибел. С чужими
# числами полосы либо всегда пустые, либо всегда полные — и человек решает,
# что микрофон не работает.
#
# Пол считается для каждой полосы отдельно, а не общий на весь спектр. Так и должно
# быть: шум не бывает ровным. Гул вентилятора сидит на низах, наводка сети — на 50 Гц,
# шипение усилителя — на верхах. Общий пол задрал бы те полосы, где шума больше,
# и в тишине они стояли бы полными.
_MIN_SPAN_DB = 18.0  # минимальная высота шкалы: не даёт растянуть шум на весь экран
_SEED_FRAMES = 10  # первые полсекунды после нажатия человек ещё не говорит — слушаем шум
_FLOOR_FALL = 0.30  # нашли тишину тише прежней — сразу принимаем
_FLOOR_CREEP = 0.02  # всё ещё тихо, но чуть громче — уточняем неспешно
_SPAN_RISE, _SPAN_FALL = 0.50, 0.02  # громкое слово принимаем сразу, забываем медленно

# Сглаживание по времени. Нужно не для красоты, а из-за математики: в нижних полосах
# усредняется буквально один-два столбика БПФ, а отдельный столбик шума — величина
# случайная с разбросом больше 20 дБ. Без сглаживания полосы дёргались до потолка
# в полной тишине (замерено на живом шуме комнаты: полная полоса в 39 % кадров).
# Усредняем мощность, а не децибелы: складывать логарифмы физически неверно.
_POWER_SMOOTH = 0.3

# Мёртвая зона: пока звук поднялся над собственным шумом микрофона меньше чем на
# столько децибел, не рисуем ничего. Комнатный шум колеблется сам по себе, и без
# запаса эти колебания выглядели бы как «вас слышно». Речь идёт на 25–30 дБ выше
# шума, так что запас в 9 дБ её не срезает.
# Замерено (шум этой комнаты + запись речи, одна дорожка):
#   без зоны        тишина 0.199, полные полосы в 24 % кадров
#   зона 9 дБ       тишина 0.013, полных полос нет вообще, речь видна в 69 % кадров
_GATE_DB = 9.0

_hann_cache: dict[int, np.ndarray] = {}


def _hann(size: int) -> np.ndarray:
    """Окно Ханна с кэшем: спектр считается 20 раз в секунду, пересоздавать незачем."""
    window = _hann_cache.get(size)
    if window is None:
        window = np.hanning(size).astype(np.float32)
        _hann_cache[size] = window
    return window


class AudioError(RuntimeError):
    """Микрофон недоступен: занят, отключён или запрещён."""


class AudioCapture:
    """Одна запись: start() → говорим → stop() отдаёт float32-моно."""

    def __init__(self, device: str | int | None = None):
        self._device = device
        self._stream = None
        self._blocks: list[np.ndarray] = []
        self._last_block: np.ndarray | None = None
        self._level = 0.0
        self._started_at = 0.0
        self._overflows = 0
        self._lock = threading.Lock()
        self.sample_rate = 0
        # Калибровка эквалайзера под этот микрофон. Между записями не сбрасывается:
        # микрофон и комната те же, и нащупывать шкалу заново каждый раз незачем.
        self._noise_db: np.ndarray | None = None  # уровень шума по каждой полосе
        self._span_db = _MIN_SPAN_DB  # насколько речь поднимается над шумом
        self._power: np.ndarray | None = None  # сглаженная по времени мощность полос
        self._frames = 0

    # ------------------------------------------------------------------ запись

    def start(self) -> None:
        import sounddevice as sd  # noqa: PLC0415

        self.stop_stream()
        with self._lock:
            self._blocks = []
        self._last_block = None
        self._level = 0.0
        self._overflows = 0
        # Заново прислушиваемся к тишине: комната могла стать тише. Сам уровень шума
        # при этом не забываем — повторная калибровка умеет только опускать пол.
        self._frames = 0
        self._power = None

        errors = []
        for rate in self._candidate_rates():
            try:
                stream = sd.InputStream(
                    samplerate=rate,
                    channels=1,
                    dtype="float32",
                    blocksize=_BLOCK,
                    device=self._device,
                    callback=self._callback,
                )
                stream.start()
            except Exception as exc:  # noqa: BLE001 — sounddevice бросает разное
                errors.append(f"{rate} Гц: {exc}")
                continue
            self._stream = stream
            self.sample_rate = int(rate)
            self._started_at = time.monotonic()
            log.info("запись начата: %d Гц, устройство %s", rate, self._device or "по умолчанию")
            return

        raise AudioError("не удалось открыть микрофон — " + "; ".join(errors))

    def _candidate_rates(self) -> list[int]:
        """Сначала родная частота устройства, потом запасные варианты."""
        import sounddevice as sd  # noqa: PLC0415

        rates = list(_RATE_CANDIDATES)
        try:
            native = int(sd.query_devices(self._device, "input")["default_samplerate"])
        except Exception:  # noqa: BLE001
            return rates
        if native in rates:
            rates.remove(native)
        return [native, *rates]

    def _callback(self, indata, frames, time_info, status) -> None:  # noqa: ANN001, ARG002
        """Колбэк PortAudio. Выполняется в realtime-потоке — держать максимально коротким."""
        if status:
            # input overflow — часть звука потеряна; не роняем запись, но считаем
            self._overflows += 1
        block = indata[:, 0].copy()
        self._blocks.append(block)  # list.append атомарен, лок не нужен
        self._last_block = block
        self._level = float(np.sqrt(np.mean(block * block)))

    def stop_stream(self) -> None:
        """Закрывает поток, не трогая накопленные данные."""
        stream, self._stream = self._stream, None
        if stream is None:
            return
        try:
            stream.stop()
            stream.close()
        except Exception as exc:  # noqa: BLE001
            log.warning("ошибка при закрытии потока: %s", exc)

    def stop(self) -> tuple[np.ndarray, int]:
        """Останавливает запись и отдаёт накопленное как float32-моно."""
        self.stop_stream()
        with self._lock:
            blocks, self._blocks = self._blocks, []
        self._last_block = None
        self._level = 0.0

        if self._overflows:
            log.warning("переполнений буфера ввода: %d", self._overflows)
        if not blocks:
            return np.zeros(0, dtype=np.float32), self.sample_rate or 16_000

        pcm = np.concatenate(blocks)
        # S4: обнуляем исходные блоки, чтобы речь не осталась в освобождённой памяти
        for block in blocks:
            block.fill(0)
        return pcm, self.sample_rate

    def cancel(self) -> None:
        """Отмена без распознавания: всё стереть (короткое случайное нажатие)."""
        pcm, _ = self.stop()
        pcm.fill(0)

    # ------------------------------------------------------ данные для картинки

    @property
    def is_recording(self) -> bool:
        return self._stream is not None

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self._started_at if self._stream is not None else 0.0

    @property
    def level(self) -> float:
        """Громкость последнего блока, 0..1. Читается из UI-таймера."""
        return self._level

    def spectrum(self, bars: int) -> np.ndarray:
        """Полосы эквалайзера из последнего блока, каждая 0..1.

        Считается здесь, а не в колбэке: FFT в realtime-потоке — верный способ
        получить щелчки в записи.
        """
        block = self._last_block
        if block is None or len(block) < 64:
            return np.zeros(bars, dtype=np.float32)

        window = _hann(len(block))
        # Нормировка на длину блока и на усиление окна обязательна: без неё амплитуды
        # растут пропорционально размеру блока, шкала уезжает, и полосы стоят
        # на максимуме даже в полной тишине.
        windowed = block * window
        magnitude = np.abs(np.fft.rfft(windowed)) * 2.0 / window.sum()

        # Полосы по логарифмической шкале: человек слышит именно так, и иначе почти
        # вся картинка приходится на верх спектра, где голоса нет.
        nyquist = (self.sample_rate or 16_000) / 2
        edges = np.geomspace(80, min(6000, nyquist * 0.95), bars + 1)
        bin_hz = nyquist / len(magnitude)

        out = np.zeros(bars, dtype=np.float32)
        for i in range(bars):
            lo = int(edges[i] / bin_hz)
            hi = max(lo + 1, int(edges[i + 1] / bin_hz))
            out[i] = magnitude[lo:hi].mean() if lo < len(magnitude) else 0.0

        # В децибелы: на линейной шкале голос выглядит как «почти всегда ноль».
        # Показываем не громкость саму по себе, а насколько она поднялась над
        # собственным шумом этого микрофона — только это и значит «вас слышно».
        db = self._smooth(out)
        # Мёртвая зона: всё, что не поднялось над шумом на _GATE_DB, считаем тишиной.
        excess = np.maximum(db - self._track_noise(db) - _GATE_DB, 0.0)
        height = excess / self._track_span(excess) * (1 + np.arange(bars) * _TILT_PER_BAND)
        return np.clip(height, 0.0, 1.0).astype(np.float32)

    def _smooth(self, magnitude: np.ndarray) -> np.ndarray:
        """Мощность полос, усреднённая по времени, в децибелах.

        Усредняем именно мощность (квадрат амплитуды), а не готовые децибелы:
        логарифмы складывать нельзя, среднее от них — не то же самое, что уровень
        среднего сигнала, и на пиках речи получалось бы занижение.
        """
        power = magnitude.astype(np.float32) ** 2
        if self._power is None or len(self._power) != len(power):
            self._power = power
        else:
            self._power += (power - self._power) * _POWER_SMOOTH
        return 10 * np.log10(self._power + 1e-24)

    def _track_noise(self, db: np.ndarray) -> np.ndarray:
        """Уровень собственного шума отдельно для каждой полосы.

        Первые полсекунды после нажатия клавиши человек ещё не говорит — берём по ним
        минимум и получаем портрет тишины этой комнаты. Минимум, а не среднее, и минимум
        со старым значением: так повторная калибровка может только опустить пол, и
        запись, начатая сразу с речи, не испортит настройку.

        Дальше пол опускается сразу, если стало тише, и подтягивается вверх только по
        тихим полосам. Полосу, в которой сейчас голос, трогать нельзя: пол уехал бы
        за голосом, и полосы схлопнулись бы посреди фразы.
        """
        if self._noise_db is None or len(self._noise_db) != len(db):
            self._noise_db = db.copy()
            self._frames = 0

        self._frames += 1
        if self._frames <= _SEED_FRAMES:
            np.minimum(self._noise_db, db, out=self._noise_db)
            return self._noise_db

        delta = db - self._noise_db
        rate = np.where(delta < 0, _FLOOR_FALL, _FLOOR_CREEP)
        self._noise_db += np.where(delta < _MIN_SPAN_DB, delta * rate, 0.0)
        return self._noise_db

    def _track_span(self, excess: np.ndarray) -> float:
        """Высота шкалы: насколько сильный сигнал считать полной полосой.

        Растёт быстро — громкое слово должно поместиться сразу, а не срезаться.
        Опускается медленно, иначе шкала «дышала» бы в паузах между словами.
        Ниже _MIN_SPAN_DB не опускается: в полной тишине это единственное, что не даёт
        растянуть остаточный шум во весь экран.
        """
        peak = float(excess.max())
        rate = _SPAN_RISE if peak > self._span_db else _SPAN_FALL
        self._span_db += (peak - self._span_db) * rate
        return max(self._span_db, _MIN_SPAN_DB)


def list_input_devices() -> list[tuple[int, str]]:
    """Микрофоны для выпадающего списка в настройках."""
    import sounddevice as sd  # noqa: PLC0415

    try:
        devices = sd.query_devices()
    except Exception as exc:  # noqa: BLE001
        log.warning("не удалось получить список устройств: %s", exc)
        return []
    return [
        (i, d["name"])
        for i, d in enumerate(devices)
        if d["max_input_channels"] > 0
    ]
