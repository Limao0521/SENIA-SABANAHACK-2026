"""Orchestrator-side helpers: worker subprocess wrapper, MemAvailable guard, RSS/VRAM sampler."""
import csv, json, subprocess, threading, time, os
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # HRIEGO_gesture root -> paths.py
import paths as P
# interpreters from $HRIEGO_PY_PHYSICALAI / $HRIEGO_PY_GMR (or $CONDA_PREFIX_PHYSICALAI / $CONDA_PREFIX_GMR); see paths.py
PY = {"st": (P.py_physicalai(), "st_worker.py", 4.3), "gmr": (P.py_gmr(), "gmr_worker.py", 3.9),
      "sonic": (P.py_physicalai(), "sonic_worker.py", 2.2)}
HERE = Path(__file__).parent
def mem_avail_gb():
    for l in open("/proc/meminfo"):
        if l.startswith("MemAvailable"): return int(l.split()[1]) / 2**20
class Worker:
    def __init__(self, kind, logdir, margin_gb=4.0, sampler=None):
        py, script, peak = PY[kind]; self.kind = kind
        av = mem_avail_gb(); assert av >= peak + margin_gb, f"{kind}: MemAvailable {av:.1f} GB < peak {peak}+{margin_gb}"
        self.t_spawn = time.monotonic(); self.lock = threading.Lock()
        self.p = subprocess.Popen([py, str(HERE / script)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=open(Path(logdir) / f"{kind}_worker.log", "w"), text=True, cwd=HERE, env={**os.environ, "PYTHONUNBUFFERED": "1"})
        if sampler: sampler.pids[kind] = self.p.pid
        self.ready = json.loads(self.p.stdout.readline()); self.ready["spawn_to_ready_s"] = round(time.monotonic() - self.t_spawn, 2)
    def call(self, **req):
        with self.lock:
            self.p.stdin.write(json.dumps(req) + "\n"); self.p.stdin.flush(); t = time.monotonic(); r = json.loads(self.p.stdout.readline()); r["t_end"] = time.monotonic(); r["t_start"] = t; return r
    def stop(self):
        try: self.p.stdin.write('{"cmd":"quit"}\n'); self.p.stdin.flush(); self.p.wait(timeout=20)
        except Exception: self.p.kill(); self.p.wait()
class Sampler(threading.Thread):
    """1 s: per-worker VmRSS/VmHWM + MemAvailable; 0.5 s: nvidia-smi total + per-pid VRAM."""
    def __init__(self, path): super().__init__(daemon=True); self.pids = {}; self.rows = []; self.stop_ev = threading.Event(); self.path = path; self.t0 = time.monotonic()
    def run(self):
        last = 0
        while not self.stop_ev.is_set():
            t = time.monotonic() - self.t0; row = {"t": round(t, 2), "mem_avail_gb": round(mem_avail_gb(), 2)}
            for k, pid in list(self.pids.items()):
                try:
                    d = dict(l.split(":") for l in open(f"/proc/{pid}/status") if l.startswith(("VmRSS", "VmHWM")))
                    row[f"{k}_rss_mb"] = int(d["VmRSS"].split()[0]) // 1024; row[f"{k}_hwm_mb"] = int(d["VmHWM"].split()[0]) // 1024
                except Exception: pass
            try:
                row["vram_total_mb"] = int(subprocess.check_output(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"], text=True).split()[0])
                for l in subprocess.check_output(["nvidia-smi", "--query-compute-apps=pid,used_memory", "--format=csv,noheader,nounits"], text=True).splitlines():
                    pid, mb = [x.strip() for x in l.split(",")]
                    for k, p in self.pids.items():
                        if str(p) == pid: row[f"{k}_vram_mb"] = int(mb)
            except Exception: pass
            self.rows.append(row); time.sleep(0.5)
    def stop(self):
        self.stop_ev.set(); self.join(timeout=5)
        keys = sorted({k for r in self.rows for k in r}, key=lambda k: (k != "t", k))
        with open(self.path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=keys); w.writeheader(); w.writerows(self.rows)
    def peak(self, key): v = [r[key] for r in self.rows if key in r]; return max(v) if v else None
