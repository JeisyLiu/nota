#!/usr/bin/env bash
# Start Nota on macOS / Linux.
#   ./run.sh              -> http://127.0.0.1:8787
#   PORT=9000 ./run.sh
set -euo pipefail

cd "$(dirname "$0")"

if [ -d .venv ]; then
  # shellcheck disable=SC1091
  . .venv/bin/activate
fi

PYTHON="${PYTHON:-python3}"

if ! "$PYTHON" -c "import fastapi, uvicorn" 2>/dev/null; then
  echo "依赖缺失，请先运行: $PYTHON -m pip install -r requirements.txt" >&2
  exit 1
fi

if [ ! -f .env ]; then
  echo "模式: demo（未找到 .env，使用确定性本地规划器）"
fi

export PYTHONIOENCODING=utf-8
exec "$PYTHON" -X utf8 -m backend.main
