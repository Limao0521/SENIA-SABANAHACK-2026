"""Direct SetMcPresetMotion client on the MC's private DDS domain (77), with an aimdk_msgs whose request has NO input_source.

Why: twin MC v1.0.0 reads SetMcPresetMotion as {header, area, motion, interrupt, ani_path, play_timestamp}; the public SDK
type (and so the twin's mc_shim forwarder) inserts input_source after the header, so the MC sees area=len(name)+1, motion=0.
Needs /tmp/aimdk_mc_overlay (colcon build of the overlay with input_source removed from SetMcPresetMotion). Own process
because two aimdk_msgs cannot be loaded together.  stdin: "set MOTION AREA INTERRUPT" | "get TASK" | "quit"; stdout: JSON.
"""
import json, sys, time
import rclpy
from aimdk_msgs.srv import SetMcPresetMotion, GetMcPresetMotionState
rclpy.init(domain_id=77)
n = rclpy.create_node("preset_helper")
cs = n.create_client(SetMcPresetMotion, "/aimdk_5Fmsgs/srv/SetMcPresetMotion")
cg = n.create_client(GetMcPresetMotionState, "/aimdk_5Fmsgs/srv/GetMcPresetMotionState")
ok = cs.wait_for_service(15.0) and cg.wait_for_service(15.0)
print(json.dumps({"ready": ok}), flush=True)
for line in sys.stdin:
    c = line.split()
    if not c or c[0] == "quit":
        break
    if c[0] == "set":
        r = SetMcPresetMotion.Request()
        r.header.stamp = n.get_clock().now().to_msg()
        r.motion.value, r.area.value, r.interrupt, r.play_timestamp = int(c[1]), int(c[2]), bool(int(c[3])), 0
        t0 = time.monotonic(); f = cs.call_async(r)
        rclpy.spin_until_future_complete(n, f, timeout_sec=5.0); t1 = time.monotonic()
        x = f.result().response if f.done() and f.exception() is None else None
        print(json.dumps({"op": "set", "t_call": t0, "t_resp": t1, "code": None if x is None else int(x.header.code),
                          "state": None if x is None else int(x.state.value), "task_id": None if x is None else int(x.task_id)}), flush=True)
    else:
        r = GetMcPresetMotionState.Request(); r.request.task_id = int(c[1])
        t0 = time.monotonic(); f = cg.call_async(r)
        rclpy.spin_until_future_complete(n, f, timeout_sec=2.0)
        x = f.result().response if f.done() and f.exception() is None else None
        print(json.dumps({"op": "get", "t_call": t0, "t_resp": time.monotonic(), "state": None if x is None else int(x.state.value)}), flush=True)
