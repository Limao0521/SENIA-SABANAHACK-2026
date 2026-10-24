import os, sys, json, subprocess, time, threading
from pathlib import Path
HERE = Path(__file__).resolve().parent
O1B = HERE.parent / "o1b_onnx"; FACE = Path("/home/luis/GitHub/x2_ultra/Face")
IMGS = sorted(__import__("glob").glob("/home/luis/GitHub/Laplace/Face/data/evaluation_osfr02/W0*.jpg"))
REG = FACE / "data/institutional_identity.sqlite3"
def setup_face():
    """cwd/path/env as the Face live app uses them."""
    os.chdir(FACE); sys.path.insert(0, str(FACE)); sys.path.insert(0, str(FACE / "thirdparty/uniface"))
    os.environ.setdefault("UNIFACE_CACHE_DIR", str(FACE / "thirdparty/uniface-models"))
def smaps(pid="self"):
    d = {}
    for l in open(f"/proc/{pid}/smaps_rollup"):
        p = l.split()
        if len(p) >= 3 and p[2] == "kB": d[p[0].rstrip(":")] = int(p[1]) / 1024
    return d
def vram(pid=None):
    pid = str(pid or os.getpid())
    try:
        for l in subprocess.check_output(["nvidia-smi", "--query-compute-apps=pid,used_memory", "--format=csv,noheader,nounits"], text=True).splitlines():
            p, m = [x.strip() for x in l.split(",")]
            if p == pid: return int(m)
    except Exception: pass
    return 0
def mem(label, **kw):
    s = smaps(); r = dict(label=label, rss=round(s["Rss"]), pss=round(s["Pss"]), uss=round(s["Private_Clean"] + s["Private_Dirty"]), pss_anon=round(s.get("Pss_Anon", 0)), pss_file=round(s.get("Pss_File", 0)), shared=round(s["Shared_Clean"] + s["Shared_Dirty"]), vram=vram())
    r.update(kw); print("MEM", json.dumps(r), flush=True); return r
