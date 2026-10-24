#!/usr/bin/env bash
# Build and run the bounded VRData publisher inside the networkless x2-twin container.
# Usage (from host):
#   docker exec -e ROS_DOMAIN_ID=77 x2-twin bash /workspace/x2_ultra/official_sim/vr_data_probe.sh --forward 0.2 --seconds 2
set -eo pipefail

[[ ${X2_SIM_ONLY:-} == 1 && ${ROS_LOCALHOST_ONLY:-} == 1 && ${ROS_DOMAIN_ID:-} == 77 ]] || {
    echo 'VRData probe requires the isolated twin container and ROS_DOMAIN_ID=77' >&2
    exit 2
}
[[ -f /workspace/x2_ultra/official_sim/vr_data_probe.c && -f /workspace/mc/lib/libaimdk_msgs__rosidl_generator_c.so ]] || {
    echo 'VRData probe must run inside x2-twin with the MC libraries mounted' >&2
    exit 2
}
# Pin the exact MC x86 v1.0.0 interface used for ABI reconstruction.
expected='c9d31ff619d47bb7f5d49e022579a4d5ae7b3fe5324e906162384e0586c83237'
actual=$(sha256sum /workspace/mc/lib/libaimdk_msgs__rosidl_generator_c.so | cut -d ' ' -f 1)
[[ $actual == "$expected" ]] || { echo 'MC VRData ABI changed; probe refused' >&2; exit 2; }

source /opt/ros/humble/setup.bash
export LD_LIBRARY_PATH="/workspace/mc/lib:${LD_LIBRARY_PATH:-}"
include_dirs=()
for dir in /opt/ros/humble/include/*; do
    [[ -d $dir ]] || continue
    # These two expose generic <features.h>/<string.h> names that mask libc.
    case ${dir##*/} in dds|idl) continue ;; esac
    include_dirs+=("-I$dir")
done
gcc -std=c11 -O2 -Wall -Wextra -D_DEFAULT_SOURCE "${include_dirs[@]}" \
    /workspace/x2_ultra/official_sim/vr_data_probe.c \
    -L/workspace/mc/lib -L/opt/ros/humble/lib \
    -Wl,-rpath,/workspace/mc/lib -Wl,-rpath,/opt/ros/humble/lib \
    -laimdk_msgs__rosidl_generator_c -laimdk_msgs__rosidl_typesupport_c \
    -lrosidl_runtime_c -lrcl -lrcutils -lm -o /tmp/x2_vr_data_probe
if [[ ${1:-} == --build-only ]]; then
    echo /tmp/x2_vr_data_probe
    exit 0
fi
exec /tmp/x2_vr_data_probe "$@"
