#!/usr/bin/env bash
# Prepara el repo en un PC nuevo (o vuelve a comprobarlo en este):
#   1. entorno Python .venv con el paquete x2_ultra (MuJoCo, onnxruntime, PySide6)
#   2. URDF/mallas oficiales de AgiBot en third_party/agibot_x2_urdf (enlace si ya existen, si no git clone)
#   3. MJCF exportado en models/ (para abrirlo también con `python -m mujoco.viewer`)
#
#   scripts/setup.sh            # simulador MuJoCo (lo necesario para x2-sim)
#   scripts/setup.sh --train    # + PyTorch CUDA 12.8 / tensorboard para reentrenar la policy
#   scripts/setup.sh --official # + binarios del simulador oficial de AgiBot (~600 MB, descarga lenta)
set -eo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"
TRAIN=0; OFFICIAL=0
for a in "$@"; do
    case $a in --train) TRAIN=1 ;; --official) OFFICIAL=1 ;; *) echo "opción desconocida: $a"; exit 1 ;; esac
done
# El perfil de ROS 2 exporta PYTHONPATH con paquetes de Jazzy: aquí estorba.
unset PYTHONPATH

if [[ ! -x .venv/bin/python ]]; then
    python3 -m venv .venv
fi
.venv/bin/pip install --upgrade pip wheel >/dev/null
.venv/bin/pip install -e ".[dev]"
if [[ $TRAIN == 1 ]]; then
    .venv/bin/pip install torch --index-url https://download.pytorch.org/whl/cu128
    .venv/bin/pip install rsl-rl-lib tensorboard onnx
fi

# URDF oficial (público, licencia Mulan PSL v2), commit fijado.
mkdir -p third_party
URDF_DST=third_party/agibot_x2_urdf
if [[ ! -e $URDF_DST ]]; then
    if [[ -d ${X2_URDF_ROOT:-$HOME/robots/agibot_x2_urdf}/X2_URDF-v1.4.0 ]]; then
        ln -s "${X2_URDF_ROOT:-$HOME/robots/agibot_x2_urdf}" "$URDF_DST"
    else
        git clone https://github.com/AgibotTech/agibot_x2_urdf.git "$URDF_DST"
        git -C "$URDF_DST" checkout 575cc6b
    fi
fi

# Perro robot AgiBot D1 del reto 2 (URDF + mallas, commit fijado).
scripts/fetch_d1.sh

# Qt ≥ 6.5 necesita libxcb-cursor0 para abrir ventanas en X11. Sin sudo: se baja el .deb y se extrae la librería
# en third_party/syslibs (la app la precarga). Con sudo, mejor: sudo apt install libxcb-cursor0
if ! ldconfig -p | grep -q "libxcb-cursor.so.0" && [[ ! -e third_party/syslibs/libxcb-cursor.so.0 ]]; then
    tmp=$(mktemp -d)
    if (cd "$tmp" && apt download libxcb-cursor0 >/dev/null 2>&1); then
        dpkg -x "$tmp"/libxcb-cursor0_*.deb "$tmp/x"
        mkdir -p third_party/syslibs
        cp -a "$tmp"/x/usr/lib/*/libxcb-cursor.so.0* third_party/syslibs/
        echo "libxcb-cursor0 extraída en third_party/syslibs"
    else
        echo "AVISO: instala libxcb-cursor0 (sudo apt install libxcb-cursor0) para abrir la ventana del simulador"
    fi
    rm -rf "$tmp"
fi

.venv/bin/python -m x2_ultra.model.builder --scene mesa
.venv/bin/python -m x2_ultra.model.builder --scene vacia

if [[ $OFFICIAL == 1 ]]; then
    official_sim/fetch_assets.sh
fi
echo
echo "Listo. Lanza el simulador con:  scripts/run_sim.sh"
