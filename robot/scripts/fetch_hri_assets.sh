#!/usr/bin/env bash
# Copia los assets del MC de AgiBot que la recepción HRI del reto 2 necesita y NO se pueden
# versionar (licencia del fabricante): animation_player.yaml y los 12 CSV de animación que
# referencia el catálogo de emotes (assets/hri/HRIEGO_gesture/contract/catalog.json).
#
#   scripts/fetch_hri_assets.sh                 # desde $X2_SIM_ROOT (default ~/robots/x2_simulation)
#   X2_SIM_ROOT=/ruta/a/x2_simulation scripts/fetch_hri_assets.sh
#
# Destino: assets/hri/mc_classic/ (ignorado por git). La integridad se verifica contra la lista
# versionada assets/hri/mc_classic.sha256 (sha256sum -c). Nota: official_sim/fetch_assets.sh baja
# los binarios del simulador oficial, NO estos parámetros del MC; use este script.
set -eo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"
: "${X2_SIM_ROOT:=$HOME/robots/x2_simulation}"
SRC="$X2_SIM_ROOT/mc/bin/mc_param/src/robot/lx2501_3_t2d5/classic"
DST="assets/hri/mc_classic"
LISTA="assets/hri/mc_classic.sha256"

if [[ ! -d $SRC ]]; then
    echo "ERROR: no existe $SRC" >&2
    echo "Defina X2_SIM_ROOT con un checkout del simulador oficial (mc_param)." >&2
    exit 1
fi

mkdir -p "$DST"
while read -r _sha file; do
    [[ -z $file ]] && continue
    mkdir -p "$DST/$(dirname "$file")"
    cp -f "$SRC/$file" "$DST/$file"
done < "$LISTA"

(cd "$DST" && sha256sum -c "$REPO/$LISTA")
echo "Listo: $DST (animation_player.yaml + $(wc -l < "$LISTA") archivos verificados)."
