#!/usr/bin/env bash
# =============================================================================
# onboard.sh — lanza los programas del robot real DENTRO del PC2 del X2 Ultra,
# en el contenedor x2-onboard (Jetson Orin NX, docker, red del host).
#
#   deploy/onboard.sh check          # SOLO LECTURA: estado del MC, batería, reloj
#   deploy/onboard.sh andar  [opts]  # marcha WASD/QE                              -> MUEVE EL ROBOT
#   deploy/onboard.sh brazos [opts]  # brazos articulación a articulación (URS)    -> MUEVE EL ROBOT
#   deploy/onboard.sh movimientos    # gestos, LinkCraft y bailes de fábrica       -> MUEVE EL ROBOT
#
# Equivale a scripts/robot_real.sh pero para cuando el contenedor corre EN el
# PC2 (en vez de desde un PC externo por cable). La red es host: el contenedor
# ve al robot por develop0 (10.0.1.41) con descubrimiento local FastDDS.
#
# Antes de usar:
#   1) deploy/build_aarch64.sh                         (construir la imagen)
#   2) docker compose -f deploy/compose.onboard.yml up -d  (arrancar contenedor)
#   3) deploy/onboard.sh check                         (solo lectura)
#
# Ventanas Tk: arms/walk/motions abren ventana Tk. Si el Orin tiene sesión
# gráfica (monitor directo o VNC), DISPLAY=:0 funciona y la ventana se ve. Si
# entras solo por ssh sin X11 forwarding, la ventana no puede abrirse: conecta
# un monitor, usa VNC/x11vnc, o ssh -X. `check` no abre ventana.
# =============================================================================
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
: "${X2_ONBOARD_CONTAINER:=x2-onboard}"

PROGRAM="${1:-}"
case "$PROGRAM" in
    check)       MODULE=check       ;;
    andar)       MODULE=walk        ;;
    brazos)      MODULE=arms        ;;
    movimientos) MODULE=motions     ;;
    -h|--help) sed -n '2,26p' "$0"; exit 0 ;;
    *) sed -n '2,26p' "$0" >&2; exit 2 ;;
esac
shift

# --- 1) contenedor corriendo --------------------------------------------------
# compose up -d lo arranca y lo deja en sleep infinity; los programas corren
# como `docker compose exec` (o `docker exec`) sobre ese contenedor.
if ! docker inspect -f '{{.State.Running}}' "$X2_ONBOARD_CONTAINER" 2>/dev/null | grep -q '^true$'; then
    echo "El contenedor $X2_ONBOARD_CONTAINER no está corriendo." >&2
    echo "  arranca con:  docker compose -f deploy/compose.onboard.yml up -d" >&2
    echo "  o construye:  deploy/build_aarch64.sh" >&2
    exit 1
fi

# --- 2) terminal? -------------------------------------------------------------
tty_flags=()
[[ -t 0 && -t 1 ]] && tty_flags=(-it)

# --- 3) DISPLAY para Tk (andar/brazos/movimientos) ----------------------------
# check no abre ventana; los otros tres sí. Si no hay DISPLAY, avisamos pero no
# fallamos: el usuario puede conectar un monitor / VNC / ssh -X y reintentar.
needs_display=0
case "$MODULE" in
    walk|arms|motions) needs_display=1 ;;
esac
if ((needs_display)) && [[ -z ${DISPLAY:-} ]]; then
    echo "Aviso: $MODULE abre ventana Tk y no hay DISPLAY en el entorno." >&2
    echo "  - Si el Orin tiene monitor: export DISPLAY=:0 y relanza." >&2
    echo "  - Si entras por ssh: ssh -X, o arranca x11vnc/VNC, o usa un monitor directo." >&2
    echo "  - check (solo lectura) no necesita DISPLAY." >&2
fi

# --- 4) exec ------------------------------------------------------------------
# Las options adicionales se pasan tal cual al módulo de Python.
forward=''
(($#)) && printf -v forward ' %q' "$@"

# El entrypoint del contenedor ya sourcea ROS + AimDK + overlay; solo falta
# lanzar python con PYTHONPATH apuntando al repo montado (que es /workspace/x2_ultra
# según compose.onboard.yml).
run="cd /workspace/x2_ultra && \
PYTHONPATH=/workspace/x2_ultra/src:\$PYTHONPATH \
exec python3 -u -m x2_ultra.real.$MODULE$forward"

# OJO: `docker exec` no admite -v; el socket X11 (/tmp/.X11-unix) ya va montado
# desde compose.onboard.yml. Aquí solo se pasa DISPLAY.
if ((${#tty_flags[@]})); then
    exec docker exec "${tty_flags[@]}" \
        ${DISPLAY:+-e DISPLAY="$DISPLAY"} \
        "$X2_ONBOARD_CONTAINER" bash -c "$run"
fi

# Sin terminal: docker exec no reenvía señales. Guardamos el PID del programa
# (bash hace exec en python) y al recibir SIGINT/SIGTERM se lo mandamos como
# SIGINT para que el programa haga su salida ordenada (ceros/HOLD y borrar fuente).
pid_file="/tmp/x2_onboard_$$.pid"
forward_stop() {
    docker exec "$X2_ONBOARD_CONTAINER" \
        bash -c "kill -INT \$(cat $pid_file) 2>/dev/null; true"
}
trap forward_stop INT TERM HUP

docker exec \
    ${DISPLAY:+-e DISPLAY="$DISPLAY"} \
    "$X2_ONBOARD_CONTAINER" bash -c "echo \$\$ > $pid_file; $run" &
child=$!
status=0
while true; do
    wait "$child" && status=0 || status=$?
    kill -0 "$child" 2>/dev/null || break
done
docker exec "$X2_ONBOARD_CONTAINER" rm -f "$pid_file" >/dev/null 2>&1 || true
exit "$status"
