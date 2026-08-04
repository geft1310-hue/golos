#!/usr/bin/env python3
"""S3/S4: убедиться, что надиктованное не оседает на диске.

Метод — фраза-маркер. Через настоящий путь приложения (распознавание файла →
буфер обмена → история) прогоняется текст с уникальной строкой, которой заведомо
нет больше нигде в системе. После этого весь домашний каталог обыскивается на неё.
Любое совпадение вне памяти процесса — утечка.

Заодно снимается «до/после» по файлам в ~/.config/golos и ~/.local/share/golos:
запись аудио или истории проявилась бы новым файлом.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import gi
from sample_audio import require_sample

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from golos.asr import Recognizer  # noqa: E402
from golos.clipboard import ClipboardManager  # noqa: E402
from golos.config import CONFIG_DIR, DATA_DIR, Config  # noqa: E402

CANARY = "ЗЮЗЮКАБЛИМБЕРВОРТ7731"
SEARCH_ROOTS = ["~/.config", "~/.local", "~/.cache"]


def snapshot(root: Path) -> set[Path]:
    return {p for p in root.rglob("*") if p.is_file()} if root.exists() else set()


def grep_disk(needle: str) -> list[str]:
    roots = [str(Path(r).expanduser()) for r in SEARCH_ROOTS]
    proc = subprocess.run(
        ["grep", "-rlaI", "--", needle, *roots],
        capture_output=True, text=True, check=False,
    )
    return [line for line in proc.stdout.splitlines() if line]


def main() -> int:
    config = Config.load()
    before = {d: snapshot(d) for d in (CONFIG_DIR, DATA_DIR)}

    # 1. Настоящее распознавание настоящего файла — как при диктовке.
    pcm, rate = require_sample()
    recognized = Recognizer(config).transcribe(pcm, rate)
    print(f"распознано: {len(recognized)} символов")

    # 2. Полный путь вывода: буфер обмена + история (S2/S3).
    clipboard = ClipboardManager(clear_after_seconds=0)
    clipboard.copy(f"{recognized} {CANARY}")

    # Дать менеджеру буфера и системе время что-нибудь записать, если они собирались.
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
        time.sleep(0.05)
    GLib.MainContext.default().iteration(False)

    print(f"в буфере сейчас: {clipboard._clipboard.wait_for_text() is not None}")

    # 3. Обыск диска, пока текст ещё в буфере — момент наибольшего риска.
    hits = grep_disk(CANARY)
    print("\n--- поиск фразы-маркера на диске ---")
    if hits:
        print("НАЙДЕНО (утечка!):")
        for h in hits:
            print("   ", h)
    else:
        print("не найдено нигде ✓")

    # 4. Новые файлы?
    print("\n--- новые файлы в каталогах программы ---")
    leaked_files = []
    for directory, old in before.items():
        for path in sorted(snapshot(directory) - old):
            leaked_files.append(path)
            print("   НОВЫЙ:", path)
    if not leaked_files:
        print("новых файлов нет ✓")

    audio = [p for p in Path.home().rglob("*.wav") if "golos" in str(p) and "bench" not in str(p)]
    print("\n--- аудиофайлы, оставленные программой (S4) ---")
    print("   " + ("\n   ".join(map(str, audio)) if audio else "нет ✓"))

    # 5. Очистка при выходе (S3).
    clipboard.wipe()
    print("\n--- после wipe() при выходе ---")
    print(f"история в памяти: {len(clipboard.history)} записей")
    print(f"буфер обмена: {clipboard._clipboard.wait_for_text()!r}")

    ok = not hits and not leaked_files and not audio and not clipboard.history
    print("\nИТОГ:", "S3/S4 соблюдены" if ok else "ЕСТЬ ПРОБЛЕМА, см. выше")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
