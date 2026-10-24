#!/usr/bin/env bash
# Banco de LLM candidatos A BORDO (Orin NX): levanta un llama-server de prueba por
# candidato en un puerto aparte (x2-llm-bench, :8091), corre scripts/bench_llm_hri.py en
# un contenedor desechable x2-hri:latest y lo apaga. NO toca x2-llm ni x2-hri-actuado.
#
# Uso (en el robot, desde la raíz del repo):
#   deploy/bench_llm_candidatos.sh                 # todos los candidatos de la tabla
#   deploy/bench_llm_candidatos.sh base draft06    # solo esos
#   REPS=1 LIMITE=6 deploy/bench_llm_candidatos.sh base   # humo rápido
#
# Memoria: con la sesión HRI arriba quedan ~2–3 GB; un segundo modelo de 4B NO cabe.
# Por eso se niega a correr si x2-llm o x2-hri-actuado están vivos (FORZAR=1 lo salta,
# bajo su responsabilidad). Resultados: out_diag/bench_llm/*.jsonl + *_resumen.json y
# la tabla comparativa al final.
set -euo pipefail
cd "$(dirname "$0")/.."

IMG_LLM=${IMG_LLM:-x2-ultra/llm:jetson}
IMG_HRI=${IMG_HRI:-x2-hri:latest}
PUERTO=${PUERTO:-8091}
CTX=${CTX:-8192}
REPS=${REPS:-2}
LIMITE=${LIMITE:-}
MODELOS=${MODELOS:-$PWD/llm/models}
SALIDA=out_diag/bench_llm
DOCKER=${DOCKER:-docker}

# etiqueta | modelo (en llm/models) | modelo borrador (o -) | flags del banco
CANDIDATOS=(
  "base|Qwen3-4B-Q4_K_M.gguf|-|"
  "base+prefijo|Qwen3-4B-Q4_K_M.gguf|-|--prefijo-estatico"
  "draft06|Qwen3-4B-Q4_K_M.gguf|Qwen3-0.6B-Q8_0.gguf|"
  "instruct2507|Qwen3-4B-Instruct-2507-Q4_K_M.gguf|-|"
  "q17b|Qwen3-1.7B-Q8_0.gguf|-|"
  "q17b+prefijo|Qwen3-1.7B-Q8_0.gguf|-|--prefijo-estatico"
)

vivo() { $DOCKER ps --format '{{.Names}}' | grep -qx "$1"; }
if [ "${FORZAR:-0}" != 1 ]; then
  for c in x2-llm x2-hri-actuado; do
    if vivo "$c"; then
      echo "[banco] $c está corriendo: pare la sesión HRI antes (memoria). FORZAR=1 para ignorar." >&2
      exit 1
    fi
  done
fi
vivo x2-llm-bench && $DOCKER rm -f x2-llm-bench >/dev/null

parar() { $DOCKER rm -f x2-llm-bench >/dev/null 2>&1 || true; }
trap parar EXIT
mkdir -p "$SALIDA"

elegidos=("$@")
resumenes=()
for fila in "${CANDIDATOS[@]}"; do
  IFS='|' read -r etiqueta modelo borrador flags <<<"$fila"
  if [ ${#elegidos[@]} -gt 0 ] && ! printf '%s\n' "${elegidos[@]}" | grep -qx "${etiqueta%%+*}\|$etiqueta"; then
    continue
  fi
  if [ ! -f "$MODELOS/$modelo" ]; then
    echo "[banco] $etiqueta: falta $MODELOS/$modelo (ver docs/reto-2/INFERENCIA_NUBE.md §B); salto"
    continue
  fi
  draft_env=()
  if [ "$borrador" != "-" ]; then
    [ -f "$MODELOS/$borrador" ] || { echo "[banco] $etiqueta: falta $borrador; salto"; continue; }
    draft_env=(-e "DRAFT_MODEL=/models/$borrador")
  fi
  echo "=== [banco] $etiqueta: $modelo ${borrador/#-/sin borrador} ==="
  parar
  # Solo llama-server (sin la API :8000 de run.sh). El entrypoint fija --port 8080;
  # EXTRA_ARGS va al final y lo sobreescribe (llama.cpp se queda con el último valor).
  $DOCKER run -d --name x2-llm-bench --runtime nvidia --network host \
    -v "$MODELOS:/models:ro" \
    -e MODEL_PATH="/models/$modelo" -e NGL=99 -e CTX_SIZE="$CTX" -e PARALLEL=1 \
    -e CPU_THREADS="${CPU_THREADS:-6}" -e EXTRA_ARGS="--port $PUERTO" "${draft_env[@]}" \
    --entrypoint /bin/sh "$IMG_LLM" /entrypoint.sh >/dev/null
  t0=$(date +%s)
  until curl -sf "http://127.0.0.1:$PUERTO/health" >/dev/null; do
    sleep 2
    if [ $(( $(date +%s) - t0 )) -gt 300 ]; then
      echo "[banco] $etiqueta: llama-server no arrancó en 300 s"; $DOCKER logs --tail 30 x2-llm-bench; continue 2
    fi
  done
  echo "[banco] listo en $(( $(date +%s) - t0 )) s"
  $DOCKER run --rm --network host -v "$PWD:/workspace/x2_ultra" -w /workspace/x2_ultra \
    -e PYTHONPATH=src "$IMG_HRI" python3 scripts/bench_llm_hri.py \
    --url "http://127.0.0.1:$PUERTO" --etiqueta "${etiqueta%%+*}" --repeticiones "$REPS" \
    ${LIMITE:+--limite "$LIMITE"} $flags --salida "$SALIDA" | tee "$SALIDA/ultimo_$etiqueta.txt"
  r=$(ls -t "$SALIDA"/*_resumen.json | head -1)
  resumenes+=("$r")
done
parar
if [ ${#resumenes[@]} -gt 0 ]; then
  $DOCKER run --rm -v "$PWD:/workspace/x2_ultra" -w /workspace/x2_ultra -e PYTHONPATH=src "$IMG_HRI" \
    python3 scripts/bench_llm_hri.py --comparar "${resumenes[@]}"
fi
