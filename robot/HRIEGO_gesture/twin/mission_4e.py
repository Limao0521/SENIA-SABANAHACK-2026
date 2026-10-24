"""4e: full mission on the x2-twin for one visitor rank: walk -> ARC (vx>0 + wz) -> walk -> stop -> STABLE -> settle -> 6 visit turns (as 4c).
Host orchestrator (podman via shim). Background logger in the container records odometry / MC mode / twin qpos at 50 Hz on the common monotonic clock.
usage: mission_4e.py RANK [--lead 4.5] [--settle-s 3]   (RANK in vip|staff|guest)"""
import sys as _sys, pathlib as _pl; _sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
from paths import laplace_root as _lr, semantic_corporea_root as _sc, py_physicalai as _pyp, conda_exe as _conda; _LR = _lr(); _SC = _sc()
import argparse, json, subprocess, time, shutil
from pathlib import Path
H = Path(__file__).resolve().parent; R = H / "runs/4e_missions"; V = H.parent / "visits_v2"
C10 = (_LR / "experiments/RCP-01/runs/20261021_rcp01c10_synced_preset_playback/scripts")
ENVS = ('source /opt/ros/humble/setup.bash; source /workspace/aimdk/install/local_setup.bash; source /tmp/aimdk_overlay/install/local_setup.bash; '
        'export RMW_IMPLEMENTATION=rmw_fastrtps_cpp; export PYTHONPATH=/workspace/x2_ultra/src:$PYTHONPATH')
ap = argparse.ArgumentParser(); ap.add_argument("rank"); ap.add_argument("--lead", type=float, default=4.5); ap.add_argument("--settle-s", type=float, default=3.0)
ap.add_argument("--hold-s", type=float, default=1.5); ap.add_argument("--skip-walk", action="store_true")
a = ap.parse_args(); rk = a.rank
def sh(cmd, **kw): return subprocess.run(cmd, capture_output=True, text=True, **kw)
def box(cmd, timeout=150): return sh(["podman", "exec", "x2-twin", "bash", "-c", f"{ENVS}; cd /tmp/v4e && {cmd}"], timeout=timeout)
def cp_in(*fs):
    for f in fs: sh(["podman", "cp", str(f), "x2-twin:/tmp/v4e/"])
def cp_out(name, dst):
    return sh(["podman", "cp", f"x2-twin:/tmp/v4e/{name}", str(dst)]).returncode == 0
(R / "traces").mkdir(exist_ok=True); (R / "plans").mkdir(exist_ok=True); (R / "logs").mkdir(exist_ok=True)
sh(["podman", "exec", "x2-twin", "mkdir", "-p", "/tmp/v4e"])
cp_in(H / "synced_playback.py", C10 / "mc_preset_helper.py", H / "stream_arms_waist_4e.py", H / "stream_point.py", H / "point_targets.json",
      H / "runs/4aa_pointing/plans/RIGHT.point.json", H / "mission_log.py", H / "walk_arc.py")
NAME = f"m_{rk}"
sh(["podman", "exec", "-d", "x2-twin", "bash", "-c", f"{ENVS}; cd /tmp/v4e && python3 mission_log.py {NAME} > log_{NAME}.txt 2>&1"]); time.sleep(3)
logf = open(R / f"logs/{rk}_mission.jsonl", "w")
def L(d): logf.write(json.dumps(d) + "\n"); logf.flush(); print(json.dumps(d)[:300], flush=True)
L({"event": "mission_start", "rank": rk, "t_mono": time.monotonic(), "lead_deg": a.lead})
if not a.skip_walk:
    r = box(f"python3 walk_arc.py {NAME}_walk --turn-lead-deg {a.lead}", 120); L({"event": "walk_done", "t_mono": time.monotonic(), "out": r.stdout[-1500:], "err": r.stderr[-300:]})
    for f in (f"{NAME}_walk_events.json", f"{NAME}_walk_cmd.csv"): cp_out(f, R / "traces" / f)
time.sleep(a.settle_s); L({"event": "settle_done", "t_mono": time.monotonic()})
for l in open(V / rk / "tts_manifest.jsonl"):
    t = json.loads(l); tid = t["turn_id"]; name = f"{tid}_r1"; emote = t["emote"]; kind = "NONE" if emote == "NONE" else "POINT" if emote.startswith("POINT") else "PRESET"
    rec = {"turn": tid, "rank": rk, "kind": kind, "emote": emote, "rep": 1, "t_start_mono": time.monotonic()}
    t0 = time.monotonic(); out = ""
    try:
        if kind == "PRESET":
            plan = V / rk / f"anchor_plan/plans/{tid}.plan.json"; wav = Path(t["wav_path"]); cp_in(plan, wav)
            r = box(f"timeout 90 python3 synced_playback.py {plan.name} {wav.name} {name}.csv", 120); out = (r.stdout + r.stderr)[-600:]; t1 = time.monotonic()
        elif kind == "POINT":
            an = json.load(open(V / rk / f"anchor_plan/events/{tid}.anchor.json")); D = emote.split("_")[1]
            pp = {"schema": "x2_point_4c.v1", "direction": D, "case_id": tid, "sample_rate_hz": an["sample_rate_hz"], "anchor_text": an["text"],
                  "anchor_start_sample": an["start_sample"], "anchor_end_sample": an["end_sample"], "sample_count": an["sample_count"], "wav_sha256": t["wav_sha256"],
                  "align": "onset (CTC start of the word)"}
            pf = R / f"plans/{tid}.point.json"; json.dump(pp, open(pf, "w"), indent=1)
            base = V / rk / f"sonic/{tid}/{tid}_arms.npz"; shutil.copy(base, R / f"plans/{tid}_arms.npz"); cp_in(pf, base)
            r = box(f"timeout 120 python3 stream_point.py {base.name} {pf.name} {name}.csv --hold-s {a.hold_s}", 150); out = (r.stdout + r.stderr)[-600:]; t1 = time.monotonic()
            box("timeout 60 python3 stream_point.py READY RIGHT.point.json zero.csv --zero --ramp-s 3", 80)
        else:
            base = V / rk / f"sonic/{tid}/{tid}_arms.npz"; cp_in(base)
            r = box(f"timeout 120 python3 stream_arms_waist_4e.py {base.name} {name}.csv --start-frame 100 --hold-s 1.0", 150); out = (r.stdout + r.stderr)[-600:]; t1 = time.monotonic()
            box("timeout 60 python3 stream_point.py READY RIGHT.point.json zero.csv --zero --ramp-s 3", 80)
    except Exception as e:  # noqa: BLE001
        out = f"EXC {e!r}"; t1 = time.monotonic()
    rec.update(wall_exec_s=t1 - t0, wall_turn_incl_reset_s=time.monotonic() - t0, t_end_mono=time.monotonic(), stdout_tail=out, csv_ok=cp_out(f"{name}.csv", R / f"traces/{name}.csv"))
    if kind != "NONE" or True: cp_out(f"{name}.json", R / f"traces/{name}.json")
    with open(R / "logs/turn_runs.jsonl", "a") as f: f.write(json.dumps(rec) + "\n")
    L({"event": "turn", **{k: v for k, v in rec.items() if k != "stdout_tail"}, "tail": out[-160:]})
    time.sleep(2)
time.sleep(2)
sh(["podman", "exec", "x2-twin", "touch", f"/tmp/v4e/STOP_{NAME}"]); time.sleep(4)
for f in (f"{NAME}_log.csv", f"{NAME}_qpos.npz"): cp_out(f, R / "traces" / f)
L({"event": "mission_end", "t_mono": time.monotonic()})
