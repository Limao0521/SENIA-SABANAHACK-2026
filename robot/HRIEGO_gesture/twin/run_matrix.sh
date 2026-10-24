#!/usr/bin/env bash
# 4aa: pointing matrix on x2-twin. BASE=READY|SONIC, DIRS="RIGHT LEFT FRONT", REPS=3, HOLD=1.5. Rep 1 records qpos for the video.
# Needs the twin up (STABLE) with /tmp/aimdk_overlay (McBodyPose overlay). Run from HRIEGO_gesture/twin with podman on PATH (shim).
set -u
H="$(cd "$(dirname "$0")" && pwd)"; R="$H/runs/4aa_pointing"; REPS=${REPS:-3}; BASE=${BASE:-READY}; DIRS=${DIRS:-"RIGHT LEFT FRONT"}; HOLD=${HOLD:-1.5}
EXTRA=${EXTRA:-}; TAG=${TAG:-$BASE}
ENVS='source /opt/ros/humble/setup.bash; source /workspace/aimdk/install/local_setup.bash; source /tmp/aimdk_overlay/install/local_setup.bash; export RMW_IMPLEMENTATION=rmw_fastrtps_cpp; export PYTHONPATH=/workspace/x2_ultra/src:$PYTHONPATH'
podman exec x2-twin mkdir -p /tmp/p4aa
podman cp "$H/stream_point.py" x2-twin:/tmp/p4aa/; podman cp "$H/point_targets.json" x2-twin:/tmp/p4aa/
mkdir -p "$R/traces"
for d in $DIRS; do
  podman cp "$R/plans/$d.point.json" x2-twin:/tmp/p4aa/
  if [[ $BASE == SONIC ]]; then podman cp "$R/motion/P_${d}_arms.npz" x2-twin:/tmp/p4aa/; B="P_${d}_arms.npz"; else B=READY; fi
  for r in $(seq 1 "$REPS"); do
    id="${TAG}_${d}_r${r}"; rec=""; [[ $r == 1 ]] && rec="--qpos-out ${id}_qpos.npz"
    podman exec x2-twin bash -c "$ENVS; cd /tmp/p4aa && timeout 60 python3 stream_point.py READY $d.point.json zero.csv --zero --ramp-s 3" >/dev/null 2>&1
    echo "== $id"
    podman exec x2-twin bash -c "$ENVS; cd /tmp/p4aa && timeout 120 python3 stream_point.py $B $d.point.json ${id}.csv --hold-s $HOLD $rec $EXTRA" 2>&1 | tail -2
    podman cp "x2-twin:/tmp/p4aa/${id}.csv" "$R/traces/${id}.csv" || echo "NO CSV"
    podman cp "x2-twin:/tmp/p4aa/${id}.json" "$R/traces/${id}.json"
    [[ $r == 1 ]] && podman cp "x2-twin:/tmp/p4aa/${id}_qpos.npz" "$R/video/${id}_qpos.npz"
    sleep 3
  done
done
