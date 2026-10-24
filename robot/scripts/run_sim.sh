#!/usr/bin/env bash
# Lanza el simulador interactivo (ventana Qt + MuJoCo). Acepta las opciones de `x2-sim --help`, p. ej.:
#   scripts/run_sim.sh --scene vacia
#   scripts/run_sim.sh --policy agibot
# Si el render EGL falla en tu equipo: MUJOCO_GL=glfw scripts/run_sim.sh
set -eo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
unset PYTHONPATH
exec "$REPO/.venv/bin/python" -m x2_ultra.app.main "$@"
