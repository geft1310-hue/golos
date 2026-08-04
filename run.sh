#!/usr/bin/env bash
# Запуск Golos. Этот же файл прописывается в автозапуск.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

if [[ ! -x .venv/bin/python ]]; then
    echo "Нет окружения .venv — выполните установку по инструкции в README.md" >&2
    exit 1
fi

# Модель работает локально, интернет не нужен. Прокси-переменные системы только мешают
# и создают лишние сетевые попытки у программы, которая слушает микрофон.
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY all_proxy

exec .venv/bin/python -m golos "$@"
