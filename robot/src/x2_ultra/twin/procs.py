"""Procesos auxiliares del gemelo que solo leen la postura (``SharedState``): visor y sensores.

Se lanzan con ``multiprocessing`` en modo "spawn". Este módulo no importa MuJoCo arriba del todo: cada proceso
elige antes su backend gráfico (GLFW para la ventana, EGL en la GPU para las cámaras).
"""
from __future__ import annotations

import os
import time


def viewer_main(shm_name: str, nq: int, scene: str, stop_event) -> None:
    os.environ.pop("MUJOCO_GL", None)              # ventana con GLFW
    import mujoco
    import mujoco.viewer

    from x2_ultra.twin.model import build_twin_model
    from x2_ultra.twin.shared import SharedState

    model = build_twin_model(scene)
    data = mujoco.MjData(model)
    state = SharedState(nq, name=shm_name)
    gantry = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_EQUALITY, "gantry")
    last_seq = -1
    with mujoco.viewer.launch_passive(model, data, show_left_ui=False) as viewer:
        while viewer.is_running() and not stop_event.is_set():
            snap = state.read()
            if snap is not None and snap[3] != last_seq:
                qpos, t, on, last_seq = snap
                data.qpos[:] = qpos
                data.time = t
                data.eq_active[gantry] = int(on)
                mujoco.mj_forward(model, data)
                viewer.sync()
            time.sleep(1 / 60)
    state.close()


def sensors_main(shm_name: str, nq: int, scene: str, quality: str, lidar: bool, stop_event) -> None:
    """Cámaras y LiDAR con los topics del robot real. Solo se dibuja lo que tiene suscriptores (en el robot real
    se publica siempre; para quien se suscribe el resultado es el mismo y la GPU no trabaja en balde)."""
    os.environ["MUJOCO_GL"] = "egl"                # render en la GPU sin ventana
    from array import array

    import cv2
    import mujoco
    import rclpy
    from rclpy.node import Node
    from sensor_msgs.msg import CameraInfo, CompressedImage, Image, PointCloud2, PointField

    from x2_ultra.twin import contract as C
    from x2_ultra.twin.model import build_twin_model
    from x2_ultra.twin.ros_hal import qos
    from x2_ultra.twin.sensors import POINT_FIELDS, CameraRenderer, LidarScanner, camera_setups
    from x2_ultra.twin.shared import SharedState

    model = build_twin_model(scene)
    data = mujoco.MjData(model)
    state = SharedState(nq, name=shm_name)
    rclpy.init()
    node = Node("x2_twin_sensors")
    setups = camera_setups(quality) if quality != "no" else []
    renderer = CameraRenderer(model, setups) if setups else None
    scanner = LidarScanner(model) if lidar else None

    def stamp():
        t = time.time()
        return int(t), int((t % 1.0) * 1e9)

    cams = []
    for s in setups:
        c = {"s": s, "img": node.create_publisher(Image, C.camera_topic(s.spec, "image"), qos(s.spec.image_qos)),
             "info": node.create_publisher(CameraInfo, C.camera_topic(s.spec, "info"), qos(s.spec.info_qos)),
             "jpg": None if s.spec.depth else node.create_publisher(
                 CompressedImage, C.camera_topic(s.spec, "compressed"), qos(s.spec.image_qos)),
             "next": 0.0, "next_info": 0.0}
        info = CameraInfo()
        info.header.frame_id = s.spec.frame_id
        info.width, info.height = s.width, s.height
        info.distortion_model = "plumb_bob"
        info.d = [0.0] * 5
        info.k, info.p = s.K, s.P
        info.r = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
        c["info_msg"] = info
        cams.append(c)
    lidar_pub = node.create_publisher(PointCloud2, C.LIDAR_TOPIC, qos(C.LIDAR_QOS)) if scanner else None
    fields = [PointField(name=n, offset=o, datatype=t, count=1) for n, o, t in POINT_FIELDS]
    next_lidar = 0.0
    node.get_logger().info(f"sensores: {len(setups)} cámaras ({quality}), lidar {'sí' if scanner else 'no'}")

    def update_pose() -> bool:
        snap = state.read()
        if snap is None:
            return False
        data.qpos[:] = snap[0]
        mujoco.mj_kinematics(model, data)
        mujoco.mj_camlight(model, data)
        return True

    while not stop_event.is_set():
        now = time.monotonic()
        posed = False
        for c in cams:
            s = c["s"]
            if now >= c["next_info"]:
                c["next_info"] = now + 1.0 / s.spec.info_rate
                sec, nsec = stamp()
                c["info_msg"].header.stamp.sec, c["info_msg"].header.stamp.nanosec = sec, nsec
                c["info"].publish(c["info_msg"])
            if now < c["next"]:
                continue
            c["next"] = now + 1.0 / s.spec.rate
            want_raw = c["img"].get_subscription_count() > 0
            want_jpg = c["jpg"] is not None and c["jpg"].get_subscription_count() > 0
            if not (want_raw or want_jpg):
                continue
            if not posed:
                posed = update_pose()
            frame = renderer.render(data, s)
            sec, nsec = stamp()
            if want_raw:
                m = Image()
                m.header.stamp.sec, m.header.stamp.nanosec, m.header.frame_id = sec, nsec, s.spec.frame_id
                m.height, m.width = s.height, s.width
                if s.spec.depth:
                    m.encoding, m.step = "16UC1", s.width * 2
                else:
                    m.encoding, m.step = "rgb8", s.width * 3
                m.data = array("B", frame.tobytes())   # con bytes, rclpy valida byte a byte (~40 ms/imagen)
                c["img"].publish(m)
            if want_jpg:
                ok, buf = cv2.imencode(".jpg", cv2.cvtColor(frame, cv2.COLOR_RGB2BGR),
                                       [cv2.IMWRITE_JPEG_QUALITY, C.JPEG_QUALITY])
                if ok:
                    m = CompressedImage()
                    m.header.stamp.sec, m.header.stamp.nanosec, m.header.frame_id = sec, nsec, s.spec.frame_id
                    m.format = "jpeg"
                    m.data = array("B", buf.tobytes())
                    c["jpg"].publish(m)
        if scanner and now >= next_lidar:
            next_lidar = now + 1.0 / C.LIDAR_RATE
            if lidar_pub.get_subscription_count() > 0:
                if not posed:
                    posed = update_pose()
                pts = scanner.scan(data)
                sec, nsec = stamp()
                m = PointCloud2()
                m.header.stamp.sec, m.header.stamp.nanosec, m.header.frame_id = sec, nsec, C.LIDAR_FRAME
                m.height, m.width = 1, len(pts)
                m.fields = fields
                m.is_bigendian, m.point_step, m.row_step = False, pts.dtype.itemsize, pts.dtype.itemsize * len(pts)
                m.is_dense = True
                m.data = array("B", pts.tobytes())
                lidar_pub.publish(m)
        rclpy.spin_once(node, timeout_sec=0.002)
    if renderer:
        renderer.close()
    node.destroy_node()
    rclpy.shutdown()
    state.close()
