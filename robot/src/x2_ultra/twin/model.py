"""Modelo MuJoCo del gemelo digital: el mismo Ultra-N + OmniHand del simulador, preparado como cuerpo HAL.

Cambios respecto a ``model.builder`` (que es el modelo del simulador con policy propia):
  - paso de 1 ms y **motores de par** en los 31 ejes del cuerpo: el par lo calcula ``TwinPhysics`` con el PD
    que llega en cada ``JointCommand`` (posición, velocidad, par, rigidez, amortiguamiento), como hace
    ``sim_mujoco`` del simulador oficial y el driver de cada motor del robot;
  - sin la compensación de gravedad de brazos y cabeza (en el robot real ese par lo pone quien manda: el MC o
    nuestro URS emulado); los dedos la conservan porque sus servos son de posición;
  - IMU de pelvis ("torso" en AimDK) y de pecho en las posiciones que publica el MC en ``/tf_static``;
  - cámaras (RGB-D, estéreo izquierda/derecha, trasera) y LiDAR montados en los marcos de ``/tf_static`` del MC,
    para que imágenes y árbol TF del robot coincidan;
  - un "pórtico" (weld de la pelvis al mundo) para arrancar colgado, como el robot real y el simulador oficial.
"""
from __future__ import annotations

import math

import mujoco
import numpy as np

from x2_ultra.model.builder import BuildOptions, build_scene_spec, default_qpos
from x2_ultra.robot import joints as J
from x2_ultra.robot import omnihand as H
from x2_ultra.twin import contract as C

TIMESTEP = 0.001
GANTRY = "gantry"


def _quat_mul(a, b) -> np.ndarray:
    """Producto de cuaterniones (w, x, y, z)."""
    w1, x1, y1, z1 = a
    w2, x2, y2, z2 = b
    return np.array([w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2, w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
                     w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2, w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2])


def _xyzw_to_wxyz(q) -> np.ndarray:
    return np.array([q[3], q[0], q[1], q[2]], dtype=float)


def _quat_to_mat(q) -> np.ndarray:
    m = np.zeros(9)
    mujoco.mju_quat2Mat(m, np.asarray(q, dtype=float))
    return m.reshape(3, 3)


def optical_to_mujoco_camera(q_opt_xyzw, roll_deg: float = 0.0) -> np.ndarray:
    """Cuaternión (wxyz) de una cámara MuJoCo a partir de un marco óptico ROS (z adelante, x derecha, y abajo).

    La cámara MuJoCo mira hacia -z con y arriba: es el marco óptico girado 180° en x. ``roll_deg`` gira la
    imagen alrededor del eje óptico (sensor montado girado)."""
    q = _xyzw_to_wxyz(q_opt_xyzw)
    if roll_deg:
        h = math.radians(roll_deg) / 2
        q = _quat_mul(q, (math.cos(h), 0.0, 0.0, math.sin(h)))
    return _quat_mul(q, (0.0, 1.0, 0.0, 0.0))


def camera_mount(cam_key: str) -> tuple[np.ndarray, np.ndarray, float]:
    """(pos, quat MuJoCo wxyz, fovy) de cada cámara en head_pitch_link."""
    spec = C.CAMERAS[cam_key]
    mount = "stereo_head_front" if cam_key.startswith("stereo") else spec.frame_id
    pos, q_opt = C.CAMERA_MOUNTS[mount]
    pos = np.array(pos, dtype=float)
    if cam_key == "stereo_right":     # la derecha está a +baseline sobre el eje x óptico de la izquierda
        pos = pos + _quat_to_mat(_xyzw_to_wxyz(q_opt)) @ np.array([C.STEREO_BASELINE, 0.0, 0.0])
    return pos, optical_to_mujoco_camera(q_opt, spec.roll_deg), spec.fovy_deg


def _finger_bodies(spec: mujoco.MjSpec) -> set[str]:
    hand_joints = set(H.HAND_JOINTS) | set(H.MIMIC)
    return {b.name for b in spec.bodies if any(j.name in hand_joints for j in b.joints)}


def build_twin_spec(scene: str = "mesa", timestep: float = TIMESTEP) -> mujoco.MjSpec:
    spec = build_scene_spec(BuildOptions(variant="full", scene=scene, timestep=timestep))
    spec.modelname = "x2_ultra_n_omnihand_twin"

    # Motores de par con el límite del Ultra-N (el PD lo pone TwinPhysics).
    for name in J.BODY_JOINTS:
        a = spec.actuator(name)
        a.set_to_motor()
        # set_to_motor no borra el sesgo -kp·q - kv·q̇ del actuador de posición que había: se quita a mano.
        a.gaintype = mujoco.mjtGain.mjGAIN_FIXED
        a.gainprm = [1.0] + [0.0] * 9
        a.biastype = mujoco.mjtBias.mjBIAS_NONE
        a.biasprm = [0.0] * 10
        a.ctrlrange = [-J.EFFORT_LIMIT[name], J.EFFORT_LIMIT[name]]
        a.ctrllimited = mujoco.mjtLimited.mjLIMITED_TRUE
        a.forcerange = [-J.EFFORT_LIMIT[name], J.EFFORT_LIMIT[name]]
        a.forcelimited = mujoco.mjtLimited.mjLIMITED_TRUE

    # Sin compensación de gravedad en brazos y cabeza; los dedos la conservan.
    fingers = _finger_bodies(spec)
    for b in spec.bodies:
        if b.gravcomp and b.name not in fingers:
            b.gravcomp = 0.0
            for j in b.joints:
                j.actgravcomp = False

    # IMU (marcos de cuerpo sin rotación, como sim_mujoco) y sus sensores.
    for name, (body, pos) in C.IMU_MOUNTS.items():
        spec.body(body).add_site(name=f"imu_{name}", pos=list(pos), size=[0.008, 0, 0], group=5)
        site = f"imu_{name}"
        for kind, stype in (("quat", mujoco.mjtSensor.mjSENS_FRAMEQUAT), ("gyro", mujoco.mjtSensor.mjSENS_GYRO),
                            ("acc", mujoco.mjtSensor.mjSENS_ACCELEROMETER),
                            ("vel", mujoco.mjtSensor.mjSENS_VELOCIMETER), ("pos", mujoco.mjtSensor.mjSENS_FRAMEPOS)):
            kwargs = {"objtype": mujoco.mjtObj.mjOBJ_SITE, "objname": site}
            spec.add_sensor(name=f"imu_{name}_{kind}", type=stype, **kwargs)

    # Cámaras en head_pitch_link.
    head = spec.body("head_pitch_link")
    for key in C.CAMERAS:
        pos, quat, fovy = camera_mount(key)
        head.add_camera(name=f"cam_{key}", pos=list(pos), quat=list(quat), fovy=fovy)
    # IMU de la RGB-D (marco óptico del giróscopo ~ marco óptico de color).
    pos, q_opt = C.CAMERA_MOUNTS["rgbd_head_front"]
    head.add_site(name="imu_rgbd", pos=list(pos), quat=list(_xyzw_to_wxyz(q_opt)), size=[0.005, 0, 0], group=5)

    # LiDAR (marco propio del sensor) y su IMU en el mismo punto.
    body, pos, q = C.LIDAR_MOUNT
    for site in ("lidar", "imu_lidar"):
        spec.body(body).add_site(name=site, pos=list(pos), quat=list(_xyzw_to_wxyz(q)), size=[0.01, 0, 0], group=5)
    for site in ("imu_rgbd", "imu_lidar"):
        spec.add_sensor(name=f"{site}_gyro", type=mujoco.mjtSensor.mjSENS_GYRO, objtype=mujoco.mjtObj.mjOBJ_SITE,
                        objname=site)
        spec.add_sensor(name=f"{site}_acc", type=mujoco.mjtSensor.mjSENS_ACCELEROMETER,
                        objtype=mujoco.mjtObj.mjOBJ_SITE, objname=site)
        spec.add_sensor(name=f"{site}_quat", type=mujoco.mjtSensor.mjSENS_FRAMEQUAT,
                        objtype=mujoco.mjtObj.mjOBJ_SITE, objname=site)

    # Pórtico: weld pelvis-mundo; TwinPhysics fija la pose sujeta al activarlo.
    spec.add_equality(type=mujoco.mjtEq.mjEQ_WELD, name=GANTRY, objtype=mujoco.mjtObj.mjOBJ_BODY,
                      name1="pelvis", name2="world", solref=[0.05, 1.0], active=True)

    # Render fuera de pantalla hasta la resolución real de las cámaras (2048×1536).
    spec.visual.global_.offwidth = 2048
    spec.visual.global_.offheight = 1536

    # Keyframe: de pie en la postura por defecto, motores a 0 y dedos abiertos.
    for k in list(spec.keys):
        spec.delete(k)
    m = spec.compile()
    spec.add_key(name="home", qpos=default_qpos(spec), ctrl=np.zeros(m.nu))
    return spec


def build_twin_model(scene: str = "mesa", timestep: float = TIMESTEP) -> mujoco.MjModel:
    return build_twin_spec(scene, timestep).compile()
