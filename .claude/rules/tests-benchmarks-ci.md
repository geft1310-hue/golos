---
paths:
  - "bench/**/*.py"
  - ".github/workflows/**/*.yml"
  - "pyproject.toml"
  - "requirements*.txt"
  - "install.sh"
  - "run.sh"
  - ".gitignore"
---

# Роль: проверки, benchmarks и CI

- Не предполагай наличие pytest или test discovery. Каждый `bench/test_*.py` и benchmark —
  отдельный исполняемый сценарий с `main()`; один тест запускается напрямую через Python.
- Держи CI детерминированным и без скачивания модели: Ruff, compileall, import smoke под Xvfb,
  `bench/test_platform.py`, shell syntax и `tools/check_site.py`.
- `test_audio.py`, `test_hotkey.py`, `test_grab_exposure.py` и `test_overlay.py` требуют живого
  микрофона или X11. ASR, memory, privacy и benchmark-сценарии требуют локальных весов и/или
  собственной незакоммиченной записи. Не выдавай их пропуск за успешный прогон.
- Для измерений сохраняй проверяемые показатели RTF, RSS и холодной загрузки; не ослабляй gates
  без нового воспроизводимого замера и обновления опубликованных чисел.
- Окружение разработки повторяет CI: venv создаётся с `--system-site-packages`, GTK/Xlib берутся
  из системы, Python-зависимости — из `requirements.lock.txt`, версия Ruff закреплена workflow.
- Никогда не добавляй в git аудиозаписи, `*.onnx` или `*.onnx_data`. При расширении списка
  аудиоформатов синхронизируй `.gitignore` и CI-проверку tracked artifacts.
- После изменения Python-кода запускай релевантный отдельный сценарий и детерминированные команды
  CI; после изменения shell-файлов обязательно выполняй `bash -n install.sh && bash -n run.sh`.
