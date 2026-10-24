"""El puente DDS debe entregar URS al MC real cuando se desactiva la emulación.

El MC de esta prueba es un nodo ROS falso en otro dominio; no usa el binario de AgiBot ni el robot.
"""
import os
import threading
import time
from types import SimpleNamespace

import pytest
import rclpy
from aimdk_msgs.msg import CommonState, McActionCommand, UpperBodyCommandArray
from aimdk_msgs.srv import GetMcAction, SetMcAction
from rclpy.executors import MultiThreadedExecutor, SingleThreadedExecutor
from rclpy.node import Node

from x2_ultra.twin import contract as C
from x2_ultra.twin.mc_shim import UpperBodyShim
from x2_ultra.twin.ros_hal import qos
from x2_ultra.twin.urs import HEAD_ONLY, URS


def _until(predicate, timeout=8.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


def _call(client, request, timeout=8.0):
    assert client.wait_for_service(timeout_sec=timeout)
    future = client.call_async(request)
    done = threading.Event()
    future.add_done_callback(lambda _: done.set())
    assert done.wait(timeout), "el servicio público no respondió"
    assert future.exception() is None
    return future.result()


@pytest.fixture(params=[False, True], ids=["native", "emulated"])
def bridge(request):
    # Dos dominios distintos y ajenos a los 0/77 del gemelo en ejecución.
    public_domain = 150 + os.getpid() % 30
    mc_context = rclpy.Context()
    rclpy.init(domain_id=public_domain)
    rclpy.init(context=mc_context, domain_id=public_domain + 40)
    mc_node = Node("fake_mc", context=mc_context)
    caller = Node("shim_test_client")
    hal = SimpleNamespace(lock=threading.Lock(), overrides=[])
    state = {"mode": "STAND_DEFAULT", "sets": [], "upper": [], "upper_event": threading.Event(), "reject": False}

    def set_action(req, response):
        state["sets"].append((req.command.action_desc, req.source))
        if state["reject"]:
            response.response.status.value = CommonState.FAILURE
            response.response.message = f"MC rejected {req.command.action_desc}"
            return response
        state["mode"] = req.command.action_desc
        response.response.status.value = CommonState.SUCCESS
        response.response.message = f"MC: {state['mode']}"
        return response

    def get_action(_req, response):
        response.info.action_desc = state["mode"]
        return response

    def on_upper(msg):
        state["upper"].append(msg)
        state["upper_event"].set()

    mc_node.create_service(SetMcAction, C.service("SetMcAction"), set_action)
    mc_node.create_service(GetMcAction, C.service("GetMcAction"), get_action)
    mc_node.create_subscription(UpperBodyCommandArray, C.UPPER_BODY_TOPIC, on_upper, qos(C.UPPER_BODY_QOS))
    shim = UpperBodyShim(hal, mc_node, emulate=request.param)
    set_client = caller.create_client(SetMcAction, C.service("SetMcAction"))
    get_client = caller.create_client(GetMcAction, C.service("GetMcAction"))
    upper_pub = caller.create_publisher(UpperBodyCommandArray, C.UPPER_BODY_TOPIC, qos(C.UPPER_BODY_QOS))
    public_exec = MultiThreadedExecutor(num_threads=2)
    private_exec = SingleThreadedExecutor(context=mc_context)
    for node in (shim, caller):
        public_exec.add_node(node)
    private_exec.add_node(mc_node)
    threads = [threading.Thread(target=executor.spin, daemon=True) for executor in (public_exec, private_exec)]
    for thread in threads:
        thread.start()
    try:
        yield SimpleNamespace(shim=shim, hal=hal, state=state, set_client=set_client,
                              get_client=get_client, upper_pub=upper_pub, emulate=request.param)
    finally:
        public_exec.shutdown(timeout_sec=3.0)
        private_exec.shutdown(timeout_sec=3.0)
        for thread in threads:
            thread.join(timeout=3.0)
        for node in (caller, shim, mc_node):
            node.destroy_node()
        rclpy.shutdown(context=mc_context)
        rclpy.shutdown()


def test_urs_service_and_upper_body_route(bridge):
    req = SetMcAction.Request()
    req.source = "sdk_test"
    req.command = McActionCommand(action_desc=URS)
    response = _call(bridge.set_client, req)
    assert response.response.status.value == CommonState.SUCCESS

    if bridge.emulate:
        assert "emulado" in response.response.message
        assert bridge.state["sets"] == []
        assert bridge.hal.overrides == [bridge.shim.emu]
    else:
        assert response.response.message == f"MC: {URS}"
        assert bridge.state["sets"] == [(URS, "sdk_test")]
        assert bridge.hal.overrides == []

    assert _call(bridge.get_client, GetMcAction.Request()).info.action_desc == URS

    upper = UpperBodyCommandArray()
    upper.source = "sdk_test"
    if not bridge.emulate:
        upper.header.stamp.sec = 1  # incluso un stamp viejo debe llegar al MC; él decide si lo descarta
    upper.arm_pos = [0.1] * 14
    upper.hand_pos = [0.2] * 20
    upper.hand_sub_mode = UpperBodyCommandArray.HAND_DEXTEROUS_JOINT
    assert _until(lambda: bridge.upper_pub.get_subscription_count() > 0)
    for _ in range(20):
        bridge.upper_pub.publish(upper)
        if bridge.state["upper_event"].wait(0.1) or bridge.shim.emu.accepted:
            break
    if bridge.emulate:
        assert bridge.shim.emu.accepted > 0
        assert bridge.state["upper"] == []
    else:
        assert bridge.state["upper_event"].is_set()
        assert bridge.state["upper"][0].source == upper.source
        assert bridge.state["upper"][0].header.stamp.sec == 1
        assert list(bridge.state["upper"][0].arm_pos) == list(upper.arm_pos)
        assert list(bridge.state["upper"][0].hand_pos) == list(upper.hand_pos)
        assert bridge.shim.emu.accepted == 0

        req.command.action_desc = HEAD_ONLY
        response = _call(bridge.set_client, req)
        assert response.response.message == f"MC: {HEAD_ONLY}"
        assert bridge.state["sets"] == [(URS, "sdk_test"), (HEAD_ONLY, "sdk_test")]
        assert _call(bridge.get_client, GetMcAction.Request()).info.action_desc == HEAD_ONLY

        bridge.state["reject"] = True
        req.command.action_desc = URS
        response = _call(bridge.set_client, req)
        assert response.response.status.value == CommonState.FAILURE
        assert response.response.message == f"MC rejected {URS}"
        assert _call(bridge.get_client, GetMcAction.Request()).info.action_desc == HEAD_ONLY
