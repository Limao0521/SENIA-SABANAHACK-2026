#!/usr/bin/env bash
# Lanza el escenario del reto 1: X2 Ultra-N y X2 EDU delante de una pantalla de vídeo. Opciones (--help):
#   scripts/run_show.sh --video presentacion.mp4
#   scripts/run_show.sh --video presentacion.mp4 --sin-audio
# Si el render EGL falla en tu equipo: MUJOCO_GL=glfw scripts/run_show.sh
set -eo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
unset PYTHONPATH
exec "$REPO/.venv/bin/python" -m x2_ultra.show.app "$@"
