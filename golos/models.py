"""Каталог моделей распознавания: единый источник правды для GUI, ASR и загрузчика.

Имена gigaam-* — родные имена onnx-asr (резолвятся в istupakov/gigaam-v3-onnx).
Имена whisper-* — репозитории формата transformers.js (onnx-community/Xenova);
загружаются по явному repo_id, потому что в model_repos onnx-asr их нет.

quantization у whisper-large-v3 — «quantized»: у Xenova int8-файлы называются
*_quantized.onnx, а не *_int8.onnx. Паттерн onnx-asr подставляет квантование
через одиночный «?», так что оба варианта матчатся одинаково.
"""

from __future__ import annotations

MODELS: dict[str, dict] = {
    "gigaam-v3-e2e-rnnt": {
        "title": "Точнее, со знаками препинания (рекомендуется)",
        "size_mb": 217,
        "repo_id": "istupakov/gigaam-v3-onnx",
        "quantization": "int8",
        "max_chunk_seconds": 25,
    },
    "gigaam-v3-e2e-ctc": {
        "title": "Быстрее, тоже со знаками препинания",
        "size_mb": 215,
        "repo_id": "istupakov/gigaam-v3-onnx",
        "quantization": "int8",
        "max_chunk_seconds": 25,
    },
    "gigaam-v3-ctc": {
        "title": "Самая быстрая, без знаков препинания",
        "size_mb": 215,
        "repo_id": "istupakov/gigaam-v3-onnx",
        "quantization": "int8",
        "max_chunk_seconds": 25,
    },
    "whisper-large-v3": {
        "title": "Whisper large-v3 — максимальная точность, медленнее",
        "size_mb": 1561,
        "repo_id": "Xenova/whisper-large-v3",
        "quantization": "quantized",
        "max_chunk_seconds": 30,
        "whisper": True,
        "language": "ru",
    },
    "whisper-large-v3-turbo": {
        "title": "Whisper large-v3-turbo — быстрее, качество почти то же",
        "size_mb": 1085,
        "repo_id": "onnx-community/whisper-large-v3-turbo",
        "quantization": "int8",
        "max_chunk_seconds": 30,
        "whisper": True,
        "language": "ru",
    },
}

DEFAULT_MODEL = "gigaam-v3-e2e-rnnt"


def spec(model: str) -> dict:
    """Параметры модели по имени; незнакомое имя откатывается к умолчанию."""
    return MODELS.get(model, MODELS[DEFAULT_MODEL])
