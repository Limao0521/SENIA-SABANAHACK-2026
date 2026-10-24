#!/usr/bin/env bash
# usage: calib.sh NAME [walk_arc args...]   (host; podman via shim)
export PATH="${X2_TWIN_SHIM_DIR:-$HOME/.local/share/hriego/shim}:$PATH"
H="$(cd "$(dirname "$0")" && pwd)"; N=$1; shift
ENVS='source /opt/ros/humble/setup.bash; source /workspace/aimdk/install/local_setup.bash; source /tmp/aimdk_overlay/install/local_setup.bash; export RMW_IMPLEMENTATION=rmw_fastrtps_cpp; export PYTHONPATH=/workspace/x2_ultra/src:$PYTHONPATH'
podman exec x2-twin mkdir -p /tmp/v4e; for f in mission_log.py walk_arc.py; do podman cp $H/$f x2-twin:/tmp/v4e/; done
podman exec -d x2-twin bash -c "$ENVS; cd /tmp/v4e && python3 mission_log.py $N > log_$N.txt 2>&1"
sleep 3
podman exec x2-twin bash -c "$ENVS; cd /tmp/v4e && python3 walk_arc.py $N $*"
sleep 1; podman exec x2-twin touch /tmp/v4e/STOP_$N; sleep 3
mkdir -p $H/runs/4e_missions/calib
for f in ${N}_log.csv ${N}_qpos.npz ${N}_events.json ${N}_cmd.csv; do podman cp x2-twin:/tmp/v4e/$f $H/runs/4e_missions/calib/; done
