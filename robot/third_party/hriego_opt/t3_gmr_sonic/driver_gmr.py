"""Run GMR worker (solver via env) over a clip list. usage: driver_gmr.py <solver> <outtag> <variant,variant,...>"""
import json, os, subprocess, sys, time
from pathlib import Path
H = Path(__file__).parent; sys.path.insert(0, str(H.parent.parent)); import paths as P
solver, tag, variants = sys.argv[1], sys.argv[2], sys.argv[3].split(",")
SRC = H.parent / "t2_streamtalk_precision/out"; OUT = H / "out"
def cpu_t(pid):
    f = open(f"/proc/{pid}/stat").read().rsplit(")", 1)[1].split(); return (int(f[11]) + int(f[12])) / os.sysconf("SC_CLK_TCK")
def threads(pid): return int([l for l in open(f"/proc/{pid}/status") if l.startswith("Threads")][0].split()[1])
def mem_av(): return [int(l.split()[1]) for l in open("/proc/meminfo") if l.startswith("MemAvailable")][0] / 2**20
assert mem_av() > 9, mem_av()
(OUT / "logs").mkdir(parents=True, exist_ok=True)
t0 = time.monotonic()
p = subprocess.Popen([P.py_gmr(), str(H / "gmr_worker_t3.py")], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=open(OUT / f"logs/gmr_{tag}.log", "w"), text=True,
                     env={**os.environ, "PYTHONUNBUFFERED": "1", "GMR_SOLVER": solver})
ready = json.loads(p.stdout.readline()); ready["spawn_to_ready_s"] = round(time.monotonic() - t0, 2); print(ready, flush=True)
rows = []
for v in variants:
    for clip in sorted(json.load(open(SRC / v / "run.json")).get("clips", {}) if False else [d.name for d in (SRC / v).iterdir() if d.is_dir()]):
        npz = SRC / v / clip / f"{clip}.npz"
        if not npz.exists(): continue
        c0 = cpu_t(p.pid); t = time.monotonic()
        p.stdin.write(json.dumps(dict(id=clip, smplx=str(npz), name=clip, out_dir=str(OUT / tag / v / clip / "retarget"))) + "\n"); p.stdin.flush()
        r = json.loads(p.stdout.readline()); wall = time.monotonic() - t
        r.update(variant=v, clip=clip, solver=solver, wall_driver_s=round(wall, 2), cpu_s=round(cpu_t(p.pid) - c0, 2), threads=threads(p.pid), first=not rows)
        r.pop("decision", None); rows.append(r); print({k: r[k] for k in ("variant", "clip", "status", "wall_s", "cpu_s", "rss_mb", "hwm_mb", "threads") if k in r}, flush=True)
        json.dump(dict(ready=ready, rows=rows), open(OUT / f"gmr_{tag}.json", "w"), indent=1)
p.stdin.write('{"cmd":"quit"}\n'); p.stdin.flush(); p.wait(timeout=30)
