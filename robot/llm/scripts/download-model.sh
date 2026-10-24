#!/usr/bin/env bash
# Descarga modelos GGUF de Qwen3 (oficiales en HuggingFace) a ./models/
#
# Uso (desde el robot o servidor Linux):
#   ./scripts/download-model.sh              # Qwen3-4B Q4_K_M (recomendado, ~2,5GB)
#   ./scripts/download-model.sh 8B Q4_K_M    # Máxima calidad en Orin NX 16GB
#   ./scripts/download-model.sh 0.6B Q8_0    # Draft p/ decodificación especulativa
#                                            # (0.6B oficial solo existe en Q8_0)
#   HF_ENDPOINT=https://hf-mirror.com ./scripts/download-model.sh   # Espejo (China)
set -euo pipefail

SIZE="${1:-4B}"
QUANT="${2:-Q4_K_M}"
ENDPOINT="${HF_ENDPOINT:-https://huggingface.co}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIR="$ROOT/models"
mkdir -p "$DIR"

FILE="Qwen3-${SIZE}-${QUANT}.gguf"
URL="${ENDPOINT%/}/Qwen/Qwen3-${SIZE}-GGUF/resolve/main/${FILE}"
DEST="$DIR/$FILE"

if [ -f "$DEST" ]; then
    # Compara con el tamaño remoto: si está completo, salta; si no, reanuda (curl -C -)
    LOCAL=$(stat -c %s "$DEST" 2>/dev/null || stat -f %z "$DEST")
    REMOTE=$(curl -sIL "$URL" | tr -d '\r' | awk 'tolower($1)=="content-length:" {v=$2} END{print v}')
    if [ -n "$REMOTE" ] && [ "$LOCAL" -ge "$REMOTE" ]; then
        echo "Ya existe y está completo: $DEST"
        exit 0
    fi
    echo "Archivo incompleto ($LOCAL de ${REMOTE:-?} bytes), reanudando descarga..."
fi

echo "Descargando $FILE"
echo "  URL:  $URL"
echo "  Dest: $DEST"

if command -v curl >/dev/null 2>&1; then
    curl -L --fail --retry 3 --retry-delay 2 -C - --progress-bar -o "$DEST" "$URL"
elif command -v wget >/dev/null 2>&1; then
    wget -c -O "$DEST" "$URL"
else
    echo "ERROR: necesitas curl o wget" >&2
    exit 1
fi

ls -lh "$DEST"
echo "OK. Ajusta MODEL_PATH=/models/$FILE en tu .env si no es el modelo por defecto."
echo "Nota: el modelo de embeddings (potion-multilingual-128M, ~500MB) se descarga"
echo "solo la primera vez que arranca el contenedor, hacia ./data/fastembed."
