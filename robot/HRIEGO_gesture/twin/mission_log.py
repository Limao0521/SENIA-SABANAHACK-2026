"""4e: background logger inside x2-twin. 50 Hz: monotonic t, odometry, MC mode/fsm, twin qpos. Stops on file /tmp/v4e/STOP_<name>.
usage: mission_log.py NAME   -> /tmp/v4e/<NAME>_log.csv, <NAME>_qpos.npz"""
import csv, math, os, sys, time
import numpy as np
from nav_msgs.msg import Odometry
from rclpy.qos import qos_profile_sensor_data
from x2_ultra.real.link import RobotLink
from x2_ultra.twin.model import build_twin_model
from x2_ultra.twin.shared import TWIN_SHM_NAME, SharedState
N = sys.argv[1]; stop = f"/tmp/v4e/STOP_{N}"
if os.path.exists(stop): os.remove(stop)
link = RobotLink("m4e_log"); od = {}
def on_odom(m):
    p, q, tw = m.pose.pose.position, m.pose.pose.orientation, m.twist.twist
    od.update(x=p.x, y=p.y, z=p.z, yaw=math.atan2(2 * (q.w * q.z + q.x * q.y), 1 - 2 * (q.y * q.y + q.z * q.z)),
              tilt=math.degrees(2 * math.asin(min(1.0, math.hypot(q.x, q.y)))), vx=tw.linear.x, vy=tw.linear.y, wz=tw.angular.z)
link.node.create_subscription(Odometry, "/aima/hal/odom/state", on_odom, qos_profile_sensor_data)
shared = SharedState(build_twin_model("vacia").nq, name=TWIN_SHM_NAME)
rows, ql = [], []
nxt = time.monotonic()
while not os.path.exists(stop):
    obs = link.observed(graph=False); snap = shared.read()
    if snap is not None: ql.append(snap[0].copy())
    elif ql: ql.append(ql[-1])
    else: nxt += 0.02; time.sleep(0.02); continue
    rows.append({"t": round(time.monotonic(), 4), "mode": obs.mode, "fsm": obs.fsm, **{k: od.get(k) for k in ("x", "y", "z", "yaw", "tilt", "vx", "vy", "wz")}})
    nxt += 0.02; time.sleep(max(0.0, nxt - time.monotonic()))
with open(f"/tmp/v4e/{N}_log.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
np.savez(f"/tmp/v4e/{N}_qpos.npz", qpos=np.array(ql), t=np.array([r["t"] for r in rows]))
print("rows", len(rows)); link.close()
