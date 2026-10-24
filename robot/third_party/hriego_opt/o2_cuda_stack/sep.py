"""Orchestrator for (a): uf worker, then st worker alive together; external smaps/VRAM of both pids. python sep.py [uf,st]"""
import subprocess, sys, os, time, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); from common import *
modes = (sys.argv[1] if len(sys.argv) > 1 else "uf,st").split(","); tmp = HERE / "logs/flags"; tmp.mkdir(exist_ok=True); done = tmp / "done"; done.unlink(missing_ok=True)
procs = {}; rows = {}
def meas(tag):
    tot = dict(rss=0, pss=0, uss=0, pss_anon=0, pss_file=0, vram=0)
    for m, p in procs.items():
        s = smaps(p.pid); r = dict(rss=s["Rss"], pss=s["Pss"], uss=s["Private_Clean"] + s["Private_Dirty"], pss_anon=s.get("Pss_Anon", 0), pss_file=s.get("Pss_File", 0), rss_file=s.get("Rss_File", 0) if "Rss_File" in s else None, shared=s["Shared_Clean"] + s["Shared_Dirty"], vram=vram(p.pid))
        rows[f"{tag}:{m}"] = {k: (round(v) if v is not None else None) for k, v in r.items()}
        for k in tot: tot[k] += r[k]
    rows[f"{tag}:SUM"] = {k: round(v) for k, v in tot.items()}
for m in modes:
    rf = tmp / f"ready_{m}"; rf.unlink(missing_ok=True)
    procs[m] = subprocess.Popen([os.environ.get("WPY", sys.executable), str(HERE / "worker.py"), m, str(rf), str(done)], cwd="/tmp", stdout=open(HERE / f"logs/sep_{m}{os.environ.get("TAGX","")}.log", "w"), stderr=subprocess.STDOUT)
    while not rf.exists():
        if procs[m].poll() is not None: sys.exit(f"{m} died")
        time.sleep(0.5)
    meas(f"after_{m}")
    os.system("free -m | sed -n 2p")
time.sleep(1); meas("final")
print(json.dumps(rows, indent=1)); json.dump(rows, open(HERE / f"logs/sep_{"_".join(modes)}{os.environ.get("TAGX","")}.json", "w"), indent=1)
done.write_text("1")
for p in procs.values(): p.wait()
done.unlink()
