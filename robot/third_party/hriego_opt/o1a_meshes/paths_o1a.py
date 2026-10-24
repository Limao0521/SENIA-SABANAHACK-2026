"""Source models for O1a (audited, never edited) and shared helpers."""
import os, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1])); import paths as P
EXT = P.x2_external_root()
SRC = {"ik": P.x2_ik_dir() / "retarget/model/x2_mocap_toe_proxy.xml",
       "sonic": EXT / "sonic_x2_c9959443/assets/mjcf/x2_ultra.xml"}
MODELS = HERE / "models"
def rss_pss():
    d = {}
    for l in open("/proc/self/smaps_rollup"):
        p = l.split()
        if len(p) >= 3 and p[2] == "kB": d[p[0].rstrip(":")] = int(p[1]) / 1024
    return d["Rss"], d["Pss"]
def trim():
    import gc, ctypes; gc.collect(); ctypes.CDLL("libc.so.6").malloc_trim(0)
