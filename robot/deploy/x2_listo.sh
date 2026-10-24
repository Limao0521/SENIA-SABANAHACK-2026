#!/usr/bin/env bash
# =============================================================================
# x2_listo.sh — ¿está el X2 listo para el deploy HRI con actuación? (SOLO LECTURA)
#
# Se ejecuta EN el robot (PC2), desde ~/x2_ultra. No arranca ni para contenedores de control,
# no arma nada y no publica nada. Revisa, en orden:
#   1. código desplegado (rama/commit)          5. CSV de emotes del MC (animation_player.yaml)
#   2. dockerd                                  6. imports en x2-hri (aimdk_msgs, runner, emoji cara)
#   3. imágenes Docker                          7. ROS: tópicos, publicadores ajenos, srv PlayEmoji
#   4. modelos (ASR, LLM, Supertonic, HRI)      8. preflight real del runner (lee el robot, no arma)
#
#   deploy/x2_listo.sh            # todo
#   deploy/x2_listo.sh --sin-ros  # 1-6 (sin cable al robot / sin DDS)
#
# Sale con 0 si no hay ningún FALLO (los AVISO no bloquean).
# =============================================================================
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"
SIN_ROS=0; [[ ${1:-} == --sin-ros ]] && SIN_ROS=1
FALLOS=0
ok()    { printf '  ok     %s\n' "$*"; }
aviso() { printf '  AVISO  %s\n' "$*"; }
fallo() { printf '  FALLO  %s\n' "$*"; FALLOS=$((FALLOS + 1)); }
titulo(){ printf '\n== %s\n' "$*"; }

titulo "1. Código"
if [[ -f DEPLOYED ]]; then ok "$(tr '\n' ' ' < DEPLOYED)"; else aviso "sin archivo DEPLOYED (¿copiado a mano?)"; fi
[[ -f src/x2_ultra/hri_runtime/live_robot_actuado.py ]] && ok "runner con actuación presente" \
    || fallo "falta src/x2_ultra/hri_runtime/live_robot_actuado.py (despliega sabanahack4.0-deploy)"

titulo "2. dockerd"
if docker info >/dev/null 2>&1; then ok "dockerd $(docker info --format '{{.ServerVersion}}')"
else fallo "dockerd no corre: deploy/dockerd_x2.sh"; fi

titulo "3. Imágenes"
img() {  # img <imagen> <cómo obtenerla> [opcional]
    if docker image inspect "$1" >/dev/null 2>&1; then ok "$1"
    elif [[ ${3:-} == opcional ]]; then aviso "$1 no está ($2)"
    else fallo "$1 no está → $2"; fi
}
img x2-onboard:latest "deploy/build_aarch64.sh"
img x2-hri:latest "docker build -f deploy/Dockerfile.hri -t x2-hri:latest ."
if docker image inspect x2-ultra/llm:jetson >/dev/null 2>&1; then ok "x2-ultra/llm:jetson (LLM en GPU)"
else img x2-ultra/llm:cpu "ver llm/README.md (build de la imagen del LLM)"; fi
img dustynv/faster-whisper:r36.4.0-cu128-24.04 "docker pull (necesita internet) o docker load desde un .tar"

titulo "4. Modelos y datos"
fichero() {  # fichero <ruta> <qué es>
    if [[ -e $1 ]]; then ok "$2: $1 ($(du -sh "$1" 2>/dev/null | cut -f1))"
    else fallo "$2: falta $1 → deploy/x2_deploy.sh modelos (desde el PC)"; fi
}
fichero models/faster-whisper-large-v3-turbo/model.bin "ASR"
fichero llm/models/Qwen3-4B-Q4_K_M.gguf "LLM"
fichero experiments/hriego_v2_tts_eval_20261021/model "Supertonic"
fichero assets/hri/HRIEGO_gesture/contract/catalog.json "catálogo de emotes"
fichero assets/hri/HRIEGO_gesture/twin/point_targets.json "objetivos de señalamiento"
free_mb="$(free -m 2>/dev/null | awk '/^Mem:/{print $7}')"
[[ -n $free_mb ]] && { (( free_mb > 6000 )) && ok "RAM disponible ${free_mb} MB" \
    || aviso "RAM disponible ${free_mb} MB (LLM+ASR+runner piden ~6 GB)"; }

titulo "5. CSV de emotes (mc_param de AgiBot)"
mc=""
if [[ -n ${MC_CLASSIC_DIR:-} && -f ${MC_CLASSIC_DIR}/animation_player.yaml ]]; then mc="$MC_CLASSIC_DIR"
elif [[ -f assets/hri/mc_classic/animation_player.yaml ]]; then mc="$REPO/assets/hri/mc_classic"
else
    y="$(find /agibot -path '*classic/animation_player.yaml' 2>/dev/null | head -1)"
    [[ -n $y ]] && mc="$(dirname "$y")"
fi
if [[ -n $mc ]]; then ok "$mc ($(find "$mc" -name '*.csv' | wc -l) CSV)"
else aviso "sin animation_player.yaml: se usan los emotes cinemáticos propios (backends/emotes.py); para los de AgiBot copia assets/hri/mc_classic"; fi

if docker info >/dev/null 2>&1 && docker image inspect x2-hri:latest >/dev/null 2>&1; then
    titulo "6. Imports en x2-hri"
    if docker run --rm --network none -v "$REPO:/workspace/x2_ultra:ro" -w /workspace/x2_ultra \
        -e PYTHONPATH=/workspace/x2_ultra/src:/workspace/x2_ultra x2-hri:latest python3 -c "
import sys
sys.modules['mujoco'] = None          # a bordo no hay mujoco: debe funcionar igual
import rclpy, supertonic, soundfile
from aimdk_msgs.msg import AudioPlayback
from aimdk_msgs.srv import SetMcInputSource, SetMcAction
from x2_ultra.real.link import RobotLink
from x2_ultra.show.hri.data import VisitStore
from x2_ultra.show.hri.motion import min_jerk, CsvPreset
from x2_ultra.hri_runtime import live_robot_actuado
from x2_ultra.hri_runtime.backends.aimdk import AimdkBackend
print('imports OK; objetivos de señalamiento:', sorted(VisitStore().point_targets()))
" 2>&1 | sed 's/^/         /'; then ok "imports del runner con actuación"
    else fallo "imports en x2-hri (mira el error de arriba)"; fi
    if docker run --rm --network none x2-hri:latest \
        python3 -c "from aimdk_msgs.srv import PlayEmoji" >/dev/null 2>&1; then
        ok "aimdk_msgs.srv.PlayEmoji (expresiones de la cara)"
    else
        aviso "sin aimdk_msgs.srv.PlayEmoji: la cara no cambiará (HRI_EXPRESIONES se desactiva sola)"
    fi
fi

if (( SIN_ROS == 0 )); then
    titulo "7. ROS (solo lectura)"
    for c in x2-onboard x2-twin x2-hri x2-hri-club x2-hri-actuado; do
        docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "$c" && fallo "$c está corriendo (páralo antes de armar)"
    done
    if docker image inspect x2-hri:latest >/dev/null 2>&1; then
        docker run --rm --network host --ipc host -v "$REPO:/workspace/x2_ultra:ro" \
            -e RMW_IMPLEMENTATION=rmw_fastrtps_cpp -e ROS_LOCALHOST_ONLY=0 -e ROS_LOG_DIR=/tmp/ros_logs \
            -e FASTRTPS_DEFAULT_PROFILES_FILE=/agibot/software/entry/cfg/ros_dds_configuration.xml \
            -v /agibot/software/entry/cfg/ros_dds_configuration.xml:/agibot/software/entry/cfg/ros_dds_configuration.xml:ro \
            x2-hri:latest python3 /workspace/x2_ultra/deploy/x2_ros_check.py 2>&1 | sed 's/^/  /'
        (( PIPESTATUS[0] == 0 )) || FALLOS=$((FALLOS + 1))

        titulo "8. Preflight real del runner (lee el robot; NO arma, NO publica órdenes)"
        if [[ -n $mc ]]; then export MC_CLASSIC_DIR="$mc"; fi
        X2_REAL=robot HRI_ACTUATION=1 HRI_ETAPA=lenta HRI_ARM_FLAG=--arm \
            docker compose -f deploy/compose.hri_club_actuado.yml run --rm --no-deps hri_club_actuado sh -c \
            "exec python3 -u -m x2_ultra.hri_runtime.live_robot_actuado --etapa lenta --arm --solo-preflight --log-dir ''" \
            2>&1 | sed 's/^/  /'
        (( PIPESTATUS[0] == 0 )) || FALLOS=$((FALLOS + 1))
    fi
fi

printf '\n'
if (( FALLOS == 0 )); then echo "LISTO: sin fallos. Siguiente: deploy/hri_club_con_actuacion.sh seco --visit STAFF"
else echo "NO LISTO: $FALLOS fallo(s). Corrige lo marcado como FALLO."; fi
exit $(( FALLOS > 0 ))
