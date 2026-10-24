#!/usr/bin/env bash
# Compila un aimdk_msgs propio (todas las definiciones del AimDK público + las que el robot tiene y el SDK no:
# scripts/aimdk_overlay/*.msg|*.srv) en $X2_SIM_ROOT/aimdk_overlay, sin tocar el AimDK instalado.
# scripts/robot_real.sh lo carga encima si existe. Hace falta para las cuclillas de `andar` (McBodyPose) y los bailes
# de fábrica de `movimientos` (SetMcMotion, GetMcMotions, McMotion); el resto funciona sin él. Tarda ≈1 min.
# Se ejecuta una vez por PC (y otra si cambia el AimDK o scripts/aimdk_overlay/). Detalles: scripts/aimdk_overlay/README.md
#
#   scripts/build_aimdk_overlay.sh           # compilar (borra y rehace $X2_SIM_ROOT/aimdk_overlay)
#   scripts/build_aimdk_overlay.sh --check   # sólo comprobar que el overlay existe y trae McBodyPose
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
: "${X2_SIM_ROOT:=$HOME/robots/x2_simulation}"
: "${X2_IMAGE:=x2-ultra-sim:humble}"
SHARE="$X2_SIM_ROOT/aimdk/src/aimdk_msgs/prebuilt_x86_64/share/aimdk_msgs"
OUT="$X2_SIM_ROOT/aimdk_overlay"

check() {
    [[ -f $OUT/install/local_setup.bash ]] || { echo "No hay overlay en $OUT (scripts/build_aimdk_overlay.sh)" >&2; return 1; }
    docker run --rm -v "$X2_SIM_ROOT/aimdk:/workspace/aimdk:ro" -v "$OUT/install:/workspace/aimdk_overlay:ro" \
        "$X2_IMAGE" bash -c 'source /opt/ros/humble/setup.bash; source /workspace/aimdk/install/local_setup.bash
            source /workspace/aimdk_overlay/local_setup.bash
            python3 -c "from aimdk_msgs.msg import McBodyPose, McMotion, McCommonState, McLocomotionVelocity
from aimdk_msgs.srv import SetMcMotion, GetMcMotions, SetMcAction, SetMcInputSource, SetMcPresetMotion
print(\"overlay OK: McBodyPose\", list(McBodyPose.get_fields_and_field_types()))
print(\"overlay OK: SetMcMotion\", list(SetMcMotion.Request.get_fields_and_field_types()))"'
}
if [[ ${1:-} == --check ]]; then check; exit; fi
[[ -z ${1:-} ]] || { sed -n '2,9p' "$0" >&2; exit 2; }
[[ -d $SHARE ]] || { echo "Falta $SHARE (official_sim/fetch_assets.sh)" >&2; exit 2; }
docker image inspect "$X2_IMAGE" >/dev/null 2>&1 || { echo "Falta la imagen $X2_IMAGE (official_sim/sim.sh image)" >&2; exit 2; }
rm -rf "$OUT"
PKG="$OUT/src/aimdk_msgs"
mkdir -p "$PKG/msg" "$PKG/srv"
cp "$X2_SIM_ROOT/aimdk/src/aimdk_msgs/package.xml" "$PKG/"
cp "$REPO/scripts/aimdk_overlay/CMakeLists.txt" "$PKG/"
# Las definiciones están repartidas por carpetas (msg/, common/, action/...): todas a msg/ y srv/.
find "$SHARE" -name '*.msg' -exec cp {} "$PKG/msg/" \;
find "$SHARE" -name '*.srv' -exec cp {} "$PKG/srv/" \;
added=0
for extra in "$REPO"/scripts/aimdk_overlay/*.msg "$REPO"/scripts/aimdk_overlay/*.srv; do
    kind=${extra##*.}
    if [[ -e $PKG/$kind/$(basename "$extra") ]]; then
        echo "$(basename "$extra") ya está en el SDK: se usa la del SDK"
    else
        cp "$extra" "$PKG/$kind/"; added=$((added + 1))
    fi
done
((added)) || { echo "El SDK ya trae todo lo de scripts/aimdk_overlay: no hace falta el overlay" >&2; rm -rf "$OUT"; exit 0; }
echo "$(ls "$PKG/msg" | wc -l) mensajes y $(ls "$PKG/srv" | wc -l) servicios ($added del robot)"
docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp -v "$OUT:/ws" -w /ws "$X2_IMAGE" bash -c '
  source /opt/ros/humble/setup.bash
  colcon build --packages-select aimdk_msgs --event-handlers console_direct- 2>&1 | tail -5'
[[ -f $OUT/install/local_setup.bash ]] || { echo "La compilación falló" >&2; exit 1; }
check
echo "overlay listo: $OUT/install (scripts/robot_real.sh lo carga solo)"
