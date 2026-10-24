"""Shared helpers for the M2 online motion server (physicalai env, py3.12). Paths, memory probes, thread wrapper."""
import ctypes, gc, json, os, queue, subprocess, sys, threading, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
HRIEGO = HERE.parents[1]                     # HRIEGO_gesture root
M1 = HERE.parent / "m1_memory"
sys.path.insert(0, str(HRIEGO))
import paths as P                             # noqa: E402

CLIPS_JSON = HERE.parent / "t2_streamtalk_precision/clips.json"
MJB_DIR = M1 / "out/mjb"
KD_CACHE = M1 / "out/kd_cache"
RNG_PT = M1 / "logs/rng_prod.pt"
SITE_GMR = HERE / "site_gmr"                  # symlinks: mink + patched smplx from the gmr env


def trim():
    gc.collect()
    try: ctypes.CDLL("libc.so.6").malloc_trim(0)
    except Exception: pass


def smaps(pid="self"):
    d = {}
    for l in open(f"/proc/{pid}/smaps_rollup"):
        p = l.split()
        if len(p) >= 3 and p[2] == "kB": d[p[0].rstrip(":")] = int(p[1]) / 1024
    return d


def status_mb():
    d = {}
    for l in open("/proc/self/status"):
        if l.startswith(("VmRSS", "VmHWM")): d[l.split(":")[0]] = int(l.split()[1]) / 1024
    return d


def vram_mb(pid=None):
    pid = str(pid or os.getpid())
    try:
        for l in subprocess.check_output(["nvidia-smi", "--query-compute-apps=pid,used_memory", "--format=csv,noheader,nounits"], text=True).splitlines():
            p, m = [x.strip() for x in l.split(",")]
            if p == pid: return int(m)
    except Exception: pass
    return 0


class MemSampler(threading.Thread):
    """Samples PSS/RSS (smaps_rollup) every `dt` s; keeps the max and the series."""
    def __init__(self, dt=0.25):
        super().__init__(daemon=True); self.dt = dt; self.ev = threading.Event(); self.rows = []; self.t0 = time.perf_counter()
    def run(self):
        while not self.ev.is_set():
            try:
                s = smaps(); self.rows.append((time.perf_counter() - self.t0, s["Pss"], s["Rss"]))
            except Exception: pass
            self.ev.wait(self.dt)
    def stop(self):
        self.ev.set(); self.join(timeout=2)
    def peak(self, t_from=0.0):
        r = [x for x in self.rows if x[0] >= t_from]
        return (max(x[1] for x in r), max(x[2] for x in r)) if r else (0, 0)


class ThreadedIter:
    """Run a generator in a worker thread; iterate its items through a bounded queue (pipeline stage)."""
    _END = object()
    def __init__(self, gen, maxsize=4):
        self.q = queue.Queue(maxsize); self.gen = gen
        self.t = threading.Thread(target=self._run, daemon=True); self.t.start()
    def _run(self):
        try:
            for x in self.gen: self.q.put(x)
            self.q.put(self._END)
        except BaseException as e:
            self.q.put(e)
    def __iter__(self): return self
    def __next__(self):
        x = self.q.get()
        if x is self._END: raise StopIteration
        if isinstance(x, BaseException): raise x
        return x
