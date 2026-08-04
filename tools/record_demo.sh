#!/usr/bin/env bash
# Запись демонстрационного ролика для README — в два шага, чтобы машина не легла.
#
# Почему не «просто ffmpeg, который сразу пишет gif или mp4»: сжатие видео на лету
# занимает оба ядра, а рядом в это же самое время работает распознавание речи. На
# слабой машине это кончается свопом и зависанием — проверено на себе, 4 августа.
#
# Поэтому шага два:
#   1) snjat  — запись почти без сжатия. Процессор при этом почти свободен, нагрузка
#               уходит на диск, а Golos спокойно работает рядом.
#   2) gif    — превращение записи в GIF. Тяжёлый шаг, но он выполняется потом,
#               когда Golos уже закрыт и торопиться некуда.
#
# Использование:
#   tools/record_demo.sh снять            запись (сама остановится через 40 секунд)
#   tools/record_demo.sh гиф              весь ролик в docs/demo.gif
#   tools/record_demo.sh гиф 3 12         только с 3-й секунды, длительностью 12 с

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HERE"

DOCS="docs"
RAW="$DOCS/demo-raw.mkv"
PALETTE="$DOCS/demo-palette.png"
GIF="$DOCS/demo.gif"

FPS=10           # для показа интерфейса хватает с запасом, а данных втрое меньше, чем при 30
MAX_SECONDS=40   # предохранитель: забытая запись не должна съесть диск
GIF_WIDTH=760    # ширина готового GIF в пикселях

red() { printf '\033[31m%s\033[0m\n' "$*"; }
green() { printf '\033[32m%s\033[0m\n' "$*"; }
bold() { printf '\033[1m%s\033[0m\n' "$*"; }
die() { red "✗ $*"; exit 1; }

command -v ffmpeg >/dev/null || die "нет ffmpeg. Поставьте: sudo apt install ffmpeg"
command -v xdpyinfo >/dev/null || die "нет xdpyinfo. Поставьте: sudo apt install x11-utils"
if [[ "${XDG_SESSION_TYPE:-}" == "wayland" ]]; then
    die "сеанс Wayland: x11grab снимет чёрный экран. Golos там всё равно не работает, см. README"
fi
mkdir -p "$DOCS"

# Область записи: снизу по центру экрана — именно там Golos показывает своё окошко.
# Берём кусок, а не весь экран: меньше данных на диск и мельче итоговый GIF.
screen_geometry() {
    local dims
    dims="$(xdpyinfo | awk '/dimensions:/ {print $2; exit}')"
    echo "${dims%x*} ${dims#*x}"
}

read -r SCREEN_W SCREEN_H < <(screen_geometry)
W=$(( SCREEN_W < 1100 ? SCREEN_W : 1100 ))
H=$(( SCREEN_H < 680 ? SCREEN_H : 680 ))
W=$(( W - W % 2 )); H=$(( H - H % 2 ))   # ffmpeg не любит нечётные размеры
X=$(( (SCREEN_W - W) / 2 ))
Y=$(( SCREEN_H - H ))

cmd_record() {
    local free_mb
    free_mb=$(df -Pm . | awk 'NR==2 {print $4}')
    [[ "$free_mb" -gt 2000 ]] || die "на диске всего ${free_mb} МБ — запись может его добить"

    bold "Область записи: ${W}x${H}, левый верхний угол ${X},${Y} (низ экрана по центру)."
    echo "Поставьте окно редактора так, чтобы оно попадало в эту область."
    echo "Окошко Golos появляется там же — внизу по центру."
    echo
    echo "Запись остановится сама через ${MAX_SECONDS} с, либо нажмите Ctrl+C."
    echo
    for i in 5 4 3 2 1; do printf '\r  начинаю через %d… ' "$i"; sleep 1; done
    printf '\r  ЗАПИСЬ ИДЁТ            \n'

    # ffvhuff — сжатие без потерь и почти без затрат процессора (обычный Хаффман,
    # без предсказаний и поиска движения). Данных в 2-3 раза меньше, чем у сырого
    # потока, а ядра остаются свободными для распознавания.
    ffmpeg -hide_banner -loglevel error -y \
        -f x11grab -framerate "$FPS" -video_size "${W}x${H}" \
        -i "${DISPLAY}+${X},${Y}" \
        -c:v ffvhuff -pix_fmt yuv420p -t "$MAX_SECONDS" \
        "$RAW" || true   # Ctrl+C — это штатное завершение записи, а не ошибка

    [[ -s "$RAW" ]] || die "запись не получилась, файл пустой"
    green "✓ записано: $RAW ($(du -h "$RAW" | cut -f1))"
    echo "  Посмотреть: xdg-open $RAW"
    echo "  Дальше, когда закроете Golos:  tools/record_demo.sh гиф"
}

cmd_gif() {
    [[ -s "$RAW" ]] || die "нет записи $RAW — сначала: tools/record_demo.sh снять"

    local trim=()
    [[ $# -ge 1 ]] && trim+=(-ss "$1")
    [[ $# -ge 2 ]] && trim+=(-t "$2")

    echo "Шаг 1 из 2: подбираю палитру…"
    ffmpeg -hide_banner -loglevel error -y "${trim[@]}" -i "$RAW" \
        -vf "fps=${FPS},scale=${GIF_WIDTH}:-1:flags=lanczos,palettegen=max_colors=128" \
        "$PALETTE"

    echo "Шаг 2 из 2: собираю GIF…"
    ffmpeg -hide_banner -loglevel error -y "${trim[@]}" -i "$RAW" -i "$PALETTE" \
        -lavfi "fps=${FPS},scale=${GIF_WIDTH}:-1:flags=lanczos[v];[v][1:v]paletteuse=dither=bayer:bayer_scale=3" \
        "$GIF"
    rm -f "$PALETTE"

    local size_kb
    size_kb=$(du -k "$GIF" | cut -f1)
    green "✓ готово: $GIF ($(du -h "$GIF" | cut -f1))"
    if [[ "$size_kb" -gt 5120 ]]; then
        red "  Больше 5 МБ — на GitHub такой GIF грузится долго."
        echo "  Обрежьте лишнее: tools/record_demo.sh гиф <с какой секунды> <сколько секунд>"
    fi
}

case "${1:-}" in
    снять|record) shift; cmd_record "$@" ;;
    гиф|gif) shift; cmd_gif "$@" ;;
    *)
        echo "tools/record_demo.sh снять        — записать (в два шага, чтобы не подвесить машину)"
        echo "tools/record_demo.sh гиф [с] [дл] — превратить запись в docs/demo.gif"
        exit 1
        ;;
esac
