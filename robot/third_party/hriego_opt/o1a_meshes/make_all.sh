#!/bin/bash
# Regenerate everything: hulls, derived XMLs, .mjb per MuJoCo version (gmr=3.13, physicalai=3.11), baseline .mjb.
cd "$(dirname "$0")"
GMR=${HRIEGO_PY_GMR:-$HOME/anaconda3/envs/gmr/bin/python}; PAI=${HRIEGO_PY_PHYSICALAI:-$HOME/anaconda3/envs/physicalai/bin/python}
$GMR build_hulls.py
for n in ik sonic; do for v in v1 v2; do for py in $GMR $PAI; do $py build_variants.py $n $v --mjb; done; done; done
for py in $GMR $PAI; do $py make_full_mjb.py; done
sha256sum models/*.xml models/*.mjb models/hull_meshes/*.STL > models/SHA256SUMS
