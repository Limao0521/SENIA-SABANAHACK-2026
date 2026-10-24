#!/usr/bin/env bash
# =============================================================================
# hri_sin_actuacion.sh — prueba de HRIEGO (cara + LLM [+ voz]) SIN ACTUACIÓN.
#
# El robot NO se mueve de ninguna manera:
#   - nunca se arranca x2-onboard (andar/brazos/movimientos); si está corriendo,
#     el script se niega a seguir;
#   - HRIEGO solo valida y registra intenciones motoras (motor_execution=not_executed);
#   - HRIEGO_NO_ACTUATION=1: la capa ROS de HRIEGO (hriego_v2/robot_io.py) rechaza
#     cualquier publicador que no sea /aima/hal/audio/playback y cualquier servicio
#     que no sea el foco de audio;
#   - la visita termina tras el saludo (HRIEGO_STOP_BEFORE=GUIDANCE);
#   - `estado` audita en vivo los publicadores ROS de HRIEGO y para todo si aparece
#     algo fuera del audio.
#
# Uso (en el robot, desde ~/x2_ultra):
#   deploy/hri_sin_actuacion.sh arrancar [--sin-voz]   # LLM + comprobación + HRIEGO
#   deploy/hri_sin_actuacion.sh estado                 # contenedores + auditoría ROS + estado
#   deploy/hri_sin_actuacion.sh logs                   # logs de HRIEGO en vivo
#   deploy/hri_sin_actuacion.sh parar                  # para HRIEGO y el LLM
#   deploy/hri_sin_actuacion.sh probar-voz ["texto"]   # solo voz: Supertonic → altavoces
#
#   --sin-voz: no suena nada por los altavoces (HRIEGO_AUDIO_BACKEND=none); el
#   texto generado se ve en los logs y en la API (ver túnel abajo).
#
# Ver el estado desde el PC (API de HRIEGO, solo en 127.0.0.1 del robot):
#   ssh -L 8765:127.0.0.1:8765 agi@10.0.1.41   y abrir http://127.0.0.1:8765/state (eventos en vivo: /events)
# =============================================================================
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"
HRI_COMPOSE=(docker compose -f deploy/compose.hri.yml)
: "${CTX_SIZE:=8192}"   # memoria justa en el Orin (servicios del robot ~7 GiB)
# LLM con GPU (x2-ultra/llm:jetson, runtime nvidia) si la imagen está; si no, CPU.
if docker image inspect x2-ultra/llm:jetson >/dev/null 2>&1 && [[ ${LLM_MODE:-auto} != cpu ]]; then
    LLM_MODE=gpu; export LLM_IMAGE=x2-ultra/llm:jetson NGL=99 HRIEGO_LLM_ALLOW_CPU=0
    LLM_COMPOSE=(docker compose -f llm/docker-compose.yml -f llm/compose.jetson.yml -f deploy/compose.llm.onboard.yml)
else
    LLM_MODE=cpu; export LLM_IMAGE=x2-ultra/llm:cpu NGL=0 HRIEGO_LLM_ALLOW_CPU=1
    LLM_COMPOSE=(docker compose -f llm/docker-compose.yml -f deploy/compose.llm.onboard.yml)
fi
export CTX_SIZE

need_docker() {
    docker info >/dev/null 2>&1 || {
        echo "dockerd no está corriendo: ejecuta deploy/dockerd_x2.sh" >&2; exit 1; }
}

refuse_actuation() {
    # Ningún contenedor capaz de mover el robot puede estar activo durante la prueba.
    local running
    running="$(docker ps --format '{{.Names}} {{.Image}}' | grep -E 'x2-onboard|x2-twin' || true)"
    if [[ -n $running ]]; then
        echo "ABORTO: hay un contenedor de control del robot corriendo:" >&2
        echo "  $running" >&2
        echo "Páralo antes de la prueba sin actuación." >&2
        exit 1
    fi
}

audit() {
    docker exec x2-hri python3 /workspace/x2_ultra/deploy/hri_audit_ros.py
}

cmd="${1:-}"; shift || true
case "$cmd" in
arrancar)
    audio=aimdk
    [[ ${1:-} == --sin-voz ]] && audio=none
    need_docker
    refuse_actuation
    echo "→ x2-llm ($LLM_MODE: $LLM_IMAGE, CTX_SIZE=$CTX_SIZE NGL=$NGL)"
    "${LLM_COMPOSE[@]}" up -d --no-build
    echo -n "→ esperando a que el LLM cargue el modelo"
    for _ in $(seq 1 120); do
        docker exec x2-llm curl -sf http://127.0.0.1:8000/ready >/dev/null 2>&1 && break
        echo -n "."; sleep 5
    done; echo
    docker exec x2-llm curl -sf http://127.0.0.1:8000/ready >/dev/null || {
        echo "El LLM no está listo; mira: docker logs x2-llm" >&2; exit 1; }
    echo "→ comprobación previa de HRIEGO (--check-only, audio=$audio)"
    HRIEGO_AUDIO_BACKEND=$audio "${HRI_COMPOSE[@]}" run --rm --no-deps hri \
        python3 -m hriego_v2.main --check-only
    echo "→ HRIEGO (audio=$audio)"
    HRIEGO_AUDIO_BACKEND=$audio "${HRI_COMPOSE[@]}" up -d --no-build
    sleep 15
    refuse_actuation
    audit || { echo "Auditoría fallida: paro HRIEGO." >&2; "${HRI_COMPOSE[@]}" down; exit 1; }
    echo "Listo. Logs: deploy/hri_sin_actuacion.sh logs · Parar: deploy/hri_sin_actuacion.sh parar"
    ;;
estado)
    need_docker
    docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}'
    refuse_actuation
    if docker ps --format '{{.Names}}' | grep -qx x2-hri; then
        audit || { echo "Auditoría fallida: paro HRIEGO." >&2; "${HRI_COMPOSE[@]}" down; exit 1; }
        curl -s http://127.0.0.1:8765/state 2>/dev/null | head -c 1500; echo
    fi
    ;;
logs)
    "${HRI_COMPOSE[@]}" logs -f --tail 100
    ;;
probar-voz)
    need_docker
    refuse_actuation
    HRIEGO_AUDIO_BACKEND=aimdk "${HRI_COMPOSE[@]}" run --rm --no-deps hri \
        python3 /workspace/x2_ultra/deploy/hri_probar_voz.py "$@"
    ;;
parar)
    "${HRI_COMPOSE[@]}" down || true
    "${LLM_COMPOSE[@]}" down || true
    docker ps --format 'table {{.Names}}\t{{.Status}}'
    ;;
*)
    sed -n "2,30p" "$0"; exit 2 ;;
esac
