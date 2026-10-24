#!/usr/bin/env bash
# =============================================================================
# build_aarch64.sh — construye la imagen x2-onboard:latest de forma NATIVA en
# el PC2 del robot (Jetson Orin NX, aarch64). No cruza desde x86: el Orin
# compila su propia imagen (es rápido, ~8×A78AE; sin qemu).
#
# Uso:
#   deploy/build_aarch64.sh                  # runtime por defecto
#   deploy/build_aarch64.sh --target bench   # incluye mujoco + onnxruntime
#   deploy/build_aarch64.sh --no-cache       # reconstruye desde cero
#
# Requisitos:
#   - Docker Engine + BuildKit en el Orin (Ubuntu 22.04 / JetPack 6 lo traen).
#   - El SDK AimDK aarch64 en $X2_SIM_ROOT/aimdk. Si no está, se baja con
#     official_sim/fetch_assets.sh (solo la parte del SDK, ~600 MB).
#   - El repo completo en algún sitio del Orin (este script lo localiza solo).
#
# X2_BUILD_NETWORK=host: necesario si dockerd corre sin bridge (--bridge=none,
# p. ej. el X2 trae docker.service enmascarado y se lanza dockerd a mano).
# =============================================================================
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
: "${X2_SIM_ROOT:=$HOME/robots/x2_simulation}"
: "${X2_ONBOARD_IMAGE:=x2-onboard:latest}"
SDK_ROOT="$X2_SIM_ROOT/aimdk"
TAG=runtime
NO_CACHE=""

while (($#)); do
    case "$1" in
        --target)
            case "${2:-}" in
                runtime|bench) TAG="$2" ;;
                *) echo "target desconocido: $2 (esperado runtime|bench)" >&2; exit 2 ;;
            esac
            shift 2 ;;
        --no-cache) NO_CACHE="--no-cache"; shift ;;
        -h|--help)
            sed -n '2,20p' "$0"; exit 0 ;;
        *) echo "arg desconocido: $1" >&2; exit 2 ;;
    esac
done

# --- 1) SDK AimDK aarch64 -----------------------------------------------------
# El zip oficial es aimdk-sdk-v1_1_0-aimdk-aarch64-faf4c945-artifacts.zip y se
# extrae en $X2_SIM_ROOT/aimdk. official_sim/fetch_assets.sh baja también mc y
# sim_mujoco (x86, no los necesitamos a bordo); para no gastar ancho de banda
# en el Orin, aquí solo bajamos el SDK con parallel_download.py si falta.
if [[ ! -f $SDK_ROOT/src/aimdk_msgs/package.xml ]]; then
    echo "Falta el SDK AimDK aarch64 en $SDK_ROOT. Lo bajo…"
    mkdir -p "$X2_SIM_ROOT" "${X2_DOWNLOADS:-$X2_SIM_ROOT/../x2_downloads}"
    MIRROR=https://x2-aimdk.agibot.com/downloads
    SDK_ZIP=aimdk-sdk-v1_1_0-aimdk-aarch64-faf4c945-artifacts.zip
    DL_DIR="${X2_DOWNLOADS:-$X2_SIM_ROOT/../x2_downloads}"
    if [[ ! -f $DL_DIR/$SDK_ZIP ]]; then
        python3 "$REPO/official_sim/parallel_download.py" \
            "$MIRROR/$SDK_ZIP" "$DL_DIR/$SDK_ZIP" 24
    fi
    python3 -c "import zipfile,sys; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])" \
        "$DL_DIR/$SDK_ZIP" "$X2_SIM_ROOT"
    mv "$X2_SIM_ROOT/aimdk-aarch64-faf4c945-artifacts" "$SDK_ROOT"
    echo "SDK AimDK aarch64 listo en $SDK_ROOT"
fi

# Comprobación rápida. El zip aarch64 no trae install/: basta con src/aimdk_msgs/prebuilt_aarch64.
[[ -d $SDK_ROOT/src/aimdk_msgs/prebuilt_aarch64/share/aimdk_msgs ]] || {
    echo "El SDK de $SDK_ROOT no tiene src/aimdk_msgs/prebuilt_aarch64 (zip corrupto?)." >&2; exit 1
}

# --- 2) Docker BuildKit ------------------------------------------------------
# BuildKit está disponible en Docker ≥23 (JetPack 6 lo trae). Lo activamos por si
# el daemon no lo tiene por defecto.
export DOCKER_BUILDKIT=1
export COMPOSE_DOCKER_CLI_BUILD=1

echo "→ construyendo $X2_ONBOARD_IMAGE (target=$TAG) desde $REPO"
echo "  SDK aimdk: $SDK_ROOT"

docker buildx build \
    --load \
    --build-context "aimdk=$SDK_ROOT" \
    --target "$TAG" \
    ${X2_BUILD_NETWORK:+--network "$X2_BUILD_NETWORK"} \
    -f "$REPO/deploy/Dockerfile.aarch64" \
    $NO_CACHE \
    -t "$X2_ONBOARD_IMAGE" \
    "$REPO"

echo ""
echo "OK · imagen: $X2_ONBOARD_IMAGE"
docker image inspect "$X2_ONBOARD_IMAGE" --format \
    '  tamaño:   {{.Size}} bytes ({{div .Size 1048576}} MB)
  creada:   {{.Created}}
  arch:     {{.Architecture}}'
echo ""
echo "Siguiente paso:  deploy/onboard.sh check"
