#!/usr/bin/env bash
# Lanza el escenario del reto 2: el X2 Ultra-N en la recepción del N Club, con los ascensores al fondo.
# Acepta las opciones de scripts/run_show.sh (--video, --sin-audio...). Si el render EGL falla: MUJOCO_GL=glfw.
set -eo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
unset PYTHONPATH
exec "$REPO/.venv/bin/python" -m x2_ultra.show.app --escena club "$@"
