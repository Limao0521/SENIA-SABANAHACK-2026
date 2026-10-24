"""4aa: POINTING overlay (right/left/front) on a base motion (READY static pose or SONIC arms), anchored to the QuartzNet CTC start of the
direction word of a Supertonic WAV, streamed into the X2 twin. Structure = c07 stream_arms_waist.py (URS 50 Hz arms + McBodyPose waist/head
sign control with Smith predictor), + c10 audio clock (T0 + random 0-20 ms phase) and per-tick dispatch log.

Timeline (audio seconds, 0 = first WAV sample = T0): stroke ts = anchor_start - lead (0.12); prep ramp [ts-0.8, ts] w 0->1 (quintic);
hold [ts, ts+hold_s] w=1 (hold_s in [1,2], default 1.5); recovery [ts+hold_s, +1.65] w 1->0 (quintic). Pointing-arm cmd = base + w*(target-base);
the other arm and waist follow the base. Head yaw toward the direction (LEFT +, RIGHT -, FRONT 0) scaled by w. Per-joint velocity limiter (2 rad/s
twin) on the pointing arm while the overlay is active; URDF clamp with a 0.05 rad margin. Runs inside x2-twin (see run_matrix.sh).
usage: stream_point.py BASE(READY|arms.npz) PLAN.json OUT.csv [--hold-s 1.5] [--qpos-out X.npz]
"""
import argparse, csv, json, math, random, time, os
import numpy as np
from collections import deque
from nav_msgs.msg import Odometry
from rclpy.qos import qos_profile_sensor_data
from x2_ultra.real.link import RobotLink
from x2_ultra.robot.joints import ARM_JOINTS

URS, SD, TICK = "UPPERBODY_REMOTE_SPLIT", "STAND_DEFAULT", 0.02
READY = np.array([0.2, 0.2, 0.0, -0.6, 0.0, 0.0, 0.0, 0.2, -0.2, 0.0, -0.6, 0.0, 0.0, 0.0])   # SONIC default arms (c07 ready_target_mj_arms)
HEAD_SIGN = {"LEFT": 1.0, "RIGHT": -1.0, "FRONT": 0.0}
LIMS = {}   # URDF v1.4.0 (prep_arms.py), +-0.05 margin
_R = {"shoulder_pitch": (-3.0805, 2.0333), "shoulder_yaw": (-2.5569, 2.5569), "elbow": (-2.3562, 0.0), "wrist_yaw": (-2.5569, 2.5569), "wrist_pitch": (-0.5236, 0.5236)}
for s in ("left", "right"):
    for p in ("shoulder_pitch", "shoulder_roll", "shoulder_yaw", "elbow", "wrist_yaw", "wrist_pitch", "wrist_roll"):
        if p == "shoulder_roll": lo, hi = (-0.0611, 3.0456) if s == "left" else (-3.0456, 0.0611)
        elif p == "wrist_roll": lo, hi = (-1.5097, 0.7243) if s == "left" else (-0.7243, 1.5097)
        else: lo, hi = _R[p]
        LIMS[f"{s}_{p}_joint"] = (lo + 0.05, hi - 0.05)
LO = np.array([LIMS[n][0] for n in ARM_JOINTS]); HI = np.array([LIMS[n][1] for n in ARM_JOINTS])

ap = argparse.ArgumentParser()
ap.add_argument("base"); ap.add_argument("plan"); ap.add_argument("out")
ap.add_argument("--targets", default="point_targets.json")
ap.add_argument("--hold-s", type=float, default=1.5, help="pointing hold at w=1, clamped to [1,2]")
ap.add_argument("--prep-s", type=float, default=0.8); ap.add_argument("--recov-s", type=float, default=1.65)
ap.add_argument("--lead-s", type=float, default=0.12)
ap.add_argument("--vlim", type=float, default=2.0, help="per-joint velocity limiter of the pointing arm while the overlay is active [rad/s]")
ap.add_argument("--head-amp", type=float, default=0.30, help="head yaw toward the direction [rad]")
ap.add_argument("--waist-amp", type=float, default=0.0, help="optional waist twist toward the direction [rad]")
ap.add_argument("--start-frame", type=int, default=100)
ap.add_argument("--ramp-s", type=float, default=2.0); ap.add_argument("--ramp-rate", type=float, default=0.5)
ap.add_argument("--no-gesture", action="store_true", help="control: stream the base only")
ap.add_argument("--zero", action="store_true", help="only drive waist/head yaw back to 0 (no clip)")
ap.add_argument("--qpos-out", default=None)
a = ap.parse_args()
random.seed(int(os.environ.get("V2_SEED", "0")))
hold_s = float(np.clip(a.hold_s, 1.0, 2.0))

plan = json.load(open(a.plan)); D = plan["direction"]; FS = plan["sample_rate_hz"]
anchor_start_s = plan["anchor_start_sample"] / FS; dur_s = plan["sample_count"] / FS
side = "left" if D == "LEFT" else "right"
tg = json.load(open(a.targets))[D]
target = np.array([tg["joints"][n] if n in tg["joints"] else np.nan for n in ARM_JOINTS])
parm = np.array([n.startswith(side) for n in ARM_JOINTS])

if a.base == "READY":
    base_cmd = READY[None]; base_wh = np.zeros((1, 4)); base_dt = 0.0
else:
    z = np.load(a.base); assert list(z["names"]) == list(ARM_JOINTS)
    base_cmd = z["cmd"][a.start_frame:]; base_wh = np.asarray(z["waist_head"])[a.start_frame:, :4]
    # SONIC waist/head yaw: waist [yaw,pitch,roll,head yaw] like c07
def base_at(ta):
    """base arms / waist-head at audio time ta (s): clip frame start+ta*50 (linear), static before 0 and after the end."""
    if len(base_cmd) == 1: return base_cmd[0], base_wh[0]
    f = np.clip(ta / TICK, 0, len(base_cmd) - 1); i = int(f); j = min(i + 1, len(base_cmd) - 1); r = f - i
    return base_cmd[i] * (1 - r) + base_cmd[j] * r, base_wh[i] * (1 - r) + base_wh[j] * r

def quintic(s):
    s = np.clip(s, 0.0, 1.0); return s ** 3 * (10 - 15 * s + 6 * s * s)
ts = anchor_start_s - a.lead_s; t_prep0 = ts - a.prep_s; t_hold1 = ts + hold_s; t_rec1 = t_hold1 + a.recov_s
def w_phase(ta):
    if a.no_gesture or ta < t_prep0: return 0.0, "BASE"
    if ta < ts: return float(quintic((ta - t_prep0) / a.prep_s)), "PREP"
    if ta <= t_hold1: return 1.0, "HOLD"
    if ta < t_rec1: return float(1 - quintic((ta - t_hold1) / a.recov_s)), "RECOVER"
    return 0.0, "BASE"

link = RobotLink("point4aa", publish_upper_body=True)
pose = {"z": None, "tilt": None, "xy": None}
def on_odom(m):
    p, q = m.pose.pose.position, m.pose.pose.orientation
    pose.update(z=p.z, xy=(p.x, p.y), tilt=math.degrees(2 * math.asin(min(1.0, math.hypot(q.x, q.y)))))
link.node.create_subscription(Odometry, "/aima/hal/odom/state", on_odom, qos_profile_sensor_data)
import rclpy
from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy
from aimdk_msgs import msg as AM
FIELDS = ("twist_waist", "bend_forward", "bend_sideways", "turn_head")
SPEED = np.array([0.81, 0.45, 0.92, 1.30]); LAG = np.array([15, 3, 8, 10]); DEADBAND = SPEED * 0.02
LOOKAHEAD = int(LAG.max())
inflight = [deque([0.0] * int(L), maxlen=int(L)) for L in LAG]
wh_state = {}
link.node.create_subscription(AM.JointStateArray, "/aima/hal/joint/waist/state", lambda m: wh_state.update(waist=[j.position for j in m.joints]), qos_profile_sensor_data)
link.node.create_subscription(AM.JointStateArray, "/aima/hal/joint/head/state", lambda m: wh_state.update(head=[j.position for j in m.joints]), qos_profile_sensor_data)
ctx77 = rclpy.Context(); rclpy.init(context=ctx77, domain_id=77)
node77 = rclpy.create_node("x2_point_waist", context=ctx77)
bp_pub = node77.create_publisher(AM.McBodyPose, "/aima/mc/body_pose", QoSProfile(depth=1, reliability=ReliabilityPolicy.BEST_EFFORT, durability=DurabilityPolicy.VOLATILE))
def wh_now():
    w, h = wh_state.get("waist"), wh_state.get("head")
    return None if w is None or h is None else np.array([w[0], w[1], w[2], h[0]])
def send_body_pose(u):
    m = AM.McBodyPose(); m.source = link.source
    for f, v in zip(FIELDS, u): setattr(m, f, float(v))
    bp_pub.publish(m)
def body_pose_step(k, plan_ahead):
    q = wh_now()
    if q is None:
        send_body_pose(np.zeros(4)); return None, np.zeros(4)
    u = np.zeros(4)
    for f in range(4):
        q_est = q[f] + sum(inflight[f]); e = plan_ahead[k + int(LAG[f]), f] - q_est
        u[f] = np.sign(e) * 0.3 if abs(e) > DEADBAND[f] else 0.0
        inflight[f].append(np.sign(u[f]) * SPEED[f] * TICK)
    send_body_pose(u); return q, u

_t = time.monotonic() + 6.0
while time.monotonic() < _t and (link.observed(graph=False).mc_age_s is None or link.latest("arms") is None or link.latest("head") is None or wh_now() is None):
    time.sleep(0.02)
rows, qpos_log, shared = [], [], None
if a.qpos_out:
    from x2_ultra.twin.model import build_twin_model
    from x2_ultra.twin.shared import TWIN_SHM_NAME, SharedState
    shared = SharedState(build_twin_model("vacia").nq, name=TWIN_SHM_NAME)
meta = {"direction": D, "base": a.base, "hold_s": hold_s, "anchor_start_s": anchor_start_s, "stroke_s": ts, "prep_start_s": t_prep0,
        "hold_end_s": t_hold1, "recovery_end_s": t_rec1, "audio_dur_s": dur_s, "vlim": a.vlim, "fs": FS, "no_gesture": a.no_gesture}
try:
    assert link.wait_stable(10.0), "MC not STABLE/RUNNING"
    if not a.zero:
        link.request_mode(URS); assert link.wait_mode(URS, 8.0), "URS not reached"
    t_end = time.monotonic() + 2.0
    while link.node.count_subscribers("/mc/upper_body_command") == 0 and time.monotonic() < t_end: time.sleep(0.05)
    link.register_source(65)
    arms0, head0 = link.latest("arms"), link.latest("head")
    anchor = np.array([arms0[n] for n in ARM_JOINTS]); head = [head0["head_yaw_joint"], head0["head_pitch_joint"]]
    hands = link.hand_positions()
    hand_mode, hand_pos = (2, hands) if hands and len(hands) == 20 else (1, [0.0, 0.0])
    b0, wh0b = base_at(0.0)
    ramp_n = int(max(a.ramp_s, np.abs(b0 - anchor).max() / a.ramp_rate) / TICK)
    settle_n = int(0.6 / TICK)
    rw = 0.5 - 0.5 * np.cos(np.linspace(0, np.pi, ramp_n))[:, None]
    wh0 = wh_now() if wh_now() is not None else np.zeros(4)
    if a.zero:
        n = int(max(a.ramp_s, 3.0) / TICK)
        K = n; plan_arms = np.repeat(anchor[None], n, 0); wh_plan = np.zeros((n, 4)); ph = ["zero"] * n; wv = np.zeros(n); tas = np.full(n, np.nan)
    else:
        PHASE_S = random.uniform(0, TICK)                 # sub-tick phase between the 50 Hz grid and the audio clock (c10)
        n_audio = int((max(dur_s, t_rec1) + 1.0) / TICK) + 1
        K = ramp_n + settle_n + n_audio
        T0_rel = (ramp_n + settle_n) * TICK + PHASE_S     # audio t0, relative to the first tick time
        plan_arms = np.zeros((K, 14)); wh_plan = np.zeros((K, 4)); ph = []; wv = np.zeros(K); tas = np.zeros(K)
        prev = None; lim_hits = 0
        for k in range(K):
            ta = k * TICK - T0_rel; tas[k] = ta
            if k < ramp_n:
                plan_arms[k] = anchor + rw[k, 0] * (b0 - anchor); wh_plan[k] = wh0 + rw[k, 0] * (wh0b - wh0); ph.append("ramp"); prev = plan_arms[k]; continue
            b, bw = base_at(ta); w, p = w_phase(ta); wv[k] = w
            c = b.copy(); c[parm] = b[parm] + w * (target[parm] - b[parm])
            c = np.clip(c, LO, HI)
            if p != "BASE" or (prev is not None and w > 0):
                step = np.clip(c - prev, -a.vlim * TICK, a.vlim * TICK); c2 = prev + step
                c2[~parm] = c[~parm]; c = c2
            elif p == "BASE" and prev is not None and np.abs(c[parm] - prev[parm]).max() > a.vlim * TICK + 1e-9 and ta < t_rec1 + 3.0 and ta > t_prep0:
                # return to base after recovery: keep limiter until the pointing arm has rejoined the base
                step = np.clip(c - prev, -a.vlim * TICK, a.vlim * TICK); c2 = prev + step; c2[~parm] = c[~parm]; c = c2
            plan_arms[k] = c; prev = c
            wp = bw.copy(); wp[3] += HEAD_SIGN[D] * a.head_amp * w; wp[0] += HEAD_SIGN[D] * a.waist_amp * w
            wh_plan[k] = wp; ph.append(p if ta >= 0 else "settle")
    wh_ahead = np.vstack([wh_plan, np.repeat(wh_plan[-1:], LOOKAHEAD + 1, 0)])
    t0 = time.monotonic() + 0.2
    meta.update(phase_s=(None if a.zero else PHASE_S), T0=(None if a.zero else t0 + T0_rel), ramp_ticks=ramp_n, settle_ticks=settle_n, K=K)
    for k in range(K):
        nxt = t0 + k * TICK
        time.sleep(max(0.0, nxt - time.monotonic() - 0.002))
        while time.monotonic() < nxt: pass
        t_pub = time.monotonic()
        obs = link.observed(graph=False)
        if (obs.mode != (SD if a.zero else URS)) or obs.mc_age_s is None or obs.mc_age_s > 0.5:
            raise RuntimeError(f"abort at tick {k}: mode={obs.mode} mc_age={obs.mc_age_s}")
        if not a.zero: link.publish_upper_body(head, plan_arms[k].tolist(), hand_mode, hand_pos, "cero")
        t_after = time.monotonic()
        q_wh, u_wh = body_pose_step(k, wh_ahead)
        if shared is not None:
            snap = shared.read(); qpos_log.append(snap[0].copy() if snap is not None else qpos_log[-1])
        meas = link.latest("arms") or {}
        rows.append({"tick": k, "t_sched": round(nxt, 5), "t_pub": round(t_pub, 5), "t_pub_done": round(t_after, 5),
                     "audio_s": round(float(tas[k]), 5), "audio_sample": (None if a.zero else int(round(tas[k] * FS))),
                     "w": float(wv[k]), "phase": ph[k], "mode": obs.mode, "fsm": obs.fsm, "pelvis_z": pose["z"], "tilt_deg": pose["tilt"],
                     **{f"cmd_{n}": float(v) for n, v in zip(ARM_JOINTS, plan_arms[k])},
                     **{f"meas_{n}": meas.get(n) for n in ARM_JOINTS},
                     **{f"wref_{f}": float(v) for f, v in zip(FIELDS, wh_plan[k])},
                     **{f"wmeas_{f}": (None if q_wh is None else float(v)) for f, v in zip(FIELDS, q_wh if q_wh is not None else [0] * 4)},
                     **{f"u_{f}": float(v) for f, v in zip(FIELDS, u_wh)}})
finally:
    for _ in range(25):
        send_body_pose(np.zeros(4)); time.sleep(TICK)
    if link.upper_pub is not None and rows and not a.zero:
        last = [rows[-1][f"cmd_{n}"] for n in ARM_JOINTS]
        for _ in range(10):
            link.publish_upper_body(head, last, hand_mode, hand_pos, "cero"); time.sleep(TICK)
    err = link.delete_source()
    try:
        link.request_mode(SD, busy_retry_s=12.0)
    except Exception as exc:  # noqa: BLE001
        print("STAND_DEFAULT request failed:", exc)
    if rows:
        with open(a.out, "w", newline="") as f:
            wr = csv.DictWriter(f, fieldnames=list(rows[0])); wr.writeheader(); wr.writerows(rows)
        json.dump(meta, open(a.out.replace(".csv", ".json"), "w"))
    if shared is not None and qpos_log:
        np.savez(a.qpos_out, qpos=np.array(qpos_log), t_pub=np.array([r["t_pub"] for r in rows]), audio_s=np.array([r["audio_s"] for r in rows]), phase=np.array([r["phase"] for r in rows]), w=np.array([r["w"] for r in rows]))
    print(f"ticks={len(rows)} delete_source_error={err} final_mode={link.observed(graph=False).mode}")
    node77.destroy_node(); rclpy.shutdown(context=ctx77)
    link.close()
