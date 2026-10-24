#!/bin/bash
cd "$(dirname "$0")"; TAG=$1; shift
export PYTHONUNBUFFERED=1 MUJOCO_GL=egl OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2
systemd-run --user --scope -q -p MemoryMax=6G -p MemorySwapMax=0 taskset -c 0-3 ~/anaconda3/envs/physicalai/bin/python brain_run.py --tag $TAG "$@" > logs/B_$TAG.out 2> logs/B_$TAG.err
echo "exit $?" >> logs/B_$TAG.out
