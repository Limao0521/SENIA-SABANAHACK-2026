"""Baseline (full-mesh) .mjb of the audited sources -> out/<name>_full_<mujoco>.mjb (700 MB total, not shipped)."""
import sys, mujoco; from paths_o1a import *
for n in ("ik", "sonic"):
    mujoco.mj_saveModel(mujoco.MjModel.from_xml_path(str(SRC[n])), str(HERE / f"out/{n}_full_{mujoco.__version__}.mjb"))
