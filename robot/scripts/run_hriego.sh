#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LAPLACE_ROOT="${LAPLACE_ROOT:-/home/luis/GitHub/Laplace}"
FACE_PYTHON="${HRIEGO_FACE_PYTHON:-$ROOT/Face/.venv-face/bin/python}"
QWEN_PYTHON="${HRIEGO_QWEN_PYTHON:-/home/luis/anaconda3/envs/physicalai/bin/python}"
INDEX_PYTHON="${HRIEGO_INDEX_PYTHON:-$LAPLACE_ROOT/voice/.venv-index/bin/python}"

for path in "$FACE_PYTHON" "$QWEN_PYTHON" "$INDEX_PYTHON" "$LAPLACE_ROOT/voice"; do
    if [[ ! -e "$path" ]]; then
        echo "HRIEGO: falta recurso local: $path" >&2
        exit 2
    fi
done

export LAPLACE_ROOT
export HRIEGO_FACE_PYTHON="$FACE_PYTHON"
export HRIEGO_QWEN_PYTHON="$QWEN_PYTHON"
export HRIEGO_INDEX_PYTHON="$INDEX_PYTHON"
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
export TOKENIZERS_PARALLELISM=false
export UNIFACE_CACHE_DIR="$ROOT/Face/thirdparty/uniface-models"
export PYTHONPATH="$ROOT/Face/thirdparty/uniface:$ROOT/Face:$ROOT:$LAPLACE_ROOT/voice${PYTHONPATH:+:$PYTHONPATH}"

cd "$ROOT"
exec "$FACE_PYTHON" -m hriego.main "$@"
