#!/usr/bin/env bash
# =============================================================================
# hri_club_con_actuacion.sh — conversación HRI del Club N EN VIVO y CON ACTUACIÓN
# (micrófono → ASR → FSM + LLM → voz) + EMOTE/POINT en el X2 real (AimdkBackend).
#
# Sin grúa: las etapas van en orden y no se saltan (docs/reto-2/DEPLOY_X2_REAL.md §6):
#   1. seco      AimdkBackend en dry-run: toda la cadena, el robot no recibe nada.
#   2. lenta     armado a 5/10 rad/s, cintura bloqueada. Primero `prueba`, luego `arrancar`.
#   3. completa  límites del spec (10/20 rad/s).
# Nunca camina (walk_to/velocidad rechazados), sin co-speech SONIC (no corre en el Orin),
# D1 solo registro. Interlock de caída siempre activo; watchdog ⇒ e-stop.
#
# Uso (en el robot, desde ~/x2_ultra; e-stop inalámbrico en mano y ≥2 m libres):
#   deploy/hri_club_con_actuacion.sh seco [--visit VIP|REGULAR|GUEST|STAFF|WORKER] [--sin-voz]
#   deploy/hri_club_con_actuacion.sh prueba --etapa lenta|completa     # READY + 1 emote + 1 señal (primer plano)
#   deploy/hri_club_con_actuacion.sh arrancar --etapa lenta|completa [--visit X] [--sin-voz] [--repetir]
#   deploy/hri_club_con_actuacion.sh arrancar --etapa lenta --identidad --repetir   # cara + QR, sin rol fijo
#   deploy/hri_club_con_actuacion.sh estado | logs
#   deploy/hri_club_con_actuacion.sh parar [--todo]   # SIGTERM ⇒ vel 0, DELETE fuente URS, STAND
#
# La parada de emergencia de verdad es el e-stop inalámbrico / el mando (prioridad 80 > 65).
# Antes de armar, el robot debe estar de pie en STAND_DEFAULT (mando/app) y sin x2-onboard,
# x2-twin, HRIEGO (x2-hri) ni el runner sin actuación (x2-hri-club) corriendo.
# =============================================================================
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"
COMPOSE=(docker compose -f deploy/compose.hri_club_actuado.yml)
SVC=hri_club_actuado
NAME=x2-hri-actuado
SOCK_DIR=/tmp/x2_hri_sock
SOCK="$SOCK_DIR/hri_asr.sock"
: "${CTX_SIZE:=8192}"
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

running() { docker ps --format '{{.Names}}' | grep -qx "$1"; }

refuse_conflicts() {
    # Otro publicador de brazos/velocidad o del altavoz: no se arma con ellos vivos.
    local c
    for c in x2-onboard x2-twin x2-hri x2-hri-club; do
        if running "$c"; then
            echo "ABORTO: $c está corriendo; páralo antes (publica al robot o usa el altavoz)." >&2
            exit 1
        fi
    done
}

find_mc_classic() {
    # CSV de emotes: copia del repo (scripts/fetch_hri_assets.sh) o el mc_param del propio robot.
    if [[ -n ${MC_CLASSIC_DIR:-} ]]; then echo "$MC_CLASSIC_DIR"; return; fi
    if [[ -f assets/hri/mc_classic/animation_player.yaml ]]; then
        echo "$REPO/assets/hri/mc_classic"; return; fi
    local y
    y="$(find /agibot -path '*lx2501_3_t2d5/classic/animation_player.yaml' 2>/dev/null | head -1 || true)"
    [[ -z $y ]] && y="$(find /agibot -path '*classic/animation_player.yaml' 2>/dev/null | head -1 || true)"
    if [[ -n $y ]]; then dirname "$y"; return; fi
    echo ""
}

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
            echo "ABORTO: x2-asr no está corriendo y no hay deploy/compose.asr.yml." >&2; exit 1
        fi
    fi
    echo -n "→ esperando al socket del ASR ($SOCK)"
    for _ in $(seq 1 90); do
        [[ -S $SOCK ]] && break
        echo -n "."; sleep 2
    done; echo
    [[ -S $SOCK ]] || { echo "El ASR no publicó $SOCK; mira: docker logs x2-asr" >&2; exit 1; }
}

confirm_arm() {
    local etapa="$1"
    cat <<EOF

  *** ETAPA $etapa: EL ROBOT VA A MOVER LOS BRAZOS ***
  [ ] e-stop inalámbrico probado y en la mano de una persona dedicada
  [ ] mando/app accesible (prioridad 80 gana a la nuestra, 65)
  [ ] robot de pie en STAND_DEFAULT, batería >= 30 %, >= 2 m libres alrededor
  [ ] nadie al alcance de los brazos

EOF
    if [[ ${HRI_CONFIRMAR:-} == ARMAR ]]; then echo "(confirmado por HRI_CONFIRMAR=ARMAR)"; return; fi
    local r
    # Vacía lo tecleado mientras esperaba (flechas, Enter…): si no, se pega delante de ARMAR.
    while read -r -t 0.05 -n 4096 _ 2>/dev/null; do :; done
    read -r -p "Escribe ARMAR para continuar: " r
    E=$'\e'; r="$(printf '%s' "$r" | sed "s/${E}\[[0-9;]*[A-Za-z~]//g; s/${E}O[A-Za-z]//g")"   # secuencias de teclas (flechas…)
    r="${r//[^A-Za-z]/}"                      # espacios y restos
    [[ ${r^^} == ARMAR ]] || { echo "Cancelado (se leyó: «$r»)."; exit 1; }
}

parse_session_opts() {
    ETAPA="" ; export HRI_CLUB_VISIT=REGULAR HRI_CLUB_AUDIO=aimdk HRI_CLUB_REPETIR=0
    export HRI_IDENTITY_REAL=0 HRI_CAMARA_FLAG=""
    while [[ $# -gt 0 ]]; do
        case "$1" in
        --etapa) ETAPA="${2:?falta la etapa}"; shift 2 ;;
        --etapa=*) ETAPA="${1#*=}"; shift ;;
        --visit) HRI_CLUB_VISIT="${2:?falta el rango}"; shift 2 ;;
        --visit=*) HRI_CLUB_VISIT="${1#*=}"; shift ;;
        --sin-voz) HRI_CLUB_AUDIO=none; shift ;;
        --repetir) HRI_CLUB_REPETIR=1; shift ;;
        --identidad) HRI_IDENTITY_REAL=1; HRI_CAMARA_FLAG=--camara; shift ;;
        *) echo "opción desconocida: $1" >&2; exit 2 ;;
        esac
    done
    HRI_CLUB_VISIT="${HRI_CLUB_VISIT^^}"
    [[ $HRI_CLUB_VISIT =~ ^(VIP|REGULAR|GUEST|STAFF|WORKER)$ ]] || {
        echo "--visit debe ser VIP|REGULAR|GUEST|STAFF|WORKER" >&2; exit 2; }
}

ensure_pylibs() {
    # zxing-cpp (decodificador de QR robusto): la rueda arm64 va versionada; se descomprime una vez.
    local whl
    whl="$(ls third_party/wheels/zxing_cpp-*aarch64*.whl 2>/dev/null | head -1 || true)"
    if [[ -n $whl && ! -d third_party/pylibs_aarch64/zxingcpp ]]; then
        mkdir -p third_party/pylibs_aarch64
        python3 -m zipfile -e "$whl" third_party/pylibs_aarch64/ && echo "→ zxing-cpp listo (QR)"
    fi
}

setup_env() {
    # $1 = seco|lenta|completa
    ensure_pylibs
    export HRI_ETAPA="$1"
    if [[ $1 == seco ]]; then
        export X2_REAL="" HRI_ACTUATION=0 HRI_ARM_FLAG=""
    else
        export X2_REAL=robot HRI_ACTUATION=1 HRI_ARM_FLAG=--arm
    fi
    [[ $1 == completa ]] || export HRI_WAIST_MODE=off
    MC_CLASSIC_DIR="$(find_mc_classic)"
    if [[ -z $MC_CLASSIC_DIR ]]; then
        echo "AVISO: sin CSV de emotes (animation_player.yaml); solo señalamiento y READY." >&2
        echo "       Copia los assets con scripts/fetch_hri_assets.sh o define MC_CLASSIC_DIR." >&2
        MC_CLASSIC_DIR="$REPO/assets/hri/mc_classic"; mkdir -p "$MC_CLASSIC_DIR"
    else
        echo "→ CSV de emotes: $MC_CLASSIC_DIR"
    fi
    export MC_CLASSIC_DIR
}

cmd="${1:-}"; shift || true
case "$cmd" in
seco)
    parse_session_opts "$@"
    [[ -z $ETAPA || $ETAPA == seco ]] || { echo "seco no lleva --etapa $ETAPA" >&2; exit 2; }
    need_docker; refuse_conflicts; ensure_llm; ensure_asr
    setup_env seco
    echo "→ etapa SECO (dry-run, nada se publica) visita=$HRI_CLUB_VISIT identidad_real=$HRI_IDENTITY_REAL audio=$HRI_CLUB_AUDIO"
    "${COMPOSE[@]}" up -d --no-build
    echo "Listo. Logs: $0 logs (busca [motor] y el archivo *_eventos.jsonl al parar) · Parar: $0 parar"
    ;;
prueba)
    parse_session_opts "$@"
    [[ $ETAPA =~ ^(lenta|completa)$ ]] || { echo "prueba exige --etapa lenta|completa" >&2; exit 2; }
    need_docker; refuse_conflicts
    setup_env "$ETAPA"
    confirm_arm "$ETAPA"
    echo "→ prueba armada ($ETAPA): preflight → URS → READY ${HRI_READY_TEST_S:-5} s → emote 3001/11 → señal LEFT → STAND"
    "${COMPOSE[@]}" run --rm --no-deps "$SVC" sh -c \
        "exec python3 -u -m x2_ultra.hri_runtime.live_robot_actuado --etapa $ETAPA --arm --solo-prueba"
    ;;
arrancar)
    parse_session_opts "$@"
    [[ $ETAPA =~ ^(lenta|completa)$ ]] || {
        echo "arrancar exige --etapa lenta|completa (para dry-run usa: $0 seco)" >&2; exit 2; }
    need_docker; refuse_conflicts; ensure_llm; ensure_asr
    setup_env "$ETAPA"
    confirm_arm "$ETAPA"
    if [[ $HRI_IDENTITY_REAL == 1 ]]; then quien="identidad real (cara + QR)"; else quien="visita=$HRI_CLUB_VISIT"; fi
    echo "→ conversación con actuación ($ETAPA) $quien audio=$HRI_CLUB_AUDIO repetir=$HRI_CLUB_REPETIR"
    "${COMPOSE[@]}" up -d --no-build
    echo "Listo. Logs: $0 logs · Parar (vel 0 + STAND): $0 parar · Emergencia: e-stop inalámbrico"
    ;;
estado)
    need_docker
    docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}'
    if running "$NAME"; then
        docker exec "$NAME" sh -c 'echo "etapa=$HRI_ETAPA X2_REAL=${X2_REAL:-} arm=${HRI_ARM_FLAG:-no}"'
        last="$(ls -t out_reto2/live/live_*.jsonl 2>/dev/null | head -1 || true)"
        if [[ -n $last ]]; then echo "Últimos turnos ($last):"; tail -n 3 "$last" | cut -c1-400; fi
    else
        echo "$NAME no está corriendo."
    fi
    ev="$(ls -t out_reto2/live/actuado_*_eventos.jsonl 2>/dev/null | head -1 || true)"
    [[ -n $ev ]] && echo "Últimos eventos del backend: $ev ($(grep -c limiter_hit "$ev" || true) limiter_hit," \
        "$(grep -c fall_interlock "$ev" || true) fall_interlock)"
    ;;
logs)
    "${COMPOSE[@]}" logs -f --tail 100
    ;;
parar)
    "${COMPOSE[@]}" stop -t 20 || true      # SIGTERM ⇒ stop(): vel 0, DELETE fuente, STAND
    "${COMPOSE[@]}" down || true
    if [[ ${1:-} == --todo ]]; then
        if [[ -f deploy/compose.asr.yml ]]; then docker compose -f deploy/compose.asr.yml down || true; fi
        "${LLM_COMPOSE[@]}" down || true
    fi
    docker ps --format 'table {{.Names}}\t{{.Status}}'
    ;;
*)
    sed -n "2,25p" "$0"; exit 2 ;;
esac
