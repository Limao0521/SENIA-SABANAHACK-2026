#!/bin/bash
# usage: start_motor_fix.sh NAME [motor args]  (runs detached inside x2-twin from /tmp/o3fix; logs /tmp/o3fix/NAME.log)
export PATH=/tmp/claude-1000/shim:$PATH; N=$1; shift
podman exec -d x2-twin bash -c "source /opt/ros/humble/setup.bash; source /workspace/aimdk/install/local_setup.bash; source /tmp/aimdk_overlay/install/local_setup.bash; export RMW_IMPLEMENTATION=rmw_fastrtps_cpp; export PYTHONPATH=/workspace/x2_ultra/src:/tmp/o3fix:\$PYTHONPATH; cd /tmp/o3fix && python3 -u motor.py $N.csv $* > $N.log 2>&1"
