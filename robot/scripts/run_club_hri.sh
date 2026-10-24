#!/usr/bin/env bash
# Runtime HRI ONLINE del Club N (T6). Punto de entrada único:
#   scripts/run_club_hri.sh --all                     # 6 WAV + 5 visitas (pass/fail)
#   scripts/run_club_hri.sh --visit VIP --video       # visita VIP → out_reto2/online/vip_visit.mp4
#   scripts/run_club_hri.sh --wav 5 --rank WORKER
#
# Entornos: show/app en $X2_PYTHON (default .venv del repo o el venv de x2_ultra-retos);
# ASR en .venv-asr (worker propio, T3); movimiento online en el env physicalai (T4);
# x2-llm en docker rootless: el puerto 8080 solo es alcanzable dentro del netns de
# rootlesskit ⇒ este script envuelve el proceso con nsenter salvo HRI_NO_NSENTER=1.
#
# Memoria (spec): NO arrancar el gemelo con < 8 GB libres; este script no lo usa.
set -eo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"
PY="${X2_PYTHON:-${PYTHON:-}}"
if [[ -z $PY || ! -x $PY ]]; then
    for cand in "$REPO/.venv/bin/python" /home/luis/GitHub/x2_ultra-retos/.venv/bin/python; do
        [[ -x $cand ]] && PY="$cand" && break
    done
fi
if [[ -z $PY || ! -x $PY ]]; then
    echo "ERROR: no hay intérprete con x2_ultra (X2_PYTHON o .venv)." >&2
    exit 1
fi
export MUJOCO_GL="${MUJOCO_GL:-egl}"
export PYTHONPATH="$REPO/src${PYTHONPATH:+:$PYTHONPATH}"
export ASR_PYTHON="${ASR_PYTHON:-$REPO/.venv-asr/bin/python}"
export HRI_LLM_URL="${HRI_LLM_URL:-http://127.0.0.1:8080}"

ARGS=("$@")
if [[ ${HRI_NO_NSENTER:-0} != 1 ]]; then
    RK="$(pgrep -x rootlesskit | head -1 || true)"
    if [[ -n $RK ]]; then
        CHILD="$(pgrep -P "$RK" | head -1 || true)"
        NS_PID="${CHILD:-$RK}"
        echo "[run_club_hri] x2-llm vía netns rootlesskit (nsenter -t $NS_PID)" >&2
        exec nsenter -t "$NS_PID" -U -n --preserve-credentials "$PY" -m x2_ultra.hri_runtime.wavtest "${ARGS[@]}"
    fi
fi
exec "$PY" -m x2_ultra.hri_runtime.wavtest "${ARGS[@]}"
