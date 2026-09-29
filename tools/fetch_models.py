#!/usr/bin/env python3
"""Скачивание весов моделей в ~/.local/share/golos/models (или GOLOS_MODELS_DIR).

Отдельно от бенчмарка по двум причинам:

1. Память. Здесь не создаётся ни одной InferenceSession — только скачивание файлов.
   На машине с исчерпанным swap это принципиально: качать и поднимать модель в одном
   процессе — лишний повод получить OOM.

2. Ловушка резолвера onnx-asr. В `Resolver.__init__` есть строка

       if self.local_dir.exists(): self.offline = True

   то есть если папка модели уже создана (хоть пустая), библиотека считает модель
   локальной и в сеть не идёт вообще. Поэтому папку модели заранее создавать нельзя —
   её создаёт сам snapshot_download. Здесь это обойдено явным revision-пином.

S5: репозиторий пинится на конкретный commit sha, после скачивания пишется
manifest.json с sha256 каждого файла — при последующих стартах приложение сверяет.
S7: права 0700 выставляются после скачивания (только для пользовательского каталога:
стaging для deb-пакета собирается под root и права там выставляет упаковщик).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from golos.models import MODELS as MODEL_SPECS  # noqa: E402

# Умолчание — то же, что в Config.model. Остальные варианты качаются по требованию,
# когда человек выбирает их в настройках: класть 645 МБ при установке незачем.
MODELS = ["gigaam-v3-e2e-rnnt"]
MODELS_DIR = Path(os.environ.get("GOLOS_MODELS_DIR", Path.home() / ".local/share/golos/models"))


def build_allow_patterns(model_name: str) -> list[str]:
    """Какие файлы репозитория качать.

    Для gigaam повторяет логику Resolver._download_model (нужен только свой вариант,
    ~225 МБ, а не весь репозиторий в 4.46 ГБ). Для whisper имена файлов фиксированы
    форматом transformers.js, так что список задаётся явно — включая разницу
    в суффиксе квантования: у Xenova это *_quantized.onnx, у onnx-community *_int8.onnx.
    """
    spec = MODEL_SPECS[model_name]
    quant = spec["quantization"]
    if spec.get("whisper"):
        suffix = f"_{quant}" if quant else ""
        return [
            "config.json",
            "preprocessor_config.json",
            "generation_config.json",
            "vocab.json",
            "added_tokens.json",
            f"onnx/encoder_model{suffix}.onnx",
            f"onnx/decoder_model_merged{suffix}.onnx",
        ]
    from onnx_asr.loader import create_asr_resolver

    resolver = create_asr_resolver(model_name, offline=False)
    files = list(resolver.model_type._get_model_files(quant).values())
    files = [*files, *(f.removeprefix("**/") for f in files if f.startswith("**/"))]
    return [
        "config.json",
        "config.yaml",
        *files,
        *(str(p.with_suffix(".onnx?data")) for f in files if (p := Path(f)).suffix == ".onnx"),
    ]


def repo_id_of(model_name: str) -> str:
    return MODEL_SPECS[model_name]["repo_id"]


def model_files(target: Path) -> list[Path]:
    """Только сами веса и конфиги.

    `.cache/huggingface/*` исключаем сознательно: это служебные метаданные самой
    библиотеки, они меняются при каждой проверке обновлений, и их sha256 в манифесте
    давали бы ложные срабатывания проверки целостности (S5).
    """
    return [
        p
        for p in target.rglob("*")
        if p.is_file() and p.name != "manifest.json" and ".cache" not in p.parts
    ]


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def harden(path: Path) -> None:
    """S7: ничего лишнего для группы и остальных."""
    for p in [path, *path.rglob("*")]:
        with contextlib.suppress(OSError):
            os.chmod(p, 0o700 if p.is_dir() else 0o600)


def fetch(model_name: str) -> int:
    from huggingface_hub import HfApi, snapshot_download

    repo_id = repo_id_of(model_name)
    target = MODELS_DIR / model_name

    if (target / "manifest.json").is_file():
        print(f"[{model_name}] уже скачано, пропускаю")
        return 0

    patterns = build_allow_patterns(model_name)
    print(f"[{model_name}] репозиторий {repo_id}")
    print(f"[{model_name}] файлы: {patterns}")

    revision = HfApi().model_info(repo_id).sha
    print(f"[{model_name}] коммит {revision}")

    snapshot_download(
        repo_id,
        revision=revision,
        local_dir=str(target),
        allow_patterns=patterns,
        local_files_only=False,
    )

    files = sorted(model_files(target))
    if not files:
        print(f"[{model_name}] ОШИБКА: не скачалось ни одного файла", file=sys.stderr)
        return 1

    manifest = {
        "repo_id": repo_id,
        "revision": revision,
        "quantization": MODEL_SPECS[model_name]["quantization"],
        "files": {str(p.relative_to(target)): sha256_of(p) for p in files},
    }
    (target / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    total_mb = sum(p.stat().st_size for p in files) / 1024**2
    harden(target)

    print(f"[{model_name}] готово: {len(files)} файлов, {total_mb:.1f} МБ")
    for p in files:
        print(f"    {p.relative_to(target)}  {p.stat().st_size / 1024**2:.1f} МБ")
    return 0


def fix_socks_proxy() -> None:
    """Если в системе задан ALL_PROXY=socks5://..., huggingface_hub падает с
    ImportError: httpx не умеет SOCKS без пакета socksio. На этой машине тот же прокси
    слушает и обычный HTTP (https_proxy), поэтому достаточно убрать SOCKS-переменные —
    ставить лишнюю зависимость в приложение с микрофоном не хочется.
    """
    if not any(os.environ.get(v, "").startswith("socks") for v in ("ALL_PROXY", "all_proxy")):
        return
    try:
        import socksio  # noqa: F401
    except ImportError:
        if not (os.environ.get("https_proxy") or os.environ.get("HTTPS_PROXY")):
            print("ВНИМАНИЕ: задан SOCKS-прокси, но нет socksio и нет https_proxy —"
                  " скачивание, скорее всего, не пройдёт", file=sys.stderr)
            return
        for var in ("ALL_PROXY", "all_proxy"):
            os.environ.pop(var, None)
        print("SOCKS-прокси отключён для скачивания, используется https_proxy")


def main() -> int:
    fix_socks_proxy()
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(MODELS_DIR, 0o700)
    os.chmod(MODELS_DIR.parent, 0o700)

    names = sys.argv[1:] or MODELS
    unknown = [n for n in names if n not in MODEL_SPECS]
    if unknown:
        print(f"неизвестные модели: {unknown}; доступные: {sorted(MODEL_SPECS)}", file=sys.stderr)
        return 2

    rc = 0
    for name in names:
        try:
            rc |= fetch(name)
        except Exception as exc:  # noqa: BLE001
            print(f"[{name}] ОШИБКА: {type(exc).__name__}: {exc}", file=sys.stderr)
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
