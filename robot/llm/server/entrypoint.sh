#!/bin/sh
# Wrapper de arranque de llama-server optimizado para latencia conversacional.
# Detecta los flags soportados por el binario (la sintaxis cambia entre versiones)
# y arma la línea de comandos óptima. Toda la configuración entra por variables
# de entorno (ver .env.example / docker-compose.yml).
set -e

# --- Localizar el binario llama-server según la imagen base -------------------
BIN="$(command -v llama-server 2>/dev/null || true)"
if [ -z "$BIN" ]; then
    for p in \
        /app/llama-server \
        /opt/llama.cpp/build/bin/llama-server \
        /llama.cpp/build/bin/llama-server \
        /build/bin/llama-server \
        /usr/local/bin/llama-server
    do
        [ -x "$p" ] && BIN="$p" && break
    done
fi
if [ -z "$BIN" ]; then
    echo "[entrypoint] ERROR: no se encontró el binario llama-server" >&2
    exit 1
fi

HELP="$("$BIN" --help 2>&1 || true)"
has() { case "$HELP" in *"$1"*) return 0 ;; *) return 1 ;; esac; }

# --- Configuración ------------------------------------------------------------
MODEL="${MODEL_PATH:-/models/Qwen3-4B-Q4_K_M.gguf}"
CTX="${CTX_SIZE:-16384}"
THREADS="${CPU_THREADS:-6}"
NGL="${NGL:-0}"
PAR="${PARALLEL:-1}"

if [ ! -f "$MODEL" ]; then
    echo "[entrypoint] ERROR: modelo no encontrado en $MODEL" >&2
    echo "[entrypoint] Descárgalo con scripts/download-model.(sh|ps1) o ajusta MODEL_PATH" >&2
    exit 1
fi

# Flags estables en todas las versiones
ARGS="--model $MODEL --host 0.0.0.0 --port 8080"
ARGS="$ARGS --ctx-size $CTX --threads $THREADS --n-gpu-layers $NGL"

# Flash Attention: -30/50% de latencia en prefill; requisito para KV cache cuantizado
if has "--flash-attn"; then
    case "$HELP" in
        # Builds nuevos: valor obligatorio/opcional (on|off|auto) — SIEMPRE pasar "on"
        *"--flash-attn <string>"*|*"--flash-attn=on"*|*"--flash-attn ["*) ARGS="$ARGS --flash-attn on" ;;
        # Builds antiguos: flag booleano sin valor
        *) ARGS="$ARGS --flash-attn" ;;
    esac
    # KV cache Q8_0: ~40% menos memoria y atención más rápida (requiere FA)
    if has "--cache-type-k"; then
        ARGS="$ARGS --cache-type-k ${KV_CACHE_TYPE:-q8_0} --cache-type-v ${KV_CACHE_TYPE:-q8_0}"
    elif has "-ctk"; then
        ARGS="$ARGS -ctk ${KV_CACHE_TYPE:-q8_0} -ctv ${KV_CACHE_TYPE:-q8_0}"
    fi
fi

# Motor de plantillas Jinja (minja): necesario para que chat_template_kwargs
# (enable_thinking=false en Qwen3) surta efecto en builds recientes
has "--jinja" && ARGS="$ARGS --jinja"

# Slots paralelos. OJO: llama-server reparte --ctx-size entre los slots;
# PARALLEL=1 deja toda la ventana a la conversación activa (defecto aquí).
has "--parallel" && ARGS="$ARGS --parallel $PAR"
has "--cont-batching" && ARGS="$ARGS --cont-batching"

# Reutilizar KV del prefijo (system + historial) entre turnos => TTFT mínimo.
# Con contexto amplio el ahorro es mayor.
has "--cache-reuse" && ARGS="$ARGS --cache-reuse ${CACHE_REUSE:-512}"

# Batching físico/micro-batch para prefill rápido
has "--batch-size" && ARGS="$ARGS --batch-size ${BATCH_SIZE:-512}"
has "--ubatch-size" && ARGS="$ARGS --ubatch-size ${UBATCH_SIZE:-512}"

# CORS abierto (la API y herramientas de prueba pueden estar en cualquier host)
if has "--cors-origins"; then
    ARGS="$ARGS --cors-origins *"
elif has "--cors-origin"; then
    ARGS="$ARGS --cors-origin *"
fi

has "--metrics" && ARGS="$ARGS --metrics"

# --- Decodificación especulativa (opcional, activa si hay DRAFT_MODEL) --------
if [ -n "${DRAFT_MODEL:-}" ] && [ -f "${DRAFT_MODEL:-}" ]; then
    if has "--model-draft"; then
        ARGS="$ARGS --model-draft $DRAFT_MODEL"
        has "--draft-max" && ARGS="$ARGS --draft-max ${DRAFT_MAX:-8}"
        has "--draft-min" && ARGS="$ARGS --draft-min ${DRAFT_MIN:-2}"
        echo "[entrypoint] Decodificación especulativa activa con $DRAFT_MODEL"
    fi
fi

# Args extra libres (última palabra, permite sobreescribir cualquier flag)
ARGS="$ARGS ${EXTRA_ARGS:-}"

echo "[entrypoint] binario: $BIN"
echo "[entrypoint] exec: $BIN $ARGS"
# set -f: desactiva la expansión de globs del shell — sin esto, el `*` de
# --cors-origins se expandiría a los archivos del workdir
set -f
# shellcheck disable=SC2086
exec "$BIN" $ARGS
