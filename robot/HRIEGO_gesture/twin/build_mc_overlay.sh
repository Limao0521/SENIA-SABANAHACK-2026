#!/usr/bin/env bash
# Build /tmp/aimdk_mc_overlay in x2-twin: the aimdk_msgs overlay with input_source REMOVED from SetMcPresetMotion (the layout
# twin MC v1.0.0 decodes). Used only by scripts/mc_preset_helper.py. ~70 s. Lives in the container's /tmp (lost if it is recreated).
set -e
podman exec x2-twin bash -c 'set -e; source /opt/ros/humble/setup.bash; rm -rf /tmp/aimdk_mc_overlay; mkdir -p /tmp/aimdk_mc_overlay; cp -r /tmp/aimdk_overlay/src /tmp/aimdk_mc_overlay/src; cd /tmp/aimdk_mc_overlay
for f in src/aimdk_msgs/srv/SetMcPresetMotion.srv src/aimdk_msgs/msg/SetMcPresetMotion_Request.msg; do python3 - $f <<PY
import sys; p=sys.argv[1]; s=open(p).read(); s=s.replace("# 输入源\nMcInputSource input_source\n\n",""); assert "input_source" not in s; open(p,"w").write(s)
PY
done
source /workspace/aimdk/install/local_setup.bash; colcon build --packages-select aimdk_msgs --event-handlers console_direct- 2>&1 | tail -3'
