"""c12 host orchestrator (podman shim on PATH): per take builds TURN.json, runs stream_emote_turn.py in x2-twin, copies traces out, zero-resets waist/head after URS takes.
usage: run_c12.py --turns id1,id2|subset|all --variants V0,V1,V2 --reps 2 [--tag X]"""
import sys as _sys, pathlib as _pl; _sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
from paths import laplace_root as _lr, semantic_corporea_root as _sc, py_physicalai as _pyp, conda_exe as _conda; _LR = _lr(); _SC = _sc()
import argparse, json, subprocess, time
from pathlib import Path
H = Path(__file__).resolve().parent; R = H / "runs/c12_transitions"; V = H.parent / "visits_v2"
C8 = (_LR / "experiments/RCP-01/runs/20261021_rcp01c08_preset_latency_twin")
C10 = (_LR / "experiments/RCP-01/runs/20261021_rcp01c10_synced_preset_playback/scripts")
ENVS = ('source /opt/ros/humble/setup.bash; source /workspace/aimdk/install/local_setup.bash; source /tmp/aimdk_overlay/install/local_setup.bash; '
        'export RMW_IMPLEMENTATION=rmw_fastrtps_cpp; export PYTHONPATH=/workspace/x2_ultra/src:$PYTHONPATH')
SUBSET = ["v2visit_vip_t00_greeting", "v2visit_staff_t00_greeting", "v2visit_vip_t03_dialogue", "v2visit_vip_t02_dialogue", "v2visit_staff_t02_dialogue", "v2visit_regular_t05_farewell"]
ap = argparse.ArgumentParser(); ap.add_argument("--turns", default="subset"); ap.add_argument("--variants", default="V0,V1,V2"); ap.add_argument("--reps", type=int, default=1); ap.add_argument("--rep0", type=int, default=1)
a = ap.parse_args()
def sh(cmd, **kw): return subprocess.run(cmd, capture_output=True, text=True, **kw)
def box(cmd, timeout=150): return sh(["podman", "exec", "x2-twin", "bash", "-c", f"{ENVS}; cd /tmp/c12 && {cmd}"], timeout=timeout)
lat = json.load(open(C8 / "metrics/preset_latency.json"))["bindings"]
turns = {}
for rk in "vip regular guest staff external".split():
    for l in open(V / rk / "tts_manifest.jsonl"):
        t = json.loads(l)
        if t.get("motion_id") is not None: t["rank"] = rk; turns[t["turn_id"]] = t
ids = SUBSET if a.turns == "subset" else list(turns) if a.turns == "all" else a.turns.split(",")
sh(["podman", "exec", "x2-twin", "mkdir", "-p", "/tmp/c12"])
for f in (H / "emote_lib.py", H / "stream_emote_turn.py", C10 / "mc_preset_helper.py", H / "stream_point.py", H / "point_targets.json", H / "runs/4aa_pointing/plans/RIGHT.point.json"):
    sh(["podman", "cp", str(f), "x2-twin:/tmp/c12/"])
(R / "traces").mkdir(exist_ok=True); (R / "plans").mkdir(exist_ok=True)
log = open(R / "logs/takes.jsonl", "a")
for rep in range(a.rep0, a.rep0 + a.reps):
    for tid in ids:
        t = turns[tid]; rk = t["rank"]; ev = json.load(open(V / rk / f"anchor_plan/events/{tid}.event.json")); bk = f"{t['motion_id']}/{t['area']}"; b = lat[bk]
        anc = ev["anchor"]["start_sample"] / ev["audio"]["sample_rate_hz"]; dur = ev["audio"]["sample_count"] / ev["audio"]["sample_rate_hz"]
        lo, ls = b["lead_onset_s"]["median"], b["lead_stroke_s"]["median"]
        T = {"turn_id": tid, "rank": rk, "emote": t["emote"], "motion_id": t["motion_id"], "area": t["area"], "binding": bk, "anchor_s": anc, "dur_s": dur, "lead_onset_s": lo, "lead_stroke_s": ls,
             "policy_dispatch_s": {"stroke": anc - ls, "mid": anc - (lo + ls) / 2, "onset": anc - lo}, "max_initial_delay_s": 1.5, "preset_dur_s": b["duration_s"]["median"]}
        tf = R / f"plans/{tid}.turn.json"; json.dump(T, open(tf, "w"), indent=1); arms = V / rk / f"sonic/{tid}/{tid}_arms.npz"
        sh(["podman", "cp", str(tf), "x2-twin:/tmp/c12/"]); sh(["podman", "cp", str(arms), "x2-twin:/tmp/c12/"])
        for var in a.variants.split(","):
            name = f"{tid}_{var}_r{rep}"; t0 = time.monotonic()
            try:
                r = box(f"timeout 100 python3 stream_emote_turn.py {var} {tf.name} {arms.name} {name}.csv", 130); out = (r.stdout + r.stderr)[-500:]
            except Exception as e: out = f"EXC {e!r}"
            ok = all(sh(["podman", "cp", f"x2-twin:/tmp/c12/{name}.{x}", str(R / f'traces/{name}.{x}')]).returncode == 0 for x in ("csv", "json"))
            if var != "V0": box("timeout 60 python3 stream_point.py READY RIGHT.point.json zero.csv --zero --ramp-s 3", 80)
            log.write(json.dumps({"take": name, "turn": tid, "variant": var, "rep": rep, "ok": ok, "wall_s": time.monotonic() - t0, "tail": out}) + "\n"); log.flush()
            print(name, "ok" if ok else "NOFILES", f"{time.monotonic()-t0:.0f}s", out[-140:].replace("\n", " | "), flush=True); time.sleep(1.5)
