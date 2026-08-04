#!/usr/bin/env bash
# Установка Golos «в один шаг». Рассчитано на то, что человек просто запускает файл
# и отвечает на пару вопросов, а не разбирается в питоне.
#
# Скрипт можно запускать повторно: он пропускает то, что уже сделано.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

# Проверяем не имена пакетов, а то, что реально работает. Причина конкретная: на этой
# системе стоит python3.12-venv, а метапакета python3-venv нет, и проверка по имени
# требовала бы пароль и установку того, что уже есть. Список пар:
#   «команда-проверка» → «что доставить, если не работает»
CHECKS=(
    "python3 -m venv --help                              |python3-venv"
    "python3 -c 'import gi; gi.require_version(\"Gtk\",\"3.0\")'|python3-gi"
    "python3 -c 'import cairo'                           |python3-gi-cairo"
    "python3 -c 'import gi; gi.require_version(\"AyatanaAppIndicator3\",\"0.1\")'|gir1.2-ayatanaappindicator3-0.1"
    "python3 -c 'import Xlib'                            |python3-xlib"
    "python3 -c 'import ctypes.util,sys; sys.exit(ctypes.util.find_library(\"portaudio\") is None)'|libportaudio2"
)
# python3-pip сознательно отсутствует: venv приносит собственный pip.

say()  { printf '\n\033[1m%s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
die()  { printf '\n\033[31mНе получилось:\033[0m %s\n\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------- 1. системные пакеты

say "Шаг 1 из 4. Системные пакеты"

missing=()
for entry in "${CHECKS[@]}"; do
    probe="${entry%%|*}"
    pkg="${entry##*|}"
    if eval "$probe" >/dev/null 2>&1; then
        ok "$pkg — уже есть"
    else
        missing+=("$pkg")
    fi
done

if ((${#missing[@]})); then
    warn "нужно доустановить: ${missing[*]}"
    echo "  Для этого система спросит пароль — тот же, что при входе в компьютер."
    sudo apt-get install -y "${missing[@]}" || die "не удалось установить системные пакеты"
    ok "готово"
else
    ok "всё системное уже на месте, пароль не понадобится"
fi

# ---------------------------------------------------------- 2. окружение и библиотеки

say "Шаг 2 из 4. Библиотеки для распознавания"

# --system-site-packages принципиально: GTK и python-xlib уже стоят в системе,
# собирать их заново через pip на слабой машине долго и незачем.
if [[ ! -x .venv/bin/python ]]; then
    python3 -m venv --system-site-packages .venv || die "не удалось создать окружение .venv"
    ok "окружение создано"
else
    ok "окружение уже есть"
fi

# Прокси-переменные окружения ломают загрузку у части систем; при установке они
# могут быть нужны, поэтому здесь их НЕ трогаем — в отличие от run.sh.
# Обновление pip — приятно, но не обязательно: если интернета нет, а библиотеки уже
# стоят, установка обязана продолжиться, а не упасть на необязательном шаге.
.venv/bin/python -m pip install --quiet --upgrade pip 2>/dev/null || true
if [[ -f requirements.lock.txt ]]; then
    .venv/bin/python -m pip install --quiet -r requirements.lock.txt || die "не удалось поставить библиотеки"
else
    .venv/bin/python -m pip install --quiet -r requirements.txt || die "не удалось поставить библиотеки"
fi
ok "библиотеки на месте"

# ------------------------------------------------------------------- 3. сама модель

say "Шаг 3 из 4. Модель распознавания речи (215 МБ)"

MODEL_DIR="$HOME/.local/share/golos/models/gigaam-v3-e2e-ctc"
if [[ -f "$MODEL_DIR/manifest.json" ]]; then
    ok "модель уже скачана"
else
    echo "  Скачивается один раз. Дальше интернет не нужен вообще."
    .venv/bin/python tools/fetch_models.py gigaam-v3-e2e-ctc || die "не удалось скачать модель"
    ok "модель скачана"
fi

# ------------------------------------------------------------------- 4. автозапуск

say "Шаг 4 из 4. Запуск вместе с системой"

AUTOSTART="$HOME/.config/autostart/golos.desktop"
if [[ -f "$AUTOSTART" ]]; then
    ok "автозапуск уже включён"
else
    read -rp "  Запускать Golos автоматически при включении компьютера? [Д/н] " answer
    case "${answer,,}" in
        n|н|no|нет)
            warn "автозапуск не включён — его можно включить позже в настройках программы"
            ;;
        *)
            # Ярлык пишет та же функция, что и галочка в настройках программы — иначе
            # два места создавали бы чуть разные файлы и расходились со временем.
            .venv/bin/python -c \
                'from golos.ui.settings import set_autostart; set_autostart(True)' \
                || die "не удалось включить автозапуск"
            ok "автозапуск включён"
            ;;
    esac
fi

chmod +x run.sh

say "Готово."
cat <<'FINAL'
  Запустить прямо сейчас:   ./run.sh
  Возле часов появится значок микрофона.

  Как пользоваться: зажмите ПРАВЫЙ Ctrl, говорите, отпустите.
  Текст окажется в буфере обмена — вставьте его через Ctrl+V.

FINAL
