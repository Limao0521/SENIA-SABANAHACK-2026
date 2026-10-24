#!/bin/bash
# Arranque del contenedor único: llama-server + API FastAPI (memoria + RAG).
# Si uno de los dos procesos muere, se apaga el contenedor (restart policy lo
# levanta). Las señales SIGTERM/SIGINT se reenvían a ambos procesos.
#
# API_ONLY=1 → solo la API (útil para pruebas: smoke tests ARM64 bajo QEMU,
# tests sin modelo, etc.)
set -e

API_PORT="${API_PORT:-8000}"

start_api() {
    exec python3 -m uvicorn --factory app.main:create_app --host 0.0.0.0 --port "$API_PORT" --log-level info
}

if [ "${API_ONLY:-0}" = "1" ]; then
    echo "[run] API_ONLY=1: arranco solo la API (sin llama-server)"
    start_api
fi

# 1) llama-server (entrypoint.sh detecta flags y hace exec del binario)
/entrypoint.sh &
LLM_PID=$!

# 2) API FastAPI — no bloquea esperando al LLM: arranca ya y /health informa
#    del estado; /chat espera a que el modelo esté listo (wait_ready interno)
python3 -m uvicorn --factory app.main:create_app --host 0.0.0.0 --port "$API_PORT" --log-level info &
API_PID=$!

term() {
    trap - TERM INT
    kill -TERM "$LLM_PID" "$API_PID" 2>/dev/null || true
}
trap term TERM INT

# En cuanto uno de los dos cae, terminamos el otro y salimos
wait -n
RC=$?
echo "[run] un proceso terminó (rc=$RC); cerrando el otro"
term
wait "$LLM_PID" 2>/dev/null || true
wait "$API_PID" 2>/dev/null || true
exit "$RC"
