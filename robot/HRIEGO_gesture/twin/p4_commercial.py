"""P4 host orchestrator (podman shim on PATH): copies helpers + timeline/ref into x2-twin, runs p4_robot_run.py per robot sequentially, copies traces + qpos out.
usage: p4_commercial.py ROBOTS(ultra,original) REP"""
import sys as _sys, pathlib as _pl; _sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
from paths import laplace_root as _lr, semantic_corporea_root as _sc, py_physicalai as _pyp, conda_exe as _conda; _LR = _lr(); _SC = _sc()
import json, subprocess, sys, time
from pathlib import Path
H = Path(__file__).resolve().parent; O = H / "runs/p4_commercial"
C10 = (_LR / "experiments/RCP-01/runs/20261021_rcp01c10_synced_preset_playback/scripts")
ENVS = ('source /opt/ros/humble/setup.bash; source /workspace/aimdk/install/local_setup.bash; source /tmp/aimdk_overlay/install/local_setup.bash; '
        'export RMW_IMPLEMENTATION=rmw_fastrtps_cpp; export PYTHONPATH=/workspace/x2_ultra/src:$PYTHONPATH')
def sh(cmd, **kw): return subprocess.run(cmd, capture_output=True, text=True, **kw)
def box(cmd, timeout=400): return sh(["podman", "exec", "x2-twin", "bash", "-c", f"{ENVS}; cd /tmp/p4 && {cmd}"], timeout=timeout)
sh(["podman", "exec", "x2-twin", "mkdir", "-p", "/tmp/p4"])
for f in (H / "emote_lib.py", H / "p4_robot_run.py", C10 / "mc_preset_helper.py", H / "stream_point.py", H / "point_targets.json", H / "runs/4aa_pointing/plans/RIGHT.point.json"): sh(["podman", "cp", str(f), "x2-twin:/tmp/p4/"])
robots, rep = sys.argv[1].split(","), sys.argv[2]
for rb in robots:
    for f in (O / f"timeline_{rb}.json", O / f"plans/ref_{rb}.npz"): sh(["podman", "cp", str(f), "x2-twin:/tmp/p4/"])
    name = f"{rb}_r{rep}"; t0 = time.monotonic()
    try: r = box(f"timeout 330 python3 p4_robot_run.py timeline_{rb}.json ref_{rb}.npz {name}.csv {name}_qpos.npz", 360); out = (r.stdout + r.stderr)[-1500:]
    except Exception as e: out = f"EXC {e!r}"
    ok = all(sh(["podman", "cp", f"x2-twin:/tmp/p4/{x}", str(O / "traces" / x)]).returncode == 0 for x in (f"{name}.csv", f"{name}.json", f"{name}_qpos.npz"))
    box("timeout 60 python3 stream_point.py READY RIGHT.point.json zero.csv --zero --ramp-s 3", 80)
    open(O / "logs/runs.jsonl", "a").write(json.dumps({"take": name, "ok": ok, "wall_s": time.monotonic() - t0, "tail": out}) + "\n")
    print(name, "ok" if ok else "NOFILES", f"{time.monotonic()-t0:.0f}s", out[-600:], flush=True); time.sleep(3)
