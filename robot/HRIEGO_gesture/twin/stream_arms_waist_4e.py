"""Stream SONIC arms (UPPERBODY_REMOTE_SPLIT) AND SONIC waist + head yaw (McBodyPose) into the X2 twin.

Waist/head go through AgiBot's own body-pose input (/aima/mc/body_pose). The probes (bodypose/) showed each
field is a DIRECTION command: any nonzero value moves the joint at a fixed MC speed (magnitude ignored), with
0.06-0.45 s of lag; 0 holds. So each field is driven by pulse-width sign control with a Smith predictor:
q_est = q_meas + displacement commanded in the last L ticks;  s = sign(q_ref(t + L) - q_est) outside a
one-tick deadband. STAND_DEFAULT keeps balancing (cpgtelecon) while only_waist/only_head move the joints.
McBodyPose is published to the MC on its private DDS domain 77 (the twin does not bridge it), under the same
registered source as the arms. Needs the McBodyPose overlay sourced. --no-arms: waist only (e.g. --zero).

Runs inside the x2-twin container (PYTHONPATH=/workspace/x2_ultra/src). Handoff copied from
x2_ultra/real/arms.py: wait STABLE -> SetMcAction URS -> wait mode -> SetMcInputSource ADD (prio 65)
-> anchor -> cosine ramp-in -> 50 Hz stream (stamp 0) -> hold -> 10 hold msgs -> DELETE -> STAND_DEFAULT.
"""
import argparse, csv, json, math, time
import numpy as np
from nav_msgs.msg import Odometry
from rclpy.qos import qos_profile_sensor_data
from x2_ultra.real.link import RobotLink
from x2_ultra.robot.joints import ARM_JOINTS

URS, SD, TICK = "UPPERBODY_REMOTE_SPLIT", "STAND_DEFAULT", 0.02

ap = argparse.ArgumentParser()
ap.add_argument("traj"); ap.add_argument("out")
ap.add_argument("--ramp-s", type=float, default=2.0)
ap.add_argument("--hold-s", type=float, default=2.0)
ap.add_argument("--ramp-rate", type=float, default=0.5, help="max mean joint speed of the ramp-in [rad/s]")
ap.add_argument("--no-arms", action="store_true")
ap.add_argument("--start-frame", type=int, default=0, help="first SONIC frame to stream (100 = source clip start = audio t0)")
ap.add_argument("--zero", action="store_true", help="only drive waist/head yaw back to 0 (no clip)")
ap.add_argument("--no-waist", action="store_true")
ap.add_argument("--qpos-out", default=None, help="record the twin qpos (SharedState) each tick to this .npz")
a = ap.parse_args()

z = np.load(a.traj)
assert list(z["names"]) == list(ARM_JOINTS), "arm order mismatch"
cmd = z["cmd"][a.start_frame:]

link = RobotLink("sonic_arms", publish_upper_body=True)
pose = {"z": None, "tilt": None, "xy": None}
def on_odom(m):
    p, q = m.pose.pose.position, m.pose.pose.orientation
    pose.update(z=p.z, xy=(p.x, p.y), tilt=math.degrees(2 * math.asin(min(1.0, math.hypot(q.x, q.y)))))
link.node.create_subscription(Odometry, "/aima/hal/odom/state", on_odom, qos_profile_sensor_data)
import rclpy
from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy
from aimdk_msgs import msg as AM
FIELDS = ("twist_waist", "bend_forward", "bend_sideways", "turn_head")      # waist yaw, pitch, roll, head yaw
SPEED = np.array([0.81, 0.45, 0.92, 1.30])          # rad/s per field when nonzero (bodypose/hold_*, min_*)
LAG = np.array([15, 3, 8, 10])                      # ticks of MC lag per field (bodypose/min_*, pulse_*)
DEADBAND = SPEED * 0.02                             # one tick of motion
LOOKAHEAD = int(LAG.max())
from collections import deque
inflight = [deque([0.0] * int(L), maxlen=int(L)) for L in LAG]
wh_state = {}
link.node.create_subscription(AM.JointStateArray, "/aima/hal/joint/waist/state",
                              lambda m: wh_state.update(waist=[j.position for j in m.joints]), qos_profile_sensor_data)
link.node.create_subscription(AM.JointStateArray, "/aima/hal/joint/head/state",
                              lambda m: wh_state.update(head=[j.position for j in m.joints]), qos_profile_sensor_data)
ctx77 = rclpy.Context(); rclpy.init(context=ctx77, domain_id=77)
node77 = rclpy.create_node("x2_sonic_waist", context=ctx77)
bp_pub = node77.create_publisher(AM.McBodyPose, "/aima/mc/body_pose",
                                 QoSProfile(depth=1, reliability=ReliabilityPolicy.BEST_EFFORT, durability=DurabilityPolicy.VOLATILE))


def wh_now():
    w, h = wh_state.get("waist"), wh_state.get("head")
    return None if w is None or h is None else np.array([w[0], w[1], w[2], h[0]])


def send_body_pose(u):
    m = AM.McBodyPose(); m.source = link.source
    for f, v in zip(FIELDS, u):
        setattr(m, f, float(v))
    bp_pub.publish(m)


def body_pose_step(k, plan_ahead):
    q = wh_now()
    if q is None:
        send_body_pose(np.zeros(4)); return None, np.zeros(4)
    u = np.zeros(4)
    for f in range(4):
        q_est = q[f] + sum(inflight[f])
        e = plan_ahead[k + int(LAG[f]), f] - q_est
        u[f] = np.sign(e) * 0.3 if abs(e) > DEADBAND[f] else 0.0
        inflight[f].append(np.sign(u[f]) * SPEED[f] * TICK)
    send_body_pose(u); return q, u
# Wait only for what the stream needs. RobotLink.wait_ready() also waits for the battery (PMU), which the twin
# does not publish, so it always timed out after 6 s.
_t = time.monotonic() + 6.0
while time.monotonic() < _t and (link.observed(graph=False).mc_age_s is None or link.latest("arms") is None
                                 or link.latest("head") is None or wh_now() is None):
    time.sleep(0.02)
rows, phase_log = [], []
qpos_log, shared = [], None
if a.qpos_out:
    from x2_ultra.twin.model import build_twin_model
    from x2_ultra.twin.shared import TWIN_SHM_NAME, SharedState
    shared = SharedState(build_twin_model("vacia").nq, name=TWIN_SHM_NAME)
try:
    assert link.wait_stable(10.0), "MC not STABLE/RUNNING"
    if not a.no_arms:
        link.request_mode(URS)
        assert link.wait_mode(URS, 8.0), "URS not reached"
    t_end = time.monotonic() + 2.0
    while link.node.count_subscribers("/mc/upper_body_command") == 0 and time.monotonic() < t_end:
        time.sleep(0.05)
    link.register_source(65)
    wh_ref_clip = np.asarray(z["waist_head"])[a.start_frame:, :4]          # SONIC waist yaw/pitch/roll, head yaw
    if a.zero or a.no_waist:
        wh_ref_clip = np.zeros_like(wh_ref_clip)
    arms0, head0 = link.latest("arms"), link.latest("head")
    anchor = np.array([arms0[n] for n in ARM_JOINTS]); head = [head0["head_yaw_joint"], head0["head_pitch_joint"]]
    hands = link.hand_positions()
    hand_mode, hand_pos = (2, hands) if hands and len(hands) == 20 else (1, [0.0, 0.0])
    ramp_n = int(max(a.ramp_s, np.abs(cmd[0] - anchor).max() / a.ramp_rate) / TICK)
    w = 0.5 - 0.5 * np.cos(np.linspace(0, np.pi, ramp_n))[:, None]
    ramp = anchor + w * (cmd[0] - anchor)
    phases = ["ramp"] * ramp_n + ["sonic"] * len(cmd) + ["hold"] * int(a.hold_s / TICK)
    plan = np.vstack([ramp, cmd, np.repeat(cmd[-1:], int(a.hold_s / TICK), 0)])
    wh0 = wh_now() if wh_now() is not None else np.zeros(4)
    if a.zero:
        phases = ["zero"] * int(max(a.ramp_s, 3.0) / TICK)
        plan = np.repeat(anchor[None], len(phases), 0)
        wh_plan = np.zeros((len(phases), 4))
    else:
        wh_plan = np.vstack([wh0 + w * (wh_ref_clip[0] - wh0), wh_ref_clip,
                             np.repeat(wh_ref_clip[-1:], int(a.hold_s / TICK), 0)])
    wh_ahead = np.vstack([wh_plan, np.repeat(wh_plan[-1:], LOOKAHEAD + 1, 0)])
    t0 = time.monotonic(); T0_ABS = t0   # 4e: absolute monotonic start, saved to <out>.json
    for k, (target, ph) in enumerate(zip(plan, phases)):
        obs = link.observed(graph=False)
        if (obs.mode != (SD if a.no_arms else URS)) or obs.mc_age_s is None or obs.mc_age_s > 0.5:
            raise RuntimeError(f"abort at tick {k}: mode={obs.mode} mc_age={obs.mc_age_s}")
        if not a.no_arms:
            link.publish_upper_body(head, target.tolist(), hand_mode, hand_pos, "cero")
        if not a.no_waist:
            q_wh, u_wh = body_pose_step(k, wh_ahead)
        else:
            q_wh, u_wh = wh_now(), np.zeros(4)
        if shared is not None:
            snap = shared.read()
            qpos_log.append(snap[0].copy() if snap is not None else qpos_log[-1])
        meas = link.latest("arms") or {}
        rows.append({"tick": k, "t": round(time.monotonic() - t0, 4), "phase": ph, "mode": obs.mode,
                     "fsm": obs.fsm, "pelvis_z": pose["z"], "tilt_deg": pose["tilt"],
                     "x": pose["xy"][0] if pose["xy"] else None, "y": pose["xy"][1] if pose["xy"] else None,
                     **{f"cmd_{n}": float(v) for n, v in zip(ARM_JOINTS, target)},
                     **{f"meas_{n}": meas.get(n) for n in ARM_JOINTS},
                     **{f"wref_{f}": float(v) for f, v in zip(FIELDS, wh_plan[k])},
                     **{f"wmeas_{f}": (None if q_wh is None else float(v)) for f, v in zip(FIELDS, q_wh if q_wh is not None else [0] * 4)},
                     **{f"u_{f}": float(v) for f, v in zip(FIELDS, u_wh)}})
        nxt = t0 + (k + 1) * TICK
        time.sleep(max(0.0, nxt - time.monotonic()))
finally:
    for _ in range(25):
        send_body_pose(np.zeros(4)); time.sleep(TICK)
    if link.upper_pub is not None and rows and not a.no_arms:
        last = [rows[-1][f"cmd_{n}"] for n in ARM_JOINTS]
        for _ in range(10):
            link.publish_upper_body(head, last, hand_mode, hand_pos, "cero"); time.sleep(TICK)
    err = link.delete_source()
    try:
        if not a.no_arms:
            link.request_mode(SD, busy_retry_s=12.0)
    except Exception as exc:  # noqa: BLE001
        print("STAND_DEFAULT request failed:", exc)
    if rows:
        with open(a.out, "w", newline="") as f:
            wr = csv.DictWriter(f, fieldnames=list(rows[0])); wr.writeheader(); wr.writerows(rows)
    if shared is not None and qpos_log:
        np.savez(a.qpos_out, qpos=np.array(qpos_log), phase=np.array(phases[:len(qpos_log)]), ramp_ticks=ramp_n, tick_s=TICK, start_frame=a.start_frame)
    try:
        json.dump({'t0_abs': T0_ABS, 'ramp_ticks': ramp_n, 'start_frame': a.start_frame}, open(a.out.replace('.csv', '.json'), 'w'))
    except NameError: pass
    print(f"ticks={len(rows)} delete_source_error={err} final_mode={link.observed(graph=False).mode}")
    node77.destroy_node(); rclpy.shutdown(context=ctx77)
    link.close()
