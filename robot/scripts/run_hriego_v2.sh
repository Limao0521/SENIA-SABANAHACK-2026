#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FACE_PYTHON="${HRIEGO_FACE_PYTHON:-$ROOT/Face/.venv-face/bin/python}"

if [[ ! -x "$FACE_PYTHON" ]]; then
    echo "HRIEGO v2: falta entorno Face: $FACE_PYTHON" >&2
    exit 2
fi
export HRIEGO_FACE_PYTHON="$FACE_PYTHON"
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 TOKENIZERS_PARALLELISM=false
export UNIFACE_CACHE_DIR="$ROOT/Face/thirdparty/uniface-models"
export PYTHONPATH="$ROOT/Face/thirdparty/uniface:$ROOT/Face:$ROOT${PYTHONPATH:+:$PYTHONPATH}"

cd "$ROOT"
exec "$FACE_PYTHON" -m hriego_v2.main "$@"
