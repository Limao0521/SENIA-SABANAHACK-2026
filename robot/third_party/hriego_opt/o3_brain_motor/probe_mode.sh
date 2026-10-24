#!/bin/bash
export PATH=/tmp/claude-1000/shim:$PATH
podman exec x2-twin bash -c 'source /opt/ros/humble/setup.bash; source /workspace/aimdk/install/local_setup.bash; source /tmp/aimdk_overlay/install/local_setup.bash; export RMW_IMPLEMENTATION=rmw_fastrtps_cpp; export PYTHONPATH=/workspace/x2_ultra/src:$PYTHONPATH; cd /tmp/o3; python3 probe_mode.py' 2>&1 | tail -3
