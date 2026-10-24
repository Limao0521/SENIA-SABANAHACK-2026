#!/bin/bash
# usage: start_motor.sh NAME [motor args]  (runs detached inside the container; logs /tmp/o3/NAME.log)
export PATH=/tmp/claude-1000/shim:$PATH; N=$1; shift
podman exec -d x2-twin bash -c "source /opt/ros/humble/setup.bash; source /workspace/aimdk/install/local_setup.bash; source /tmp/aimdk_overlay/install/local_setup.bash; export RMW_IMPLEMENTATION=rmw_fastrtps_cpp; export PYTHONPATH=/workspace/x2_ultra/src:/tmp/o3:\$PYTHONPATH; cd /tmp/o3 && python3 -u motor.py $N.csv $* > $N.log 2>&1"
