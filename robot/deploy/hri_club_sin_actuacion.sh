#!/usr/bin/env bash
# =============================================================================
# hri_club_sin_actuacion.sh — conversación HRI del Club N EN VIVO y SIN ACTUACIÓN
# (micrófono → ASR → FSM + LLM → voz por los altavoces del robot).
#
# El robot NO se mueve de ninguna manera:
#   - nunca se arranca x2-onboard/x2-twin; si alguno está corriendo, el script se niega;
#   - el runner (python -m x2_ultra.hri_runtime.live_robot) aborta si existe X2_REAL o
#     se le pasa --arm, exige HRI_NO_ACTUATION=1 y HRIEGO_NO_ACTUATION=1, veta por
#     import guard los módulos que actúan (x2_ultra.real, backends aimdk/twin/show,
#     gemelo, motor del show, motion_online, mujoco) y usa NullBackend sin movimiento;
#   - la capa ROS (hriego_v2/robot_io.py) solo permite publicar /aima/hal/audio/playback
#     y pedir/soltar el foco de audio; el micrófono es una suscripción;
#   - las intenciones motoras (EMOTE/POINT/WALK_WITH/DISPATCH_D1) se registran como
#     NO EJECUTADAS;
#   - `arrancar` y `estado` auditan en vivo los nodos hriego_*/hri_club_* y paran todo si
#     aparece algo fuera del audio.
#
# Uso (en el robot, desde ~/x2_ultra):
#   deploy/hri_club_sin_actuacion.sh arrancar [--visit VIP|REGULAR|GUEST|STAFF|WORKER] [--sin-voz] [--repetir]
#   deploy/hri_club_sin_actuacion.sh estado    # contenedores + auditoría ROS + últimos turnos
#   deploy/hri_club_sin_actuacion.sh logs      # conversación en vivo (transcript, FSM, LLM, latencias)
#   deploy/hri_club_sin_actuacion.sh parar [--todo]   # para el runner (--todo: también LLM y ASR)
#
#   --sin-voz: no suena nada por los altavoces (NullAudioPlayer, mismos tiempos).
#   --repetir: al despedirse vuelve a admitir la misma persona simulada.
#
# Dependencias: x2-llm (deploy/compose.llm.onboard.yml; se arranca si no está) y el ASR
# en el contenedor x2-asr con el socket en /tmp/x2_hri_sock/hri_asr.sock (si existe
# deploy/compose.asr.yml se arranca; si no, debe estar corriendo ya).
# =============================================================================
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"
CLUB_COMPOSE=(docker compose -f deploy/compose.hri_club.yml)
SOCK_DIR=/tmp/x2_hri_sock
SOCK="$SOCK_DIR/hri_asr.sock"
: "${CTX_SIZE:=8192}"
# LLM: misma selección que deploy/hri_sin_actuacion.sh (GPU si está la imagen de Jetson).
if docker image inspect x2-ultra/llm:jetson >/dev/null 2>&1 && [[ ${LLM_MODE:-auto} != cpu ]]; then
    LLM_MODE=gpu; export LLM_IMAGE=x2-ultra/llm:jetson NGL=99
    LLM_COMPOSE=(docker compose -f llm/docker-compose.yml -f llm/compose.jetson.yml -f deploy/compose.llm.onboard.yml)
else
    LLM_MODE=cpu; export LLM_IMAGE=x2-ultra/llm:cpu NGL=0
    LLM_COMPOSE=(docker compose -f llm/docker-compose.yml -f deploy/compose.llm.onboard.yml)
fi
export CTX_SIZE

need_docker() {
    docker info >/dev/null 2>&1 || {
        echo "dockerd no está corriendo: ejecuta deploy/dockerd_x2.sh" >&2; exit 1; }
}

refuse_actuation() {
    # Misma regla que hri_sin_actuacion.sh: nada capaz de mover el robot puede estar activo.
    local running
    running="$(docker ps --format '{{.Names}} {{.Image}}' | grep -E 'x2-onboard|x2-twin' || true)"
    if [[ -n $running ]]; then
        echo "ABORTO: hay un contenedor de control del robot corriendo:" >&2
        echo "  $running" >&2
        echo "Páralo antes de la prueba sin actuación." >&2
        exit 1
    fi
    if [[ -n ${X2_REAL:-} ]]; then
        echo "ABORTO: X2_REAL está definido en este shell; la prueba es sin actuación." >&2
        exit 1
    fi
}

refuse_hriego() {
    # HRIEGO (x2-hri) también usa altavoces/foco de audio: no mezclar las dos pruebas.
    if docker ps --format '{{.Names}}' | grep -qx x2-hri; then
        echo "ABORTO: HRIEGO (x2-hri) está corriendo; páralo: deploy/hri_sin_actuacion.sh parar" >&2
        exit 1
    fi
}

audit() {
    docker exec -e HRI_AUDIT_REQUIRE=1 x2-hri-club \
        python3 /workspace/x2_ultra/deploy/hri_audit_ros.py
}

running() { docker ps --format '{{.Names}}' | grep -qx "$1"; }

ensure_llm() {
    if ! running x2-llm; then
        echo "→ x2-llm ($LLM_MODE: $LLM_IMAGE, CTX_SIZE=$CTX_SIZE NGL=$NGL)"
        "${LLM_COMPOSE[@]}" up -d --no-build
    fi
    echo -n "→ esperando a llama-server (:8080/health)"
    for _ in $(seq 1 120); do
        curl -sf http://127.0.0.1:8080/health >/dev/null 2>&1 && break
        echo -n "."; sleep 5
    done; echo
    curl -sf http://127.0.0.1:8080/health >/dev/null || {
        echo "El LLM no está listo; mira: docker logs x2-llm" >&2; exit 1; }
}

ensure_asr() {
    mkdir -p "$SOCK_DIR"
    if ! running x2-asr; then
        if [[ -f deploy/compose.asr.yml ]]; then
            echo "→ x2-asr (deploy/compose.asr.yml)"
            docker compose -f deploy/compose.asr.yml up -d --no-build
        else
            echo "ABORTO: el contenedor x2-asr no está corriendo y no hay deploy/compose.asr.yml." >&2
            exit 1
        fi
    fi
    echo -n "→ esperando al socket del ASR ($SOCK)"
    for _ in $(seq 1 90); do
        [[ -S $SOCK ]] && break
        echo -n "."; sleep 2
    done; echo
    [[ -S $SOCK ]] || { echo "El ASR no publicó $SOCK; mira: docker logs x2-asr" >&2; exit 1; }
}

cmd="${1:-}"; shift || true
case "$cmd" in
arrancar)
    export HRI_CLUB_VISIT=REGULAR HRI_CLUB_AUDIO=aimdk HRI_CLUB_REPETIR=0
    while [[ $# -gt 0 ]]; do
        case "$1" in
        --visit) HRI_CLUB_VISIT="${2:?falta el rango}"; shift 2 ;;
        --visit=*) HRI_CLUB_VISIT="${1#*=}"; shift ;;
        --sin-voz) HRI_CLUB_AUDIO=none; shift ;;
        --repetir) HRI_CLUB_REPETIR=1; shift ;;
        --arm*) echo "ABORTO: --arm no existe en la prueba sin actuación." >&2; exit 3 ;;
        *) echo "opción desconocida: $1" >&2; exit 2 ;;
        esac
    done
    HRI_CLUB_VISIT="${HRI_CLUB_VISIT^^}"
    [[ $HRI_CLUB_VISIT =~ ^(VIP|REGULAR|GUEST|STAFF|WORKER)$ ]] || {
        echo "--visit debe ser VIP|REGULAR|GUEST|STAFF|WORKER" >&2; exit 2; }
    need_docker
    refuse_actuation
    refuse_hriego
    ensure_llm
    ensure_asr
    echo "→ comprobación previa (--check-only: seguridad, LLM, ASR, TTS, micrófono)"
    "${CLUB_COMPOSE[@]}" run --rm --no-deps hri_club \
        python3 -u -m x2_ultra.hri_runtime.live_robot --check-only
    echo "→ runner conversacional (visita=$HRI_CLUB_VISIT, audio=$HRI_CLUB_AUDIO)"
    "${CLUB_COMPOSE[@]}" up -d --no-build
    sleep 15
    refuse_actuation
    audit || { echo "Auditoría fallida: paro el runner." >&2; "${CLUB_COMPOSE[@]}" down; exit 1; }
    echo "Listo. Habla cerca del robot. Logs: $0 logs · Parar: $0 parar"
    ;;
estado)
    need_docker
    docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}'
    refuse_actuation
    if running x2-hri-club; then
        audit || { echo "Auditoría fallida: paro el runner." >&2; "${CLUB_COMPOSE[@]}" down; exit 1; }
        last="$(ls -t out_reto2/live/live_*.jsonl 2>/dev/null | head -1 || true)"
        if [[ -n $last ]]; then
            echo "Últimos turnos ($last):"
            tail -n 3 "$last" | cut -c1-400
        fi
    else
        echo "x2-hri-club no está corriendo."
    fi
    if [[ -S $SOCK ]]; then echo "ASR: socket $SOCK presente"; else echo "ASR: sin socket $SOCK"; fi
    ;;
logs)
    "${CLUB_COMPOSE[@]}" logs -f --tail 100
    ;;
parar)
    "${CLUB_COMPOSE[@]}" down || true
    if [[ ${1:-} == --todo ]]; then
        if [[ -f deploy/compose.asr.yml ]]; then docker compose -f deploy/compose.asr.yml down || true; fi
        "${LLM_COMPOSE[@]}" down || true
    fi
    docker ps --format 'table {{.Names}}\t{{.Status}}'
    ;;
*)
    sed -n "2,35p" "$0"; exit 2 ;;
esac
