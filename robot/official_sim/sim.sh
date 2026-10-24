#!/usr/bin/env bash
# Simulador OFICIAL de AgiBot para el X2: cuerpo MuJoCo (sim_mujoco) + control de movimiento real (mc),
# en un contenedor ROS 2 Humble aislado (sin red), manejado por AimDK v1.1.0 (FastRTPS).
#
#   official_sim/sim.sh image       # construir la imagen (official_sim/docker/Dockerfile)
#   official_sim/sim.sh up          # arrancar el contenedor (crea la escena con pórtico)
#   official_sim/sim.sh sdk         # compilar aimdk_msgs + py_examples (una vez)
#   official_sim/sim.sh start       # ventana MuJoCo + MC en segundo plano (logs/x2_sim.log, logs/x2_mc.log)
#   official_sim/sim.sh stand       # STAND_DEFAULT (reintenta mientras arranca el MC, 3-4 min)
#                                   #   -> después soltar el pórtico: ventana MuJoCo > Equality > gantry
#   official_sim/sim.sh teclado     # ventana de teclado WASD/QE (añade --terminal para modo consola)
#   official_sim/sim.sh autotest    # "mantiene W" 3 s por programa y mide piernas/odometría (sin teclado)
#   official_sim/sim.sh check       # el MC responde a GetMcAction
#   official_sim/sim.sh ex <tool> [args]    # ros2 run py_examples <tool>, p. ej. set_mc_action SD
#   official_sim/sim.sh probe --forward 0.2 --duration 3   # marcha medida y acotada
#   official_sim/sim.sh shell       # shell en el contenedor
#   official_sim/sim.sh stop        # parar MC + MuJoCo;  down = además borrar el contenedor
#
# Los binarios de AgiBot no van en git: official_sim/fetch_assets.sh los deja en $X2_SIM_ROOT
# (por defecto ~/robots/x2_simulation/{aimdk,mc,sim_mujoco}).
set -eo pipefail

: "${X2_SIM_ROOT:=$HOME/robots/x2_simulation}"
: "${X2_IMAGE:=x2-ultra-sim:humble}"
: "${X2_CONTAINER:=x2-ultra-sim}"
: "${X2_ROBOT:=lx2501_3_t2d5}"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$REPO/logs"
ROS="source /opt/ros/humble/setup.bash; export RMW_IMPLEMENTATION=rmw_fastrtps_cpp"
SDK="$ROS; source /workspace/aimdk/install/local_setup.bash"

in_box() { docker exec "$@"; }
running() { [[ $(docker inspect -f '{{.State.Running}}' "$X2_CONTAINER" 2>/dev/null) == true ]]; }
need_up() { running || { echo "El contenedor $X2_CONTAINER no está arrancado: official_sim/sim.sh up" >&2; exit 1; }; }

case "${1:-}" in
image)
    docker build -t "$X2_IMAGE" "$REPO/official_sim/docker"
    ;;
up)
    if running; then
        echo "El contenedor $X2_CONTAINER ya está arrancado."
        exit 0
    fi
    for dir in aimdk mc sim_mujoco; do
        [[ -d $X2_SIM_ROOT/$dir ]] || { echo "Falta $X2_SIM_ROOT/$dir (ejecuta official_sim/fetch_assets.sh)" >&2; exit 1; }
    done
    # El MC escribe su estado en /agibot/data/var: un directorio del usuario sustituye al /agibot del robot.
    mkdir -p "$X2_SIM_ROOT/agibot/data/var" "$LOG_DIR"
    # La escena de AgiBot deja la pelvis libre: mientras el MC arranca (3-4 min en PASSIVE) el robot se cae.
    # Como el arranque colgado del robot real, una copia de la configuración añade un weld "gantry" que
    # sujeta la pelvis; se desactiva en la ventana de MuJoCo (Equality) una vez de pie.
    vendor="$X2_SIM_ROOT/sim_mujoco/configuration/robot/$X2_ROBOT"
    gantry="$X2_SIM_ROOT/configuration_gantry/robot/$X2_ROBOT"
    rm -rf "$X2_SIM_ROOT/configuration_gantry"
    mkdir -p "$gantry/model_info"
    ln -s "../../../sim_mujoco/configuration/robot/$X2_ROBOT/meshes" "$gantry/meshes"
    ln -s "../../../sim_mujoco/configuration/robot/$X2_ROBOT/simulator" "$gantry/simulator"
    cp "$vendor/model_info/x2.xml" "$vendor/model_info/default.yaml" "$gantry/model_info/"
    sed 's|</worldbody>|</worldbody>\n  <equality>\n    <weld name="gantry" body1="pelvis" solref="0.05 1"/>\n  </equality>|' \
        "$vendor/model_info/scene.xml" > "$gantry/model_info/scene.xml"
    xhost +si:localuser:root >/dev/null
    docker rm -f "$X2_CONTAINER" >/dev/null 2>&1 || true
    args=(run -d --name "$X2_CONTAINER" --network none
          -e "DISPLAY=${DISPLAY:-:0}" -e QT_X11_NO_MITSHM=1
          -e RMW_IMPLEMENTATION=rmw_fastrtps_cpp -e ROS_LOCALHOST_ONLY=1 -e X2_SIM_ONLY=1
          -v "$X2_SIM_ROOT/aimdk:/workspace/aimdk:rw"
          -v "$X2_SIM_ROOT/sim_mujoco:/workspace/sim_mujoco:rw"
          -v "$X2_SIM_ROOT/mc:/workspace/mc:rw"
          -v "$X2_SIM_ROOT/configuration_gantry:/workspace/configuration_gantry:rw"
          -v "$X2_SIM_ROOT/agibot:/agibot:rw"
          -v "$REPO:/workspace/x2_ultra:rw"
          -v /tmp/.X11-unix:/tmp/.X11-unix:rw)
    [[ -d /dev/dri ]] && args+=(--device /dev/dri)
    docker "${args[@]}" "$X2_IMAGE" >/dev/null
    # Cachés de los lanzadores (robot a cargar y configuración con pórtico) para que no pregunten.
    in_box "$X2_CONTAINER" bash -c "echo $X2_ROBOT > /workspace/sim_mujoco/bin/sim_robot_name_cache.txt
        echo $X2_ROBOT > /workspace/mc/bin/sim_robot_name_cache.txt
        echo /workspace/configuration_gantry > /workspace/sim_mujoco/bin/sim_configuration_directory_cache.txt"
    echo "Contenedor $X2_CONTAINER arrancado."
    ;;
sdk)
    need_up
    in_box "$X2_CONTAINER" bash -c "$ROS; cd /workspace/aimdk && colcon build --packages-up-to py_examples"
    ;;
start)
    need_up
    "$0" stop >/dev/null 2>&1 || true
    in_box -d "$X2_CONTAINER" bash -c "$ROS; cd /workspace/sim_mujoco/bin && exec ./start_sim.sh > /workspace/x2_ultra/logs/x2_sim.log 2>&1"
    # El MC se suscribe a los estados articulares e IMU que publica el simulador.
    sleep 5
    in_box -d "$X2_CONTAINER" bash -c "$ROS; unset AGIBOT_SOFTWARE_HOME; cd /workspace/mc/bin && exec ./em_run.sh > /workspace/x2_ultra/logs/x2_mc.log 2>&1"
    echo "MuJoCo + MC arrancando; logs en logs/x2_sim.log y logs/x2_mc.log (el MC tarda 3-4 min)"
    ;;
check)
    need_up
    in_box "$X2_CONTAINER" bash -c "$SDK; timeout 20 ros2 run py_examples get_mc_action"
    ;;
stand)
    need_up
    for _ in $(seq 1 50); do
        in_box "$X2_CONTAINER" bash -c "$SDK; timeout 15 ros2 run py_examples set_mc_action SD" >/dev/null 2>&1 || true
        sleep 3
        if in_box "$X2_CONTAINER" bash -c "$SDK; timeout 15 ros2 run py_examples get_mc_action" 2>&1 | grep -q "Mode name: STAND_DEFAULT"; then
            echo "STAND_DEFAULT. Suelta el pórtico: ventana MuJoCo -> Equality -> gantry (desactivar)."
            exit 0
        fi
    done
    echo "El MC no llegó a STAND_DEFAULT; mira logs/x2_mc.log" >&2
    exit 1
    ;;
teclado|keyboard)
    need_up
    shift
    in_box -it -e X2_SIM_ONLY=1 "$X2_CONTAINER" bash -c \
        "$SDK; exec python3 /workspace/x2_ultra/official_sim/keyboard_teleop.py $*"
    ;;
autotest)
    need_up
    shift
    in_box -e X2_SIM_ONLY=1 "$X2_CONTAINER" bash -c \
        "$SDK; cd /workspace/x2_ultra/official_sim && exec python3 teleop_selftest.py ${*:---keys w --seconds 3}"
    ;;
ex)
    need_up
    shift
    tty=(); [[ -t 0 ]] && tty=(-it)
    in_box "${tty[@]}" "$X2_CONTAINER" bash -c "$SDK; ros2 run py_examples $*"
    ;;
probe)
    need_up
    shift
    in_box -e X2_SIM_ONLY=1 "$X2_CONTAINER" bash -c \
        'source /opt/ros/humble/setup.bash; source /workspace/aimdk/install/local_setup.bash; exec python3 /workspace/x2_ultra/official_sim/official_sim_probe.py "$@"' \
        sim-probe "$@"
    ;;
shell)
    need_up
    in_box -it "$X2_CONTAINER" bash
    ;;
stop)
    need_up
    in_box "$X2_CONTAINER" bash -c 'pkill -INT -f em_run.sh; pkill -INT -f aima-mc-app; pkill -INT -f start_sim.sh; pkill -INT -f aima-sim-app; sleep 2; pkill -9 -f "aima-(mc|sim)-app|aimrt_main|iox-roudi" || true'
    ;;
down)
    docker rm -f "$X2_CONTAINER" >/dev/null 2>&1 || true
    xhost -si:localuser:root >/dev/null 2>&1 || true
    ;;
*)
    sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
    exit 1
    ;;
esac
