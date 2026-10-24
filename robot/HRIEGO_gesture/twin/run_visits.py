"""4c: execute the 5 simulated visits (visits_v2) on the x2-twin, turn by turn, no video. Host-side orchestrator (podman on PATH via shim).
Per turn: preset -> c10 synced_playback.py (plan.json with required start delay + WAV, fail-closed on sha); POINT -> stream_point.py (SONIC base of the
turn, hold 1.5 s, overlay on the CTC anchor); NONE -> c07 stream_arms_waist.py --start-frame 100. After URS turns: waist/head zero reset (stream_point --zero).
usage: run_visits.py [--visits vip,regular,...] [--rep 1] [--only turn_id,...]"""
import sys as _sys, pathlib as _pl; _sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
from paths import laplace_root as _lr, semantic_corporea_root as _sc, py_physicalai as _pyp, conda_exe as _conda; _LR = _lr(); _SC = _sc()
import argparse, json, subprocess, time, shutil
from pathlib import Path
H = Path(__file__).resolve().parent; R = H / "runs/4c_visits"; V = H.parent / "visits_v2"
C10 = (_LR / "experiments/RCP-01/runs/20261021_rcp01c10_synced_preset_playback/scripts")
ENVS = ('source /opt/ros/humble/setup.bash; source /workspace/aimdk/install/local_setup.bash; source /tmp/aimdk_overlay/install/local_setup.bash; '
        'export RMW_IMPLEMENTATION=rmw_fastrtps_cpp; export PYTHONPATH=/workspace/x2_ultra/src:$PYTHONPATH')
ap = argparse.ArgumentParser(); ap.add_argument("--visits", default="vip,regular,guest,staff,external"); ap.add_argument("--rep", type=int, default=1)
ap.add_argument("--only", default=""); ap.add_argument("--hold-s", type=float, default=1.5)
a = ap.parse_args()
def sh(cmd, **kw): return subprocess.run(cmd, capture_output=True, text=True, **kw)
def box(cmd, timeout=150): return sh(["podman", "exec", "x2-twin", "bash", "-c", f"{ENVS}; cd /tmp/v4c && {cmd}"], timeout=timeout)
def cp_in(*fs):
    for f in fs: sh(["podman", "cp", str(f), "x2-twin:/tmp/v4c/"])
def cp_out(name, dst):
    r = sh(["podman", "cp", f"x2-twin:/tmp/v4c/{name}", str(dst)]); return r.returncode == 0
sh(["podman", "exec", "x2-twin", "mkdir", "-p", "/tmp/v4c"])
cp_in(H / "synced_playback.py", C10 / "mc_preset_helper.py", H / "stream_arms_waist.py", H / "stream_point.py", H / "point_targets.json", H / "runs/4aa_pointing/plans/RIGHT.point.json")
(R / "plans").mkdir(exist_ok=True)
only = set(filter(None, a.only.split(",")))
log = open(R / "logs/turn_runs.jsonl", "a")
for rk in a.visits.split(","):
    for l in open(V / rk / "tts_manifest.jsonl"):
        t = json.loads(l); tid = t["turn_id"]
        if only and tid not in only: continue
        name = f"{tid}_r{a.rep}"; emote = t["emote"]; kind = "NONE" if emote == "NONE" else "POINT" if emote.startswith("POINT") else "PRESET"
        ev = json.load(open(V / rk / f"anchor_plan/events/{tid}.event.json")) if (V / rk / f"anchor_plan/events/{tid}.event.json").exists() else None
        rec = {"turn": tid, "rank": rk, "kind": kind, "emote": emote, "rep": a.rep, "t_start_wall": time.time()}
        t0 = time.monotonic(); out = ""
        try:
            if kind == "PRESET":
                plan = V / rk / f"anchor_plan/plans/{tid}.plan.json"; wav = Path(t["wav_path"])
                cp_in(plan, wav)
                r = box(f"timeout 90 python3 synced_playback.py {plan.name} {wav.name} {name}.csv", 120); out = (r.stdout + r.stderr)[-600:]
                t1 = time.monotonic()
            elif kind == "POINT":
                an = json.load(open(V / rk / f"anchor_plan/events/{tid}.anchor.json")); D = emote.split("_")[1]
                pp = {"schema": "x2_point_4c.v1", "direction": D, "case_id": tid, "sample_rate_hz": an["sample_rate_hz"], "anchor_text": an["text"],
                      "anchor_start_sample": an["start_sample"], "anchor_end_sample": an["end_sample"], "sample_count": an["sample_count"], "wav_sha256": t["wav_sha256"],
                      "align": "onset (CTC start of the word)"}
                pf = R / f"plans/{tid}.point.json"; json.dump(pp, open(pf, "w"), indent=1)
                base = V / rk / f"sonic/{tid}/{tid}_arms.npz"; shutil.copy(base, R / f"plans/{tid}_arms.npz")
                cp_in(pf, base)
                r = box(f"timeout 120 python3 stream_point.py {base.name} {pf.name} {name}.csv --hold-s {a.hold_s}", 150); out = (r.stdout + r.stderr)[-600:]
                t1 = time.monotonic()
                box("timeout 60 python3 stream_point.py READY RIGHT.point.json zero.csv --zero --ramp-s 3", 80)
            else:
                base = V / rk / f"sonic/{tid}/{tid}_arms.npz"; cp_in(base)
                r = box(f"timeout 120 python3 stream_arms_waist.py {base.name} {name}.csv --start-frame 100 --hold-s 1.0", 150); out = (r.stdout + r.stderr)[-600:]
                t1 = time.monotonic()
                box("timeout 60 python3 stream_point.py READY RIGHT.point.json zero.csv --zero --ramp-s 3", 80)
        except Exception as e:  # noqa: BLE001
            out = f"EXC {e!r}"; t1 = time.monotonic()
        rec.update(wall_exec_s=t1 - t0, wall_turn_incl_reset_s=time.monotonic() - t0, stdout_tail=out, csv_ok=cp_out(f"{name}.csv", R / f"traces/{name}.csv"))
        if kind != "NONE": cp_out(f"{name}.json", R / f"traces/{name}.json")
        log.write(json.dumps(rec) + "\n"); log.flush()
        print(name, kind, f"{rec['wall_turn_incl_reset_s']:.1f}s", "csv" if rec["csv_ok"] else "NOCSV", out[-160:].replace("\n", " | "), flush=True)
        time.sleep(2)
