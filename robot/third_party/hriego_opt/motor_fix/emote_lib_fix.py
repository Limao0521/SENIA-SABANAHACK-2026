"""c12 shared rig (runs inside x2-twin): RobotLink (URS arms 50 Hz) + McBodyPose waist/head (domain 77, Smith-predictor sign control as c07/4aa)
+ SetMcPresetMotion helper subprocess (c08/c10 mc_preset_helper.py, mc overlay) + 50 Hz row logger. One sequential tick loop owns everything."""
import csv, json, math, queue, subprocess, threading, time
from collections import deque
import numpy as np
from nav_msgs.msg import Odometry
from rclpy.qos import qos_profile_sensor_data, QoSProfile, ReliabilityPolicy, DurabilityPolicy
import rclpy
from x2_ultra.real.link import RobotLink
from x2_ultra.robot.joints import ARM_JOINTS
from aimdk_msgs import msg as AM

URS, SD, TICK = "UPPERBODY_REMOTE_SPLIT", "STAND_DEFAULT", 0.02
READY = np.array([0.2, 0.2, 0.0, -0.6, 0.0, 0.0, 0.0, 0.2, -0.2, 0.0, -0.6, 0.0, 0.0, 0.0])   # SONIC default arms == preset CSV first frame / planner default
FIELDS = ("twist_waist", "bend_forward", "bend_sideways", "turn_head")
SPEED = np.array([0.81, 0.45, 0.92, 1.30]); LAG = np.array([15, 3, 8, 10]); DEADBAND = SPEED * 0.02; LOOKAHEAD = int(LAG.max())
LIMS = {}
_R = {"shoulder_pitch": (-3.0805, 2.0333), "shoulder_yaw": (-2.5569, 2.5569), "elbow": (-2.3562, 0.0), "wrist_yaw": (-2.5569, 2.5569), "wrist_pitch": (-0.5236, 0.5236)}
for s in ("left", "right"):
    for p in ("shoulder_pitch", "shoulder_roll", "shoulder_yaw", "elbow", "wrist_yaw", "wrist_pitch", "wrist_roll"):
        if p == "shoulder_roll": lo, hi = (-0.0611, 3.0456) if s == "left" else (-3.0456, 0.0611)
        elif p == "wrist_roll": lo, hi = (-1.5097, 0.7243) if s == "left" else (-0.7243, 1.5097)
        else: lo, hi = _R[p]
        LIMS[f"{s}_{p}_joint"] = (lo + 0.05, hi - 0.05)
LO = np.array([LIMS[n][0] for n in ARM_JOINTS]); HI = np.array([LIMS[n][1] for n in ARM_JOINTS])
HELPER_ENV = ("source /opt/ros/humble/setup.bash; source /workspace/aimdk/install/local_setup.bash; source /tmp/aimdk_mc_overlay/install/local_setup.bash; "
              "export RMW_IMPLEMENTATION=rmw_fastrtps_cpp; exec python3 -u mc_preset_helper.py")

def minjerk(s):
    s = np.clip(s, 0.0, 1.0); return s ** 3 * (10 - 15 * s + 6 * s * s)

class Rig:
    def __init__(self, name):
        self.link = RobotLink(name, publish_upper_body=True)
        self.pose = {"z": None, "tilt": None}
        def on_odom(m):
            p, q = m.pose.pose.position, m.pose.pose.orientation
            self.pose.update(z=p.z, tilt=math.degrees(2 * math.asin(min(1.0, math.hypot(q.x, q.y)))))
        self.link.node.create_subscription(Odometry, "/aima/hal/odom/state", on_odom, qos_profile_sensor_data)
        self.wh_state = {}
        self.link.node.create_subscription(AM.JointStateArray, "/aima/hal/joint/waist/state", lambda m: self.wh_state.update(waist=[j.position for j in m.joints]), qos_profile_sensor_data)
        self.link.node.create_subscription(AM.JointStateArray, "/aima/hal/joint/head/state", lambda m: self.wh_state.update(head=[j.position for j in m.joints]), qos_profile_sensor_data)
        # motor_fix (fix 6): watch the locomotion velocity COMMAND topic; HRI turns refuse to start while anything commands a non-zero velocity
        self.loc_state = {}
        try:
            self.link.node.create_subscription(AM.McLocomotionVelocity, "/aima/mc/locomotion/velocity",
                lambda m: self.loc_state.update(v=(float(m.forward_velocity), float(m.lateral_velocity), float(m.angular_velocity)), t=time.monotonic()), qos_profile_sensor_data)
        except Exception:
            pass
        self.ctx77 = rclpy.Context(); rclpy.init(context=self.ctx77, domain_id=77)
        self.node77 = rclpy.create_node("c12_waist", context=self.ctx77)
        self.bp_pub = self.node77.create_publisher(AM.McBodyPose, "/aima/mc/body_pose", QoSProfile(depth=1, reliability=ReliabilityPolicy.BEST_EFFORT, durability=DurabilityPolicy.VOLATILE))
        self.inflight = [deque([0.0] * int(L), maxlen=int(L)) for L in LAG]
        self.helper = subprocess.Popen(["bash", "-c", HELPER_ENV], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        self.replies = queue.Queue(); threading.Thread(target=lambda: [self.replies.put(json.loads(l)) for l in self.helper.stdout], daemon=True).start()
        assert self.replies.get(timeout=30)["ready"], "helper: services not found on domain 77"
        self.rows = []; self.events = []; self.head = None; self.hand_mode = 1; self.hand_pos = [0.0, 0.0]; self.last_cmd = None
        t = time.monotonic() + 8
        while time.monotonic() < t and (self.link.latest("arms") is None or self.link.latest("head") is None or self.wh_now() is None or self.link.observed(graph=False).mc_age_s is None or self.pose["z"] is None):
            time.sleep(0.02)
    # ---- helpers
    def wh_now(self):
        w, h = self.wh_state.get("waist"), self.wh_state.get("head")
        return None if w is None or h is None else np.array([w[0], w[1], w[2], h[0]])
    def arms_now(self):
        a = self.link.latest("arms"); return None if a is None else np.array([a[n] for n in ARM_JOINTS])
    def ev(self, name, **kw): self.events.append({"t": time.monotonic(), "event": name, **kw})
    # ---- fix 6: locomotion lockout helpers
    def loc_cmd(self, max_age_s=0.5):
        """Last COMMANDED locomotion velocity (vx, vy, wz) if fresh; None if no command in the last max_age_s (treated as zero)."""
        v, t = self.loc_state.get("v"), self.loc_state.get("t")
        return None if v is None or t is None or time.monotonic() - t > max_age_s else v
    def loc_zero(self, max_age_s=0.5):
        v = self.loc_cmd(max_age_s)
        return v is None or max(abs(v[0]), abs(v[1]), abs(v[2])) <= 1e-6
    def send_body_pose(self, u):
        m = AM.McBodyPose(); m.source = self.link.source
        for f, v in zip(FIELDS, u): setattr(m, f, float(v))
        self.bp_pub.publish(m)
    def body_pose_step(self, k, plan_ahead):
        """plan_ahead[i] = waist/head reference (4) at tick i; k = current index (LOOKAHEAD rows of padding required)."""
        q = self.wh_now()
        if q is None: self.send_body_pose(np.zeros(4)); return None, np.zeros(4)
        u = np.zeros(4)
        for f in range(4):
            q_est = q[f] + sum(self.inflight[f]); e = plan_ahead[k + int(LAG[f]), f] - q_est
            u[f] = np.sign(e) * 0.3 if abs(e) > DEADBAND[f] else 0.0
            self.inflight[f].append(np.sign(u[f]) * SPEED[f] * TICK)
        self.send_body_pose(u); return q, u
    def body_pose_ref(self, ref_fn, audio_s):
        """Like body_pose_step but ref_fn(audio_time)->(4,) is evaluated LAG[f] ticks ahead per field (reference tied to the audio clock)."""
        q = self.wh_now()
        if q is None: self.send_body_pose(np.zeros(4)); return None, np.zeros(4), np.zeros(4)
        u = np.zeros(4); r0 = ref_fn(audio_s)
        for f in range(4):
            q_est = q[f] + sum(self.inflight[f]); e = ref_fn(audio_s + LAG[f] * TICK)[f] - q_est
            u[f] = np.sign(e) * 0.3 if abs(e) > DEADBAND[f] else 0.0
            self.inflight[f].append(np.sign(u[f]) * SPEED[f] * TICK)
        self.send_body_pose(u); return q, u, r0
    def reset_inflight(self):
        for d in self.inflight: d.extend([0.0] * d.maxlen)
    def preset_set(self, motion, area):
        self.helper.stdin.write(f"set {motion} {area} 0\n"); self.helper.stdin.flush(); self.t_set = time.monotonic(); return self.t_set
    def preset_get(self, task):
        self.helper.stdin.write(f"get {task}\n"); self.helper.stdin.flush()
    def poll_replies(self):
        out = []
        while True:
            try: out.append(self.replies.get_nowait())
            except queue.Empty: return out
    def hand_init(self):
        h = self.link.hand_positions(); self.hand_mode, self.hand_pos = (2, h) if h and len(h) == 20 else (1, [0.0, 0.0])
        hd = self.link.latest("head"); self.head = [hd["head_yaw_joint"], hd["head_pitch_joint"]]
    def publish_arms(self, cmd):
        self.link.publish_upper_body(self.head, np.clip(cmd, LO, HI).tolist(), self.hand_mode, self.hand_pos, "cero"); self.last_cmd = np.clip(cmd, LO, HI)
    # ---- logging (one row per tick)
    def log(self, phase, audio_s=None, cmd=None, sonic=None, wref=None, u=None, q_wh=None, extra=None):
        obs = self.link.observed(graph=False); meas = self.link.latest("arms") or {}
        r = {"t": round(time.monotonic(), 5), "audio_s": None if audio_s is None else round(float(audio_s), 5), "phase": phase, "mode": obs.mode, "fsm": obs.fsm, "action_status": obs.action_status,
             "input_source": obs.input_source, "player_state": obs.player_state, "motion": obs.motion, "mc_age": obs.mc_age_s, "pelvis_z": self.pose["z"], "tilt_deg": self.pose["tilt"]}
        for i, n in enumerate(ARM_JOINTS):
            r[f"cmd_{n}"] = None if cmd is None else float(cmd[i]); r[f"meas_{n}"] = meas.get(n); r[f"sonic_{n}"] = None if sonic is None else float(sonic[i])
        w = self.wh_now(); hp = self.wh_state.get("head")
        r["head_pitch"] = None if hp is None else hp[1]
        for i, f in enumerate(FIELDS):
            r[f"wmeas_{f}"] = None if w is None else float(w[i]); r[f"wref_{f}"] = None if wref is None else float(wref[i]); r[f"u_{f}"] = None if u is None else float(u[i])
        if extra: r.update(extra)
        self.rows.append(r); return obs
    def dump(self, out):
        if self.rows:
            keys = list(dict.fromkeys(k for r in self.rows for k in r))
            with open(out, "w", newline="") as f:
                wr = csv.DictWriter(f, fieldnames=keys); wr.writeheader(); wr.writerows(self.rows)
    def close(self):
        try: self.helper.stdin.write("quit\n"); self.helper.stdin.flush(); self.helper.wait(5)
        except Exception: self.helper.kill()
        self.node77.destroy_node(); rclpy.shutdown(context=self.ctx77); self.link.close()
