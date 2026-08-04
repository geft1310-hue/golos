"""Настройки Golos: dataclass + JSON в ~/.config/golos/config.json.

Файл и каталог создаются с правами 0700/0600 (S7): в конфиге лежит в том числе
keycode горячей клавиши, и вообще посторонним тут делать нечего.
"""

from __future__ import annotations

import contextlib
import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path

CONFIG_DIR = Path.home() / ".config/golos"
CONFIG_PATH = CONFIG_DIR / "config.json"
DATA_DIR = Path.home() / ".local/share/golos"
MODELS_DIR = DATA_DIR / "models"

# Клавиши, которые не подходят под push-to-talk: X11 включает для них автоповтор,
# и удержание превращается в гирлянду press/release. Модификаторы безопасны.
SAFE_MODIFIER_KEYCODES = {37, 105, 50, 62, 64, 108, 133, 134, 66}


@dataclass
class Config:
    """Всё, что можно поменять из менюшки настроек."""

    # --- горячая клавиша ---
    hotkey_keycode: int = 105  # Control_R: автоповтор для него выключен (проверено xset q)
    hotkey_label: str = "правый Ctrl"
    min_press_seconds: float = 0.3  # короче — считаем случайным касанием, не распознаём

    # --- модель ---
    model: str = "gigaam-v3-e2e-ctc"
    quantization: str = "int8"
    threads: int = 2  # уточняется по итогам bench/benchmark.py
    offline: bool = True  # S6: после первой загрузки в сеть не ходим
    idle_unload_seconds: int = 300  # 0 = держать модель в памяти всегда
    max_chunk_seconds: int = 20  # предел GigaAM ~25 с, режем с запасом

    # --- звук ---
    input_device: str | None = None  # None = устройство по умолчанию

    # --- буфер обмена ---
    clipboard_clear_seconds: int = 0  # 0 = не очищать

    @property
    def model_dir(self) -> Path:
        return MODELS_DIR / self.model

    @classmethod
    def load(cls) -> Config:
        """Читает конфиг, игнорируя незнакомые и битые поля.

        Настройки не должны быть причиной, по которой приложение не стартует:
        при любой проблеме откатываемся на значения по умолчанию.
        """
        if not CONFIG_PATH.is_file():
            return cls()
        try:
            raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        if not isinstance(raw, dict):
            return cls()

        known = {f.name: f for f in fields(cls)}
        kwargs = {}
        for name, value in raw.items():
            field = known.get(name)
            if field is None:
                continue
            kwargs[name] = value

        try:
            config = cls(**kwargs)
        except TypeError:
            return cls()
        config.validate()
        return config

    def validate(self) -> None:
        """Правит заведомо нерабочие значения, не роняя приложение."""
        if not isinstance(self.hotkey_keycode, int) or not 8 <= self.hotkey_keycode <= 255:
            self.hotkey_keycode = 105
            self.hotkey_label = "правый Ctrl"
        self.threads = max(1, min(int(self.threads), 8))
        self.max_chunk_seconds = max(5, min(int(self.max_chunk_seconds), 25))
        self.min_press_seconds = max(0.0, min(float(self.min_press_seconds), 2.0))
        self.idle_unload_seconds = max(0, int(self.idle_unload_seconds))
        self.clipboard_clear_seconds = max(0, int(self.clipboard_clear_seconds))
        if self.quantization not in ("int8", None, "", "fp32"):
            self.quantization = "int8"

    def save(self) -> None:
        """Атомарная запись: сначала во временный файл, потом rename."""
        ensure_dirs()
        tmp = CONFIG_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False), encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, CONFIG_PATH)


def ensure_dirs() -> None:
    """S7: каталоги приложения — только для владельца."""
    for path in (CONFIG_DIR, DATA_DIR, MODELS_DIR):
        path.mkdir(parents=True, exist_ok=True)
        # Права могут не примениться на сетевой или чужой файловой системе — не беда,
        # каталог всё равно создан. Ронять из-за этого запуск нельзя.
        with contextlib.suppress(OSError):
            os.chmod(path, 0o700)
