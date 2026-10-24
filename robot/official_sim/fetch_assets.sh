#!/usr/bin/env bash
# Descarga lo que el simulador oficial necesita y git no lleva (≈ 600 MB comprimido, 3,5 GB descomprimido):
#   - AimDK SDK v1.1.0 (mensajes ROS 2, ejemplos, docs, extra/mc-rl)   -> $X2_SIM_ROOT/aimdk
#   - mc x86 v1.0.0 (control de movimiento oficial)                     -> $X2_SIM_ROOT/mc
#   - sim_mujoco x86 v1.0.0 (cuerpo simulado, robot lx2501_3_t2d5)      -> $X2_SIM_ROOT/sim_mujoco
# El espejo de AgiBot limita cada conexión (~8 KB/s), así que se baja por rangos en paralelo.
# Se puede relanzar: las partes terminadas se conservan.
set -eo pipefail

: "${X2_SIM_ROOT:=$HOME/robots/x2_simulation}"
DL="${X2_DOWNLOADS:-$X2_SIM_ROOT/../x2_downloads}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MIRROR=https://x2-aimdk.agibot.com/downloads
SDK_ZIP=aimdk-sdk-v1_1_0-aimdk-aarch64-faf4c945-artifacts.zip
MC_ZIP=mc-x86-v1.0.0-20260522.zip
SIM_ZIP=sim_mujoco-x86-v1.0.0-20260522.zip

mkdir -p "$DL" "$X2_SIM_ROOT"
pids=()
for zip in "$SDK_ZIP" "$MC_ZIP" "$SIM_ZIP"; do
    [[ -f $DL/$zip ]] || { python3 "$HERE/parallel_download.py" "$MIRROR/$zip" "$DL/$zip" 24 & pids+=($!); }
done
for pid in "${pids[@]}"; do wait "$pid"; done

unpack() {  # unpack <zip> <carpeta raíz dentro del zip> <nombre destino>
    [[ -d $X2_SIM_ROOT/$3 ]] && return
    python3 -c "import zipfile, sys; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])" "$DL/$1" "$X2_SIM_ROOT"
    mv "$X2_SIM_ROOT/$2" "$X2_SIM_ROOT/$3"
}
unpack "$SDK_ZIP" aimdk-aarch64-faf4c945-artifacts aimdk
unpack "$MC_ZIP" mc-x86-v1.0.0-20260522 mc
unpack "$SIM_ZIP" sim_mujoco-x86-v1.0.0-20260522 sim_mujoco
chmod -R u+x "$X2_SIM_ROOT/mc/bin" "$X2_SIM_ROOT/sim_mujoco/bin"   # zipfile pierde los bits de ejecución
echo "Assets del simulador oficial listos en $X2_SIM_ROOT"
