#!/usr/bin/env bash
# Descarga de modelos para la pila en la nube (x2-nube).
#
# Se ejecuta EN EL VPS, desde deploy/nube/ (o con cualquier cwd: el script se
# reubica junto a sí mismo). Crea ./modelos/ con:
#   - El GGUF del LLM (Qwen3-8B-Q4_K_M.gguf por defecto; elegible con el
#     argumento 1 o la variable NUBE_LLM_MODELO entre los tres soportados).
#   - faster-whisper-large-v3-turbo (repositorio CTranslate2 de Hugging Face).
#
# Supertonic NO se descarga aquí: el contenedor `voz` lo obtiene solo la primera
# vez con SUPERTONIC_AUTO_DOWNLOAD=1 (queda en modelos/supertonic/).
#
# Idempotente: los archivos ya completos se omiten; los incompletos se reanudan
# (curl -C -). Uso:
#   deploy/nube/descargar_modelos.sh [Qwen3-8B-Q4_K_M.gguf|Qwen3-14B-Q4_K_M.gguf|Qwen3-4B-Q4_K_M.gguf]
set -euo pipefail

RUTA_SCRIPT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$RUTA_SCRIPT"

HF="https://huggingface.co"
DIR_MODELOS="modelos"
mkdir -p "$DIR_MODELOS"

# ---------------------------------------------------------------- utilidades
# descargar <url> <destino>: omite si el archivo local ya tiene el tamaño
# remoto (completo); si no, reanuda la descarga donde quedó.
descargar() {
    local url="$1" dst="$2" remoto tam
    mkdir -p "$(dirname -- "$dst")"
    if [ -f "$dst" ]; then
        remoto="$(curl -sIL --fail "$url" 2>/dev/null | tr -d '\r' \
                  | awk 'tolower($1)=="content-length:"{v=$2} END{print v+0}')"
        tam="$(stat -c %s "$dst" 2>/dev/null || stat -f %z "$dst" 2>/dev/null || echo 0)"
        if [ "$remoto" -gt 0 ] && [ "$tam" -eq "$remoto" ]; then
            echo "[modelos] ya completo, se omite: $dst"
            return 0
        fi
        echo "[modelos] incompleto ($tam de ${remoto:-?} bytes), se reanuda: $dst"
    fi
    echo "[modelos] descargando $url"
    echo "[modelos]          hacia $dst"
    curl -L --fail -C - --retry 3 --retry-delay 5 -o "$dst" "$url"
}

# ---------------------------------------------------------------- LLM (GGUF)
MODELO_LLM="${1:-${NUBE_LLM_MODELO:-Qwen3-8B-Q4_K_M.gguf}}"
case "$MODELO_LLM" in
    Qwen3-8B-Q4_K_M.gguf)  REPO_LLM="Qwen/Qwen3-8B-GGUF"  ;;
    Qwen3-14B-Q4_K_M.gguf) REPO_LLM="Qwen/Qwen3-14B-GGUF" ;;
    Qwen3-4B-Q4_K_M.gguf)  REPO_LLM="Qwen/Qwen3-4B-GGUF"  ;;
    *)
        echo "ERROR: modelo LLM no soportado: $MODELO_LLM" >&2
        echo "       Opciones: Qwen3-8B-Q4_K_M.gguf (defecto), Qwen3-14B-Q4_K_M.gguf, Qwen3-4B-Q4_K_M.gguf" >&2
        exit 1
        ;;
esac
echo "[modelos] LLM: $REPO_LLM/$MODELO_LLM"
descargar "$HF/$REPO_LLM/resolve/main/$MODELO_LLM" "$DIR_MODELOS/$MODELO_LLM"

# ---------------------------------------------------------------- ASR (Whisper)
# Repositorio CTranslate2 usado por faster-whisper (HRI_ASR_MODEL del contenedor
# voz apunta a /modelos/faster-whisper-large-v3-turbo).
REPO_ASR="mobiuslabsgmbh/faster-whisper-large-v3-turbo"
DIR_ASR="$DIR_MODELOS/faster-whisper-large-v3-turbo"
echo "[modelos] ASR: $REPO_ASR"
for f in config.json model.bin preprocessor_config.json tokenizer.json vocabulary.json; do
    descargar "$HF/$REPO_ASR/resolve/main/$f" "$DIR_ASR/$f"
done

# ---------------------------------------------------------------- TTS (Supertonic)
echo "[modelos] Supertonic: no se descarga aquí. El contenedor 'voz' lo obtiene"
echo "[modelos] automáticamente (SUPERTONIC_AUTO_DOWNLOAD=${NUBE_TTS_AUTO_DOWNLOAD:-1})"
echo "[modelos] en $DIR_MODELOS/supertonic/ la primera vez que arranca."

echo "[modelos] listo."
