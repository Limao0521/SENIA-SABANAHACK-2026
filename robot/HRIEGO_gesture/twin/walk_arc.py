"""4e: walk -> ARC (vx>0 together with wz) -> walk -> stop, through AgiBot's MC /aima/mc/locomotion/velocity, closed loop on odometry. Runs inside x2-twin.
Continuous command stream (no zero between phases). Events/trace with time.monotonic().
usage: walk_arc.py OUT_PREFIX [--d1 1.5 --turn-deg 90 --vx 0.4 --arc-vx 0.35 --wz 0.8 --d2 1.0 --turn-lead-deg 8 --d1-lead 0.1 --d2-lead 0.2 --sign 1]"""
import argparse, csv, json, math, time
from nav_msgs.msg import Odometry
from rclpy.qos import qos_profile_sensor_data
from x2_ultra.real.link import RobotLink
ap = argparse.ArgumentParser(); ap.add_argument("out")
ap.add_argument("--d1", type=float, default=1.5); ap.add_argument("--d2", type=float, default=1.0)
ap.add_argument("--turn-deg", type=float, default=90.0); ap.add_argument("--vx", type=float, default=0.4)
ap.add_argument("--arc-vx", type=float, default=0.35); ap.add_argument("--wz", type=float, default=0.8)
ap.add_argument("--turn-lead-deg", type=float, default=8.0); ap.add_argument("--d1-lead", type=float, default=0.0); ap.add_argument("--d2-lead", type=float, default=0.2)
ap.add_argument("--sign", type=float, default=1.0)
a = ap.parse_args(); TICK = 0.02
link = RobotLink("walk4e", publish_velocity=True); od = {}
def on_odom(m):
    p, q = m.pose.pose.position, m.pose.pose.orientation
    od.update(x=p.x, y=p.y, z=p.z, yaw=math.atan2(2 * (q.w * q.z + q.x * q.y), 1 - 2 * (q.y * q.y + q.z * q.z)))
link.node.create_subscription(Odometry, "/aima/hal/odom/state", on_odom, qos_profile_sensor_data)
wrap = lambda x: (x + math.pi) % (2 * math.pi) - math.pi
ev, tr = [], []; cur = {"phase": "init", "vx": 0.0, "wz": 0.0}
def event(_n, **kw):
    e = {"t": round(time.monotonic(), 4), "event": _n, **kw}; ev.append(e); print(json.dumps(e), flush=True)
def pub(vx, wz, phase):
    cur.update(phase=phase, vx=vx, wz=wz); link.publish_velocity(vx, 0.0, wz, "cero")
    tr.append({"t": round(time.monotonic(), 4), "phase": phase, "cmd_vx": vx, "cmd_wz": wz, **od}); time.sleep(TICK)
t_end = time.monotonic() + 3
while "x" not in od and time.monotonic() < t_end: time.sleep(0.02)
try:
    time.sleep(1.0); assert link.wait_stable(15.0), "MC not STABLE at start"
    link.register_source(40)
    x0, y0, yaw0 = od["x"], od["y"], od["yaw"]; event("start", x=od["x"], y=od["y"], yaw=od["yaw"])
    ph = "walk1"; event("phase", name=ph)
    while math.hypot(od["x"] - x0, od["y"] - y0) < a.d1 - a.d1_lead: pub(a.vx, 0.0, ph)
    xa, ya, yawa = od["x"], od["y"], od["yaw"]; ph = "arc"; event("phase", name=ph, x=xa, y=ya)
    tgt = math.radians(a.turn_deg) - math.radians(a.turn_lead_deg); n = 0
    while abs(wrap(od["yaw"] - yawa)) < tgt and n < 1500: pub(a.arc_vx, a.sign * a.wz, ph); n += 1
    xb, yb, yawb = od["x"], od["y"], od["yaw"]; ph = "walk2"; event("phase", name=ph, x=xb, y=yb)
    while math.hypot(od["x"] - xb, od["y"] - yb) < a.d2 - a.d2_lead and n < 3000: pub(a.vx, 0.0, ph); n += 1
    xc, yc = od["x"], od["y"]; ph = "stop"; event("phase", name=ph, x=xc, y=yc)
    for _ in range(10): pub(0.0, 0.0, ph)
    t_stop = time.monotonic(); ok = link.wait_stable(15.0); t_st = time.monotonic()
    event("stable", ok=ok, time_to_stable_s=round(t_st - t_stop + 0.2, 2))
    time.sleep(1.0)
    event("final", x=od["x"], y=od["y"], yaw=od["yaw"], d1=math.hypot(xa - x0, ya - y0), d_arc=math.hypot(xb - xa, yb - ya), d2=math.hypot(od["x"] - xb, od["y"] - yb),
          d2_to_stop=math.hypot(xc - xb, yc - yb), turn_deg_arc_end=math.degrees(wrap(yawb - yawa)), turn_deg_final=math.degrees(wrap(od["yaw"] - yawa)),
          turn_deg_total_final=math.degrees(wrap(od["yaw"] - yaw0)), straight_dev_walk1_deg=math.degrees(wrap(yawa - yaw0)))
except Exception as exc:  # noqa: BLE001
    event("failed", error=repr(exc))
finally:
    for _ in range(25): link.publish_velocity(0.0, 0.0, 0.0, "cero"); time.sleep(TICK)
    link.delete_source(); time.sleep(0.5)
    json.dump(ev, open(a.out + "_events.json", "w"), indent=1)
    if tr:
        with open(a.out + "_cmd.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(tr[0])); w.writeheader(); w.writerows(tr)
    link.close()
