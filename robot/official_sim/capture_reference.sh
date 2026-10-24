#!/usr/bin/env bash
# Graba el contrato HAL del simulador oficial (sim_mujoco + MC) para que el gemelo digital lo imite:
# topics y servicios con tipo, QoS de cada topic HAL, frecuencias medidas y un mensaje de muestra.
# Se ejecuta DENTRO del contenedor del simulador oficial (official_sim/sim.sh shell o docker exec):
#   bash /workspace/x2_ultra/official_sim/capture_reference.sh /workspace/x2_ultra/logs/hal_reference
set -o pipefail
source /opt/ros/humble/setup.bash
export RMW_IMPLEMENTATION=rmw_fastrtps_cpp
source /workspace/aimdk/install/local_setup.bash
OUT=${1:-/workspace/x2_ultra/logs/hal_reference}
mkdir -p "$OUT"

ros2 topic list -t > "$OUT/topics.txt" 2>&1
ros2 service list -t > "$OUT/services.txt" 2>&1

TOPICS=(
  /aima/hal/joint/leg/state /aima/hal/joint/waist/state /aima/hal/joint/arm/state /aima/hal/joint/head/state
  /aima/hal/joint/hand/state
  /aima/hal/joint/leg/command /aima/hal/joint/waist/command /aima/hal/joint/arm/command
  /aima/hal/joint/head/command /aima/hal/joint/hand/command
  /aima/hal/imu/torso/state /aima/hal/imu/chest/state /aima/hal/odom/state
  /aima/mc/common/state /aima/mc/leg_odometry /aima/mc/locomotion/velocity /mc/upper_body_command
  /tf /tf_static
)
for t in "${TOPICS[@]}"; do
  f="$OUT/$(echo "${t#/}" | tr '/' '_')"
  ros2 topic info -v "$t" > "$f.info.txt" 2>&1
  timeout 8 ros2 topic echo --once "$t" > "$f.echo.txt" 2>&1
  timeout 7 ros2 topic hz "$t" > "$f.hz.txt" 2>&1
done
echo "referencia guardada en $OUT"
