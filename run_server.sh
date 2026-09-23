#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

python_bin="${DATAREPLAY_PYTHON:-python3}"
if [[ -z "${DATAREPLAY_PYTHON:-}" && -x .venv/bin/python ]]; then
  python_bin="$PWD/.venv/bin/python"
elif [[ -z "${DATAREPLAY_PYTHON:-}" && -x /mnt/sunbing/projects/RLinf/.venv/bin/python ]]; then
  python_bin=/mnt/sunbing/projects/RLinf/.venv/bin/python
fi

# With arguments, use the supplied sources instead of the A800 defaults.
if (( $# == 0 )); then
  set -- \
    --source '快递分拣=LH217=/mnt/datasets/ExpressSorting_LH217' \
    --source '茶艺=LH214=/mnt/datasets/Tea_LH214' \
    --source '双脑=LH210=/mnt/datasets/ExpressSorting_LH210' \
    --source '双脑=清洗训练集=/mnt/sunbing/projects/RLinf_expresssorting_0904_0917_clean25hz'
fi
exec "$python_bin" -u server.py \
  --host "${DATAREPLAY_HOST:-127.0.0.1}" \
  --port "${DATAREPLAY_PORT:-7865}" "$@"
