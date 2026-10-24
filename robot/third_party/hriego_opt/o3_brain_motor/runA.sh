#!/bin/bash
cd "$(dirname "$0")"; M=$1
export PYTHONUNBUFFERED=1 MUJOCO_GL=egl OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2
systemd-run --user --scope -q -p MemoryMax=5G -p MemorySwapMax=0 taskset -c 0-3 ~/anaconda3/envs/physicalai/bin/python partA_run.py $M $2 > logs/A_$M$2.out 2> logs/A_$M$2.err
echo "exit $?" >> logs/A_$M$2.out
