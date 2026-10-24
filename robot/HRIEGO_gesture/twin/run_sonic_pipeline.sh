#!/usr/bin/env bash
# 4aa SONIC base S1-S5 from the 3 pointing WAVs. Run from HRIEGO_gesture/twin. Envs: physicalai (StreamTalk, SONIC), gmr (retarget), laplace (prep_arms).
set -eu
H="$(cd "$(dirname "$0")" && pwd)"; M="$H/runs/4aa_pointing/motion"; L=${LAPLACE_ROOT:-$HOME/GitHub/Laplace}/experiments/RCP-01/runs
C3=$L/20261020_rcp01c03_segment_scheduler/scripts; C7=$L/20261021_rcp01c07_ready_pose/scripts
CONDA=${CONDA_EXE:-${CONDA_BASE:-$HOME/anaconda3}/bin/conda}; DIRS=${DIRS:-"RIGHT LEFT FRONT"}
[[ ${SKIP_ST:-0} == 1 ]] || $CONDA run --no-capture-output -n physicalai python "$H/sonic_pipeline.py" $DIRS
for D in $DIRS; do
  n=P_$D
  $CONDA run --no-capture-output -n gmr python $C3/x2_retarget.py --model $n --input-dir $M/x2/inputs --output-dir $M/x2/retarget --force
  $CONDA run --no-capture-output -n physicalai python $C3/x2_sonic.py convert --model $n --retarget-dir $M/x2/retarget --sonic-out-dir $M/sonic --log-dir $M/logs --force
  X2_TRACE_DIR=$M/traces X2_TRACE_TAG=$n $CONDA run --no-capture-output -n physicalai python $C3/x2_sonic.py rollout --model $n --retarget-dir $M/x2/retarget --sonic-out-dir $M/sonic --log-dir $M/logs --trace-dir $M/traces --no-record --force
  $CONDA run --no-capture-output -n laplace python $C7/prep_arms.py $M/traces/full_$n.csv $M/${n}_arms.npz > $M/${n}_arms.json
done
