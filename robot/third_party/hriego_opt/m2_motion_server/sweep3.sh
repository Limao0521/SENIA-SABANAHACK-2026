#!/bin/bash
cd "$(dirname "$0")"
while kill -0 43317 2>/dev/null; do sleep 2; done
R="--prefix fast --armed"
./run.sh n30_threads --solver native --rate 30 $R --pipeline threads
./run.sh n15_serial --solver native --rate 15 $R
./run.sh n50_serial --solver native --rate 50 $R
./run.sh a30_serial --solver analytic --rate 30 $R
./run.sh a15_serial --solver analytic --rate 15 $R
./run.sh a50_serial --solver analytic --rate 50 $R
./run.sh n15_threads --solver native --rate 15 $R --pipeline threads
./run.sh n50_threads --solver native --rate 50 $R --pipeline threads
./run.sh a15_threads --solver analytic --rate 15 $R --pipeline threads
./run.sh n30_threads_rt1 --solver native --rate 30 $R --pipeline threads --audio-rtf 1.0 --clips c010,c018,v_guest_t01_guidance_POINT,c001
echo ALLDONE3
