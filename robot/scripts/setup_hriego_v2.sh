#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FACE="$ROOT/Face"
VOICE="$ROOT/experiments/hriego_v2_tts_eval_20261021"
PYTHON_BIN="${HRIEGO_SETUP_PYTHON:-$(command -v python3.12 || true)}"
SKIP_DOCKER=0

if [[ "${1:-}" == "--skip-docker" ]]; then
    SKIP_DOCKER=1
elif [[ $# -gt 0 ]]; then
    echo "Uso: $0 [--skip-docker]" >&2
    exit 2
fi

if [[ -z "$PYTHON_BIN" || ! -x "$PYTHON_BIN" ]]; then
    echo "Falta Python 3.12 con venv y pip (HRIEGO_SETUP_PYTHON permite elegirlo)." >&2
    exit 2
fi
for tool in docker curl nvidia-smi; do
    command -v "$tool" >/dev/null || { echo "Falta $tool" >&2; exit 2; }
done
if ! command -v pw-play >/dev/null && ! command -v aplay >/dev/null; then
    echo "Falta pw-play (PipeWire) o aplay (ALSA) para reproducir audio." >&2
    exit 2
fi
docker compose version >/dev/null
if [[ ! -f "$FACE/data/institutional_identity.sqlite3" ]]; then
    echo "Falta Face/data/institutional_identity.sqlite3; comprueba que clonaste esta rama completa." >&2
    exit 2
fi
chmod 600 "$FACE/data/institutional_identity.sqlite3"
if [[ ! -f "$ROOT/hriego_v2/contract_assets/intervention.schema.json" ]]; then
    echo "Falta el contrato RCP-01c versionado." >&2
    exit 2
fi

if [[ ! -x "$FACE/.venv-face/bin/python" ]]; then
    "$PYTHON_BIN" -m venv "$FACE/.venv-face"
fi
if ! "$FACE/.venv-face/bin/python" -c 'import cv2, numpy, scipy, skimage, onnxruntime, torch' >/dev/null 2>&1; then
    "$FACE/.venv-face/bin/python" -m pip install --upgrade pip
    "$FACE/.venv-face/bin/python" -m pip install -r "$FACE/requirements/live-gpu-cu130.txt"
fi
if ! "$FACE/.venv-face/bin/python" -c 'import zxingcpp' >/dev/null 2>&1; then
    "$FACE/.venv-face/bin/python" -m pip install zxing-cpp     # QR de la credencial en la webcam (show_face_stream)
fi

export PYTHONPATH="$FACE/thirdparty/uniface:$FACE:$ROOT${PYTHONPATH:+:$PYTHONPATH}"
export UNIFACE_CACHE_DIR="$FACE/thirdparty/uniface-models"
"$FACE/.venv-face/bin/python" - <<'PY'
from uniface.constants import EdgeFaceWeights, RetinaFaceWeights
from uniface.model_store import verify_model_weights
for model in (RetinaFaceWeights.MNET_V2, EdgeFaceWeights.XXS):
    print("UniFace verificado:", verify_model_weights(model), flush=True)
PY

mkdir -p "$VOICE"
if [[ ! -x "$VOICE/.venv/bin/python" ]]; then
    "$PYTHON_BIN" -m venv "$VOICE/.venv"
fi
if ! "$VOICE/.venv/bin/python" -c 'import supertonic, onnxruntime, soundfile' >/dev/null 2>&1; then
    "$VOICE/.venv/bin/python" -m pip install --upgrade pip
    "$VOICE/.venv/bin/python" -m pip install -r "$ROOT/hriego_v2/requirements-supertonic.txt"
fi
RAM_AVAILABLE_KIB="$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo)"
if [[ -z "$RAM_AVAILABLE_KIB" || "$RAM_AVAILABLE_KIB" -lt 5242880 ]]; then
    echo "Se necesitan al menos 5 GiB de RAM disponible para verificar el TTS." >&2
    exit 2
fi
VOICE_MODEL_DIR="$VOICE/model" "$VOICE/.venv/bin/python" - <<'PY'
import os
from supertonic import TTS
tts = TTS(model_dir=os.environ["VOICE_MODEL_DIR"], auto_download=True,
          intra_op_num_threads=4, inter_op_num_threads=1)
tts.get_voice_style("M1")
print("Supertonic 3/M1 disponible y cargado", flush=True)
PY

GGUF="$ROOT/llm/models/Qwen3-4B-Q4_K_M.gguf"
if [[ ! -f "$GGUF" ]]; then
    "$ROOT/llm/scripts/download-model.sh" 4B Q4_K_M
fi
echo "7485fe6f11af29433bc51cab58009521f205840f5b4ae3a32fa7f92e8534fdf5  $GGUF" | sha256sum --check --status || {
    echo "El GGUF no coincide con el Qwen3-4B Q4_K_M verificado para HRIEGO v2." >&2
    exit 2
}

if [[ "$SKIP_DOCKER" == 0 ]]; then
    if ! docker exec x2-llm curl -sf http://127.0.0.1:8000/ready >/dev/null 2>&1; then
        (cd "$ROOT/llm" && docker compose -f docker-compose.yml -f compose.x86-cuda.yml up -d --build)
    fi
    for _ in $(seq 1 180); do
        if docker exec x2-llm curl -sf http://127.0.0.1:8000/ready >/dev/null 2>&1; then
            break
        fi
        sleep 2
    done
fi

"$ROOT/scripts/run_hriego_v2.sh" --check-only
echo "Instalación lista. Ejecuta: $ROOT/scripts/run_hriego_v2.sh"
