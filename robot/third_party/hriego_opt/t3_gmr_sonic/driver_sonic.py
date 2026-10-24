"""usage: driver_sonic.py <tag> ; runs SONIC convert+rollout for every out/<tag>/<variant>/<clip>/retarget, then prep_arms -> arms.npz"""
import json, os, subprocess, sys, time
from pathlib import Path
H = Path(__file__).parent; sys.path.insert(0, str(H.parent.parent)); import paths as P
tag = sys.argv[1]; OUT = H / "out"
def mem_av(): return [int(l.split()[1]) for l in open("/proc/meminfo") if l.startswith("MemAvailable")][0] / 2**20
assert mem_av() > 9
t0 = time.monotonic()
p = subprocess.Popen([P.py_physicalai(), str(H.parent.parent / "workers/sonic_worker.py")], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=open(OUT / f"logs/sonic_{tag}.log", "w"), text=True,
                     cwd=H.parent.parent / "workers", env={**os.environ, "PYTHONUNBUFFERED": "1"})
ready = json.loads(p.stdout.readline()); print(ready, flush=True)
rows = []
for rd in sorted((OUT / tag).glob("*/*/retarget")):
    clip = rd.parent.name; v = rd.parent.parent.name; b = rd.parent
    if json.load(open(rd / f"{clip}_DECISION_SUMMARY.json"))["status"] != "PASS": pass
    t = time.monotonic()
    p.stdin.write(json.dumps(dict(id=clip, name=clip, retarget_dir=str(rd), sonic_dir=str(b / "sonic"), log_dir=str(b / "logs"), trace_dir=str(b / "traces"))) + "\n"); p.stdin.flush()
    r = json.loads(p.stdout.readline()); r.update(variant=v, clip=clip)
    if r.get("ok"):
        o = subprocess.run([P.py_physicalai(), str(H.parent.parent / "motion/prep_arms.py"), r["trace"], str(b / "arms.npz")], capture_output=True, text=True)
        r["prep"] = json.loads(o.stdout) if o.returncode == 0 else {"error": o.stderr[-300:]}
    rows.append(r); print(v, clip, r.get("ok"), r.get("wall_s"), r.get("error"), flush=True)
    json.dump(dict(ready=ready, rows=rows), open(OUT / f"sonic_{tag}.json", "w"), indent=1)
p.stdin.write('{"cmd":"quit"}\n'); p.stdin.flush(); p.wait(timeout=30)
