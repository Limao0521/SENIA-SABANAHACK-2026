"""c10: dispatch one SDK preset on the twin at the sample a c09 plan chose, on a common monotonic audio clock.

Runs inside x2-twin (PYTHONPATH=/workspace/x2_ultra/src), robot STAND_DEFAULT + STABLE. The twin has no audio: playback is virtual,
audio_start_monotonic = T0 + start_delay_s is only logged. Dispatch happens when monotonic reaches audio_start + dispatch_sample/Fs.
Fail-closed: plan status != pending, or WAV sha256 != plan.audio.sha256 -> no dispatch (meta records why).
usage: synced_playback.py PLAN.json WAV OUT.csv [--qpos-out X.npz]
"""
import argparse, csv, hashlib, random, json, math, queue, subprocess, threading, time
from nav_msgs.msg import Odometry
from rclpy.qos import qos_profile_sensor_data
from x2_ultra.real.link import RobotLink
from x2_ultra.robot.joints import ARM_JOINTS

TICK, MARGIN_S, TAIL_S, MAX_S = 0.02, 1.0, 1.0, 25.0
ap = argparse.ArgumentParser(); ap.add_argument("plan"); ap.add_argument("wav"); ap.add_argument("out")
ap.add_argument("--qpos-out", default=None)
a = ap.parse_args()
plan = json.load(open(a.plan))
meta = {"utterance_id": plan["utterance_id"], "binding_key": plan["binding_key"], "align": plan["policy"]["align"],
        "plan_status": plan["status"], "dispatched": False, "fail_reason": None}
sha = hashlib.sha256(open(a.wav, "rb").read()).hexdigest()
meta["wav_sha256"] = sha
if plan["status"] != "pending":
    meta["fail_reason"] = f"plan status {plan['status']}"
elif sha != plan["audio"]["sha256"]:
    meta["fail_reason"] = "wav sha256 mismatch"
elif not plan["dispatches"]:
    meta["fail_reason"] = "plan has no dispatches"
if meta["fail_reason"]:
    json.dump(meta, open(a.out.replace(".csv", ".json"), "w")); print(json.dumps(meta)); raise SystemExit(0)
d = plan["dispatches"][0]
FS = plan["audio"]["sample_rate_hz"]
delay_s = plan["required_audio_start_delay_samples"] / FS
rel_dispatch_s = plan["dispatch_sample"] / FS

link = RobotLink("synced_playback")
pose = {"z": None, "tilt": None}
def on_odom(m):
    p, q = m.pose.pose.position, m.pose.pose.orientation
    pose.update(z=p.z, tilt=math.degrees(2 * math.asin(min(1.0, math.hypot(q.x, q.y)))))
link.node.create_subscription(Odometry, "/aima/hal/odom/state", on_odom, qos_profile_sensor_data)
waist = {}
link.node.create_subscription(link.M.JointStateArray, "/aima/hal/joint/waist/state",
                              lambda m: waist.update(q=[j.position for j in m.joints]), qos_profile_sensor_data)
ENV = ("source /opt/ros/humble/setup.bash; source /workspace/aimdk/install/local_setup.bash; "
       "source /tmp/aimdk_mc_overlay/install/local_setup.bash; export RMW_IMPLEMENTATION=rmw_fastrtps_cpp; "
       "exec python3 -u mc_preset_helper.py")
helper = subprocess.Popen(["bash", "-c", ENV], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
replies = queue.Queue()
threading.Thread(target=lambda: [replies.put(json.loads(l)) for l in helper.stdout], daemon=True).start()
assert replies.get(timeout=30)["ready"], "helper: services not found on domain 77"
def tell(cmd):
    helper.stdin.write(cmd + "\n"); helper.stdin.flush()
W = ("waist_yaw", "waist_pitch", "waist_roll")
shared, qpos_log = None, []
if a.qpos_out:
    from x2_ultra.twin.model import build_twin_model
    from x2_ultra.twin.shared import TWIN_SHM_NAME, SharedState
    shared = SharedState(build_twin_model("vacia").nq, name=TWIN_SHM_NAME)
t_end = time.monotonic() + 8
while time.monotonic() < t_end and (link.latest("arms") is None or link.latest("head") is None or not waist or pose["z"] is None):
    time.sleep(0.02)
assert link.wait_stable(15.0), "MC not STABLE"
obs0 = link.observed(graph=False)
assert obs0.mode == "STAND_DEFAULT", f"mode {obs0.mode}"

# sub-tick random phase: otherwise dispatch (= T0 when the start delay equals -dispatch) would always sit on the 50 Hz logging grid
PHASE_S = random.uniform(0, TICK)
T0 = time.monotonic() + MARGIN_S + PHASE_S
audio_start = T0 + delay_s
t_plan = audio_start + rel_dispatch_s
meta.update(phase_s=PHASE_S, fs=FS, start_delay_s=delay_s, dispatch_sample=plan["dispatch_sample"], T0=T0, audio_start_monotonic=audio_start,
            planned_dispatch_monotonic=t_plan, mode_before=obs0.mode, motion_id=d["motion_id"], area=d["area"])
rows, task, t_disp, t_resp, last_get, get_state = [], 0, None, None, 0, None
t_idle, seen_busy, k = None, False, 0
t0 = time.monotonic()
while True:
    now = time.monotonic()
    if t_disp is None and 0 <= t_plan - now < TICK:   # sleep to just before, then busy-wait the last ms
        rem = t_plan - time.monotonic()
        if rem > 0.003: time.sleep(rem - 0.003)
        while time.monotonic() < t_plan: pass
        t_disp = time.monotonic(); tell(f"set {d['motion_id']} {d['area']} 0"); now = t_disp
    while True:
        try: rp = replies.get_nowait()
        except queue.Empty: break
        if rp["op"] == "set":
            t_resp = rp["t_resp"]; task = rp["task_id"] or 0
            meta.update(set_code=rp["code"], set_state=rp["state"], t_call_helper=rp["t_call"])
        else:
            get_state = rp["state"]
    if t_resp is not None and now - last_get >= 0.1:
        tell(f"get {task}"); last_get = now
    obs = link.observed(graph=False)
    busy = (obs.player_state or 0) != 0
    if t_disp is not None and busy: seen_busy = True
    if seen_busy and not busy and t_idle is None: t_idle = now
    if shared is not None:
        snap = shared.read(); qpos_log.append(snap[0].copy() if snap is not None else qpos_log[-1])
    arms, head, w = link.latest("arms") or {}, link.latest("head") or {}, waist.get("q") or [None] * 3
    rows.append({"t": round(now, 4), "dispatched": int(t_disp is not None), "mode": obs.mode, "fsm": obs.fsm,
                 "player_state": obs.player_state, "motion": obs.motion, "get_state": get_state,
                 "pelvis_z": pose["z"], "tilt_deg": pose["tilt"],
                 **{n: arms.get(n) for n in ARM_JOINTS}, **dict(zip(W, w)),
                 "head_yaw": head.get("head_yaw_joint"), "head_pitch": head.get("head_pitch_joint")})
    if t_disp is not None and ((t_idle is not None and now - t_idle >= TAIL_S) or now - t_disp > MAX_S): break
    if t_disp is None and now - t_plan > 1: meta["fail_reason"] = "dispatch time missed"; break
    k += 1; time.sleep(max(0.0, t0 + k * TICK - time.monotonic()))
meta.update(dispatched=t_disp is not None, t_dispatch=t_disp, t_response=t_resp, t_idle=t_idle, completed=t_idle is not None,
            jitter_ms=None if t_disp is None else 1000 * (t_disp - t_plan), get_state_last=get_state,
            service_rtt_s=None if t_resp is None else t_resp - t_disp)
ok = link.wait_stable(15.0); obs = link.observed(graph=False)
if obs.mode != "STAND_DEFAULT":
    try: link.request_mode("STAND_DEFAULT", busy_retry_s=12.0); ok = link.wait_stable(15.0)
    except Exception as exc: meta["stand_error"] = str(exc)
meta["stable_after"] = bool(ok); meta["mode_after"] = link.observed(graph=False).mode
with open(a.out, "w", newline="") as f:
    wr = csv.DictWriter(f, fieldnames=list(rows[0])); wr.writeheader(); wr.writerows(rows)
if shared is not None:
    import numpy as np
    np.savez(a.qpos_out, qpos=np.array(qpos_log), t=np.array([r["t"] for r in rows]), dispatched=np.array([r["dispatched"] for r in rows]))
json.dump(meta, open(a.out.replace(".csv", ".json"), "w"))
print(json.dumps(meta))
helper.stdin.write("quit\n"); helper.stdin.flush(); helper.wait(5)
link.close()
