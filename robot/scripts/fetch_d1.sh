#!/usr/bin/env bash
# URDF y mallas del perro robot AgiBot D1 (Edu) en third_party/agibot_d1 (no se versionan, ~16 MB).
# AgiBot no publica el modelo del D1 en GitHub (su repo agibot_D1_Edu-Ultra solo trae el SDK); este es el
# edu_description del repo público chiway-luo/mujoco_agibot_d1, commit fijado.
set -eo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DST="$REPO/third_party/agibot_d1"
COMMIT=19177917275dd084e1f7fbe8e2cee97fe9fc4686
BASE="https://raw.githubusercontent.com/chiway-luo/mujoco_agibot_d1/$COMMIT/src/sim_dog/edu_description"
MESHES="BASE_LINK"
for leg in FL FR RL RR; do
    for part in ABAD HIP KNEE FOOT; do MESHES="$MESHES ${leg}_${part}_LINK"; done
done

mkdir -p "$DST/urdf" "$DST/meshes"
[[ -s $DST/urdf/edu_mujoco.urdf ]] || curl -fsSL -o "$DST/urdf/edu_mujoco.urdf" "$BASE/urdf/edu_mujoco.urdf"
for m in $MESHES; do
    [[ -s $DST/meshes/$m.STL ]] || curl -fsSL -o "$DST/meshes/$m.STL" "$BASE/meshes/$m.STL"
done
echo "AgiBot D1 en $DST"
