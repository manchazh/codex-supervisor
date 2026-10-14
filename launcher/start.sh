#!/bin/sh
# Запускайте: sh start.sh ["папка проекта"] [--resume]
launcher_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd) || exit 1
exec python3 "$launcher_dir/launch.py" "$@"
