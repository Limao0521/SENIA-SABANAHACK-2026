"""ONE process: StreamTalk (lean: fp16 cast + mmap retrieval) -> GMR (mjb lean, shared SMPL-X) -> SONIC (mjb). Cumulative snapshots."""
import os, sys, runpy
from pathlib import Path
H = Path(__file__).resolve().parent; sys.path.insert(0, str(H)); from memlib import snap
def seg(path, env, argv=None):
    os.environ.update(env); a = sys.argv[:]; sys.argv = argv or [path]; g = {"__file__": str(H / path), "__name__": "__main__"}
    exec(compile(open(H / path).read(), str(H / path), "exec"), g); sys.argv = a; return g
snap("m0_start", H / "logs/merged.jsonl")
seg("st_probe.py", dict(VAR="merged_st", HALF="cast", KD="mmap", KD_VERIFY="0", TRIM="1", RNG="load", CLIPS="c010"))
snap("m1_after_streamtalk", H / "logs/merged.jsonl")
seg("gmr_probe.py", dict(VAR="merged_gmr", MJB="lean", SUB="share", TRIM="1", CLIPS="c010,c001,c010"))
snap("m2_after_gmr", H / "logs/merged.jsonl")
seg("sonic_probe.py", dict(MJB="1"), ["sonic_probe.py", "full"])
snap("m3_after_sonic", H / "logs/merged.jsonl")
from memlib import trim; trim(); snap("m4_final_trim", H / "logs/merged.jsonl")
print("HWM", [l.strip() for l in open("/proc/self/status") if l.startswith("VmHWM")], file=sys.stderr)
