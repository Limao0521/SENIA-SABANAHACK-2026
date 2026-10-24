"""Memory snapshot helpers: smaps_rollup (RSS/PSS/USS anon vs file) + per-pid VRAM (nvidia-smi) + optional torch/tracemalloc."""
import json, os, subprocess, sys, time, ctypes, gc
T0 = time.monotonic()
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
def snap(label, log=None, pid="self", extra=None):
    s = smaps(pid); rp = None if pid == "self" else pid
    uss = s.get("Private_Clean", 0) + s.get("Private_Dirty", 0)
    r = dict(label=label, t=round(time.monotonic() - T0, 2), rss=round(s["Rss"]), pss=round(s["Pss"]), uss=round(uss), pss_anon=round(s.get("Pss_Anon", 0)), pss_file=round(s.get("Pss_File", 0)), vram=vram(rp))
    if "torch" in sys.modules and pid == "self":
        t = sys.modules["torch"]
        try:
            if t.cuda.is_initialized(): r["t_alloc"] = round(t.cuda.memory_allocated() / 2**20); r["t_resv"] = round(t.cuda.memory_reserved() / 2**20)
        except Exception: pass
    if extra: r.update(extra)
    print("SNAP", json.dumps(r), file=sys.stderr, flush=True)
    if log:
        with open(log, "a") as f: f.write(json.dumps(r) + "\n")
    return r
def trim():
    gc.collect()
    try: ctypes.CDLL("libc.so.6").malloc_trim(0)
    except Exception: pass

def top_objects(n=12):
    """Largest live numpy arrays / torch tensors (gc scan) -> list of (MB, kind, shape, dtype)."""
    import numpy as np
    out = []
    tt = sys.modules.get("torch")
    for o in gc.get_objects():
        try:
            if isinstance(o, np.ndarray) and o.base is None: out.append((o.nbytes / 2**20, "np", o.shape, str(o.dtype)))
            elif tt is not None and isinstance(o, tt.Tensor) and o.device.type == "cpu" and o.untyped_storage().nbytes() > 2**20: out.append((o.untyped_storage().nbytes() / 2**20, "torch", tuple(o.shape), str(o.dtype)))
        except Exception: pass
    out.sort(key=lambda x: -x[0]); return out[:n]
