#!/usr/bin/env bash
# Programas para el X2 Ultra REAL por AimDK (cable a develop0 10.0.1.41). Guía: docs/09-robot-real.md.
#
#   scripts/robot_real.sh check  [opts]   # SOLO LECTURA: estado del MC, batería, reloj, si se puede armar
#   scripts/robot_real.sh andar  [opts]   # marcha WASD/QE (locomoción oficial del MC)        -> MUEVE EL ROBOT
#   scripts/robot_real.sh brazos [opts]   # brazos uno a uno, articulación a articulación (URS) -> MUEVE EL ROBOT
#   scripts/robot_real.sh movimientos     # gestos, LinkCraft y bailes de fábrica (--listar: sólo consulta) -> MUEVE
#
# Añadir --gemelo (primer argumento tras el programa) para correrlo dentro del gemelo digital en vez de contra el
# robot (official_sim/twin.sh up/start/stand/suelta antes). Las opciones de cada programa: --help.
#
# andar, brazos y movimientos los lanza una persona que mira el robot, con el mando a mano (siempre puede retomar el control)
# y otra al lado. Antes: batería cargada, parada inalámbrica conectada, cable y `nmcli con up "X2 robot"`, robot
# en STAND_DEFAULT con el mando/app.
set -euo pipefail

PROGRAM="${1:-}"
case "$PROGRAM" in
    check) MODULE=check ;;
    andar) MODULE=walk ;;
    brazos) MODULE=arms ;;
    movimientos) MODULE=motions ;;
    *) sed -n '2,14p' "$0" >&2; exit 2 ;;
esac
shift
TARGET=robot
if [[ "${1:-}" == --gemelo ]]; then TARGET=gemelo; shift; fi

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
: "${X2_SIM_ROOT:=$HOME/robots/x2_simulation}"
: "${X2_IMAGE:=x2-ultra-sim:humble}"
: "${X2_TWIN_CONTAINER:=x2-twin}"
SDK_ROOT="$X2_SIM_ROOT/aimdk"
OVERLAY="$X2_SIM_ROOT/aimdk_overlay/install"     # aimdk_msgs con McBodyPose (scripts/build_aimdk_overlay.sh)
[[ -f "$SDK_ROOT/install/local_setup.bash" ]] || { echo "Falta AimDK compilado en $SDK_ROOT" >&2; exit 2; }
mkdir -p "$REPO/logs/real"
xhost "+si:localuser:$(id -un)" >/dev/null 2>&1 || true
tty_flags=()
[[ -t 0 && -t 1 ]] && tty_flags=(-it)
forward=''
(($#)) && printf -v forward ' %q' "$@"
run="source /opt/ros/humble/setup.bash; source /workspace/aimdk/install/local_setup.bash
[[ -f /workspace/aimdk_overlay/local_setup.bash ]] && source /workspace/aimdk_overlay/local_setup.bash
cd /workspace/x2_ultra && PYTHONPATH=/workspace/x2_ultra/src:\$PYTHONPATH exec python3 -u -m x2_ultra.real.$MODULE$forward"

if [[ $TARGET == gemelo ]]; then
    [[ $(docker inspect -f '{{.State.Running}}' "$X2_TWIN_CONTAINER" 2>/dev/null) == true ]] || {
        echo "El gemelo no está arrancado: official_sim/twin.sh up && official_sim/twin.sh start" >&2; exit 1; }
    xhost +si:localuser:root >/dev/null 2>&1 || true
    exec_args=(-e X2_REAL=gemelo -e PYTHONDONTWRITEBYTECODE=1 -e "DISPLAY=${DISPLAY:-:0}"
               -e RMW_IMPLEMENTATION=rmw_fastrtps_cpp "$X2_TWIN_CONTAINER")
    # Con terminal, Ctrl+C viaja por la pty del contenedor hasta el programa.
    ((${#tty_flags[@]})) && exec docker exec -it "${exec_args[@]}" bash -c "$run"
    # Sin terminal, docker exec no reenvía señales: se guarda el PID del programa (bash hace exec en python) y Ctrl+C/SIGTERM
    # se le mandan como SIGINT, para que haga su salida ordenada (ceros o HOLD y borrar la fuente).
    pid_file="/tmp/x2_real_$$.pid"
    forward_stop() {
        docker exec "$X2_TWIN_CONTAINER" bash -c "kill -INT \$(cat $pid_file) 2>/dev/null; true"
    }
    trap forward_stop INT TERM HUP
    docker exec "${exec_args[@]}" bash -c "echo \$\$ > $pid_file; $run" &
    child=$!
    status=0
    while true; do
        wait "$child" && status=0 || status=$?
        kill -0 "$child" 2>/dev/null || break       # wait vuelve antes al llegar una señal: esperar al final
    done
    docker exec "$X2_TWIN_CONTAINER" rm -f "$pid_file" >/dev/null 2>&1 || true
    exit "$status"
fi

# Robot real: el FastDDS del robot sólo escucha en develop0 (10.0.1.41); por Wi-Fi no hay descubrimiento.
if ! ip -4 -o addr show | grep -q ' inet 10\.0\.1\.'; then
    echo 'No hay cable al robot (ninguna IP 10.0.1.x): nmcli con up "X2 robot"' >&2
    exit 1
fi
args=(run --rm --init "${tty_flags[@]}" --network host --cap-drop ALL --security-opt no-new-privileges
      --read-only --tmpfs /tmp:rw,nosuid,size=64m --user "$(id -u):$(id -g)"
      -e HOME=/tmp -e PYTHONDONTWRITEBYTECODE=1 -e ROS_LOG_DIR=/tmp/ros_logs
      -e RMW_IMPLEMENTATION=rmw_fastrtps_cpp -e ROS_LOCALHOST_ONLY=0 -e X2_REAL=robot
      -e "DISPLAY=${DISPLAY:-:0}" -v /tmp/.X11-unix:/tmp/.X11-unix:ro
      -v "$SDK_ROOT:/workspace/aimdk:ro"
      -v "$REPO:/workspace/x2_ultra:ro"
      -v "$REPO/logs/real:/workspace/x2_ultra/logs/real:rw")
[[ -n "${ROS_DOMAIN_ID:-}" ]] && args+=(-e "ROS_DOMAIN_ID=$ROS_DOMAIN_ID")
[[ -f $OVERLAY/local_setup.bash ]] && args+=(-v "$OVERLAY:/workspace/aimdk_overlay:ro")
exec docker "${args[@]}" "$X2_IMAGE" bash -c "$run"
