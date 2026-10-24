#!/bin/bash
# Deploy the motor_fix stack into x2-twin:/tmp/o3fix (host-side; needs the podman shim on PATH).
set -e
export PATH=/tmp/claude-1000/shim:$PATH
HERE=$(cd "$(dirname "$0")" && pwd)
podman exec x2-twin mkdir -p /tmp/o3fix
podman cp "$HERE/o3_motor_fix.py"      x2-twin:/tmp/o3fix/motor.py
podman cp "$HERE/emote_lib_fix.py"     x2-twin:/tmp/o3fix/emote_lib.py
podman cp "$HERE/o3common_fix.py"      x2-twin:/tmp/o3fix/o3common.py
podman cp "$HERE/mc_preset_helper.py"  x2-twin:/tmp/o3fix/mc_preset_helper.py
podman cp "$HERE/point_targets.json"   x2-twin:/tmp/o3fix/point_targets.json
podman cp "$HERE/test_preset.py"       x2-twin:/tmp/o3fix/test_preset.py
podman exec x2-twin rm -rf /tmp/o3fix/__pycache__
echo DEPLOY_OK
