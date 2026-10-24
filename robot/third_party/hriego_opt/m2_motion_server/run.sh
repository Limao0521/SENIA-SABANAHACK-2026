#!/bin/bash
# usage: run.sh <tag> <server.py args...>  -> Orin-like limits: 5 GB cgroup (no swap), CPU pinned to 4 cores
cd "$(dirname "$0")"; TAG=$1; shift
export PYTHONUNBUFFERED=1 MUJOCO_GL=egl OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2
systemd-run --user --scope -q -p MemoryMax=5G -p MemorySwapMax=0 taskset -c ${CPUS:-0-3} ~/anaconda3/envs/physicalai/bin/python server.py --tag $TAG "$@" > logs/$TAG.out 2> logs/$TAG.err
echo "exit $?" >> logs/$TAG.out
