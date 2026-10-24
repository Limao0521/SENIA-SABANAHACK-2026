#!/usr/bin/env bash
# Gemelo digital del X2 Ultra-N: el MC OFICIAL de AgiBot (mc x86) controlando NUESTRO MuJoCo (Ultra-N + OmniHand),
# que hace de HAL del robot real con sus mismos topics y servicios, más cámaras, LiDAR, audio/TTS y el
# UPPERBODY_REMOTE_SPLIT emulado (el MC v1.0.0 no lo trae). Todo en un contenedor ROS 2 Humble aislado (sin red).
#
#   official_sim/twin.sh image        # construir x2-twin:humble (encima de x2-ultra-sim:humble)
#   official_sim/twin.sh up           # arrancar el contenedor (GPU, pantalla y audio del PC)
#   official_sim/twin.sh start [--mc-profile P0|P1|P2] [opts]
#                                     # gemelo (logs/twin.log) y MC (logs/twin_mc.log)
#                                     # P0=original, P1=cpgtelecon sin transición, P2=cpgwalk
#                                     # otros opts de x2_ultra.twin.run: --escena vacia --camaras real ...
#   official_sim/twin.sh profile-info # perfil privado, configuración generada y modo MC actual
#   official_sim/twin.sh stand        # STAND_DEFAULT con el pórtico puesto (reintenta mientras arranca el MC)
#   official_sim/twin.sh suelta       # quitar el pórtico (portico = volver a ponerlo; reset = reiniciar)
#   official_sim/twin.sh gui          # la app de control (WASD/QE, IK, poses, manos) por la interfaz de AimDK
#   official_sim/twin.sh teclado      # ventana de teclado WASD/QE (la misma del simulador oficial)
#   official_sim/twin.sh urs-test     # URS emulado: brazos al frente + manos en agarre + marcha oficial, medido
#   official_sim/twin.sh test         # pruebas que necesitan ROS 2 (tests_twin/) dentro del contenedor
#   official_sim/twin.sh ex <tool> [args]   # ros2 run py_examples <tool>, p. ej. set_mc_action URS
#   official_sim/twin.sh topics       # topics y servicios que ve un programa AimDK
#   official_sim/twin.sh shell | stop | down
set -eo pipefail

: "${X2_SIM_ROOT:=$HOME/robots/x2_simulation}"
: "${X2_URDF_ROOT:=$HOME/robots/agibot_x2_urdf}"
: "${X2_TWIN_IMAGE:=x2-twin:humble}"
: "${X2_TWIN_CONTAINER:=x2-twin}"
: "${X2_ROBOT:=lx2501_3_t2d5}"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$REPO/logs"
ROS="source /opt/ros/humble/setup.bash; export RMW_IMPLEMENTATION=rmw_fastrtps_cpp"
SDK="$ROS; source /workspace/aimdk/install/local_setup.bash"
# El MC va en su propio dominio DDS; el gemelo publica la HAL también allí y hace de puente con el público
# (x2_ultra.twin.mc_shim): así puede atender SetMcAction/GetMcAction y emular URS y HEAD_ONLY.
MC_DOMAIN_ID=77
MC_PROFILE_RUNTIME=/workspace/x2_ultra/runs/mc_profiles/current

in_box() { docker exec "$@"; }
running() { [[ $(docker inspect -f '{{.State.Running}}' "$X2_TWIN_CONTAINER" 2>/dev/null) == true ]]; }
need_up() { running || { echo "El contenedor $X2_TWIN_CONTAINER no está arrancado: official_sim/twin.sh up" >&2; exit 1; }; }
sim_option() { in_box "$X2_TWIN_CONTAINER" bash -c "$ROS; ros2 topic pub --once /aima/sim/option/command std_msgs/msg/String \"{data: '$1'}\"" >/dev/null; }

case "${1:-}" in
image)
    docker image inspect x2-ultra-sim:humble >/dev/null 2>&1 || "$REPO/official_sim/sim.sh" image
    docker build -t "$X2_TWIN_IMAGE" -f "$REPO/official_sim/docker/Dockerfile.twin" "$REPO/official_sim/docker"
    ;;
up)
    if running; then echo "El contenedor $X2_TWIN_CONTAINER ya está arrancado."; exit 0; fi
    for dir in aimdk mc; do
        [[ -d $X2_SIM_ROOT/$dir ]] || { echo "Falta $X2_SIM_ROOT/$dir (official_sim/fetch_assets.sh)" >&2; exit 1; }
    done
    [[ -d $X2_URDF_ROOT/X2_URDF-v1.4.0 ]] || { echo "Falta el URDF en $X2_URDF_ROOT (scripts/setup.sh)" >&2; exit 1; }
    mkdir -p "$X2_SIM_ROOT/agibot/data/var" "$LOG_DIR"
    xhost +si:localuser:root >/dev/null 2>&1 || true
    docker rm -f "$X2_TWIN_CONTAINER" >/dev/null 2>&1 || true
    # --init: un init mínimo como PID 1 que recoge los procesos terminados (con "sleep" quedaban zombis).
    args=(run -d --init --name "$X2_TWIN_CONTAINER" --network none --gpus all
          -e "DISPLAY=${DISPLAY:-:0}" -e QT_X11_NO_MITSHM=1
          -e RMW_IMPLEMENTATION=rmw_fastrtps_cpp -e ROS_LOCALHOST_ONLY=1 -e X2_SIM_ONLY=1
          -v "$X2_SIM_ROOT/aimdk:/workspace/aimdk:rw"
          -v "$X2_SIM_ROOT/mc:/workspace/mc:ro"
          -v "$X2_SIM_ROOT/agibot:/agibot:rw"
          -v "$X2_URDF_ROOT:/workspace/agibot_x2_urdf:ro"
          -v "$REPO:/workspace/x2_ultra:rw"
          -v /tmp/.X11-unix:/tmp/.X11-unix:rw)
    # Audio: el socket de PulseAudio/PipeWire del usuario (altavoces y micrófono del PC).
    pulse="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/pulse"
    if [[ -S $pulse/native ]]; then
        args+=(-v "$pulse:/run/pulse:rw" -e PULSE_SERVER=unix:/run/pulse/native)
        [[ -f $HOME/.config/pulse/cookie ]] && args+=(-v "$HOME/.config/pulse/cookie:/root/.config/pulse/cookie:ro")
    fi
    docker "${args[@]}" "$X2_TWIN_IMAGE" >/dev/null
    echo "Contenedor $X2_TWIN_CONTAINER arrancado."
    ;;
start)
    need_up
    shift
    mc_profile=P0
    twin_args=()
    while (($#)); do
        case "$1" in
        --mc-profile)
            (($# >= 2)) || { echo "Falta el valor de --mc-profile" >&2; exit 2; }
            mc_profile=$2; shift 2 ;;
        --mc-profile=*) mc_profile=${1#*=}; shift ;;
        *) twin_args+=("$1"); shift ;;
        esac
    done
    case "$mc_profile" in
        P0|P1|P2|baseline|stand_cpgtelecon|cpgwalk) ;;
        *) echo "Perfil MC desconocido: $mc_profile (P0, P1 o P2)" >&2; exit 2 ;;
    esac
    "$0" stop >/dev/null 2>&1 || true
    in_box "$X2_TWIN_CONTAINER" python3 /workspace/x2_ultra/official_sim/mc_profile.py prepare \
        --vendor /workspace/mc --runtime "$MC_PROFILE_RUNTIME" --robot "$X2_ROBOT" \
        --profile "$mc_profile" > "$LOG_DIR/twin_mc_profile.json"
    twin_command="$SDK; cd /workspace/x2_ultra && exec python3 -m x2_ultra.twin.run"
    for arg in "${twin_args[@]}"; do
        printf -v quoted_arg '%q' "$arg"
        twin_command+=" $quoted_arg"
    done
    twin_command+=" > /workspace/x2_ultra/logs/twin.log 2>&1"
    in_box -d "$X2_TWIN_CONTAINER" bash -c "$twin_command"
    # El MC espera a los estados de pierna y a la IMU de pelvis (wait_ready): primero el gemelo.
    twin_ready=false
    for _ in $(seq 1 60); do
        if in_box "$X2_TWIN_CONTAINER" bash -c "$ROS; timeout 5 ros2 topic echo --once /aima/hal/imu/torso/state" >/dev/null 2>&1; then
            twin_ready=true
            break
        fi
        if ! in_box "$X2_TWIN_CONTAINER" pgrep -f '^python3 -m x2_ultra[.]twin[.]run' >/dev/null 2>&1; then
            echo "El gemelo terminó antes de publicar IMU; mira logs/twin.log" >&2
            exit 1
        fi
        sleep 1
    done
    if [[ $twin_ready != true ]]; then
        echo "El gemelo no publicó IMU; no se inicia el MC. Mira logs/twin.log" >&2
        exit 1
    fi
    in_box -d -e ROS_DOMAIN_ID=$MC_DOMAIN_ID "$X2_TWIN_CONTAINER" bash -c "$ROS; unset AGIBOT_SOFTWARE_HOME; cd $MC_PROFILE_RUNTIME/mc/bin && exec ./em_run.sh > /workspace/x2_ultra/logs/twin_mc.log 2>&1"
    echo "Gemelo + MC perfil $mc_profile arrancando; logs en logs/twin.log y logs/twin_mc.log (el MC tarda 1-4 min)"
    ;;
profile-info)
    need_up
    in_box "$X2_TWIN_CONTAINER" python3 /workspace/x2_ultra/official_sim/mc_profile.py inspect \
        --runtime "$MC_PROFILE_RUNTIME" --log /workspace/x2_ultra/logs/twin_mc.log --require-loaded
    if ! in_box "$X2_TWIN_CONTAINER" pgrep -f '^./aima-mc-app-main --cfg_file_path=' >/dev/null 2>&1; then
        echo "MC detenido; se muestra el perfil del último arranque."
        exit 0
    fi
    echo "Modo MC actual (GetMcAction):"
    in_box "$X2_TWIN_CONTAINER" bash -c "$SDK; timeout 5 ros2 run py_examples get_mc_action" || true
    ;;
stand)
    need_up
    stand_deadline=$((SECONDS + 300))
    while ((SECONDS < stand_deadline)); do
        in_box "$X2_TWIN_CONTAINER" bash -c "$SDK; timeout 15 ros2 run py_examples set_mc_action SD" >/dev/null 2>&1 || true
        sleep 3
        if in_box "$X2_TWIN_CONTAINER" bash -c "$SDK; timeout 15 ros2 run py_examples get_mc_action" 2>&1 | grep -q "Mode name: STAND_DEFAULT"; then
            echo "STAND_DEFAULT. Quita el pórtico con: official_sim/twin.sh suelta"
            exit 0
        fi
    done
    echo "El MC no llegó a STAND_DEFAULT; mira logs/twin_mc.log" >&2
    exit 1
    ;;
suelta) need_up; sim_option "gantry off"; echo "Pórtico quitado." ;;
portico) need_up; sim_option "gantry on"; echo "Pórtico puesto." ;;
reset) need_up; sim_option "reset"; echo "Gemelo reiniciado (con pórtico)." ;;
gui)
    need_up
    shift
    in_box -e QT_X11_NO_MITSHM=1 "$X2_TWIN_CONTAINER" bash -c "$SDK; cd /workspace/x2_ultra && exec python3 -m x2_ultra.app.main --gemelo $*"
    ;;
teclado|keyboard)
    need_up
    shift
    in_box -it -e X2_SIM_ONLY=1 "$X2_TWIN_CONTAINER" bash -c "$SDK; exec python3 /workspace/x2_ultra/official_sim/keyboard_teleop.py $*"
    ;;
test)
    need_up
    in_box "$X2_TWIN_CONTAINER" bash -c "$SDK; cd /workspace/x2_ultra && python3 -m pytest -q -p no:cacheprovider tests_twin"
    ;;
urs-test)
    need_up
    shift
    in_box -e X2_SIM_ONLY=1 "$X2_TWIN_CONTAINER" bash -c "$SDK; cd /workspace/x2_ultra/official_sim && exec python3 urs_selftest.py $*"
    ;;
ex)
    need_up
    shift
    tty=(); [[ -t 0 ]] && tty=(-it)
    in_box "${tty[@]}" "$X2_TWIN_CONTAINER" bash -c "$SDK; ros2 run py_examples $*"
    ;;
topics)
    need_up
    in_box "$X2_TWIN_CONTAINER" bash -c "$SDK; ros2 topic list -t; echo; ros2 service list -t | grep aimdk"
    ;;
shell)
    need_up
    in_box -it "$X2_TWIN_CONTAINER" bash
    ;;
stop)
    need_up
    in_box "$X2_TWIN_CONTAINER" bash /workspace/x2_ultra/official_sim/twin_stop.sh
    ;;
down)
    docker rm -f "$X2_TWIN_CONTAINER" >/dev/null 2>&1 || true
    ;;
*)
    sed -n '2,17p' "$0" | sed 's/^# \{0,1\}//'
    exit 1
    ;;
esac
