#!/usr/bin/env bash
# Recepción HRI del reto 2, headless (EGL). Punto de entrada único:
#   scripts/run_reto2_hri.sh --person Laura --video
#   scripts/run_reto2_hri.sh --all --video
#   scripts/run_reto2_hri.sh --rank VIP
# Intérprete: .venv del repo (scripts/setup.sh) o $X2_PYTHON / $PYTHON.
set -eo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"
PY="${X2_PYTHON:-${PYTHON:-$REPO/.venv/bin/python}}"
if [[ ! -x $PY ]]; then
    echo "ERROR: no hay intérprete en $PY." >&2
    echo "Ejecute scripts/setup.sh, o exporte X2_PYTHON=/ruta/al/venv/bin/python." >&2
    exit 1
fi
export MUJOCO_GL="${MUJOCO_GL:-egl}"
export PYTHONPATH="$REPO/src${PYTHONPATH:+:$PYTHONPATH}"
exec "$PY" -m x2_ultra.show.hri.cli "$@"
