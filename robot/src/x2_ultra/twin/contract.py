"""Contrato de la interfaz del X2 Ultra-N real (AimDK v1.1.0) que imita el gemelo digital. Sin ROS.

Fuentes, por orden de prioridad:
  1. Documentación de AimDK v1.1.0 (``aimdk/docs``: ``Interface/hal/sensor``, ``joint_control``, ``interactor/voice``,
     ``control_mod/upper_body_control``) y la lista ``aimdk/topics_and_services`` del robot real.
  2. Lo que hace el simulador oficial con el MC v1.0.0, grabado con ``official_sim/capture_reference.sh``
     (QoS, ``frame_id``, convenios de IMU y odometría). Se usa donde la documentación no dice nada.

Las QoS se escriben como ``(fiabilidad, durabilidad, profundidad)``; ``ros_hal`` las convierte a ``QoSProfile``.
"""
from __future__ import annotations

from dataclasses import dataclass

from x2_ultra.robot import joints as J
from x2_ultra.robot import omnihand as H

BEST_EFFORT, RELIABLE = "best_effort", "reliable"
VOLATILE, TRANSIENT_LOCAL = "volatile", "transient_local"

# --- Articulaciones (HAL) ------------------------------------------------------------------------
# Orden de JointCommand[]/JointState[] por grupo (joint_control.html): izquierda primero.
JOINT_GROUPS: dict[str, tuple[str, ...]] = {
    "leg": J.LEG_JOINTS,
    "waist": J.WAIST_JOINTS,
    "arm": J.ARM_JOINTS,
    "head": J.HEAD_JOINTS,
}
# Robot real: 500 Hz brazos/piernas/cintura y 333 Hz cabeza (el simulador oficial publica todo a 1 kHz).
JOINT_STATE_RATE = {"leg": 500.0, "waist": 500.0, "arm": 500.0, "head": 333.0}
JOINT_STATE_QOS = (BEST_EFFORT, TRANSIENT_LOCAL, 1)
JOINT_COMMAND_QOS = (BEST_EFFORT, VOLATILE, 10)


def joint_state_topic(group: str) -> str:
    return f"/aima/hal/joint/{group}/state"


def joint_command_topic(group: str) -> str:
    return f"/aima/hal/joint/{group}/command"


# --- Manos OmniHand -------------------------------------------------------------------------------
HAND_STATE_TOPIC = "/aima/hal/joint/hand/state"
HAND_COMMAND_TOPIC = "/aima/hal/joint/hand/command"
HAND_STATE_RATE = 200.0
HAND_STATE_QOS = (BEST_EFFORT, TRANSIENT_LOCAL, 1)
HAND_COMMAND_QOS = (BEST_EFFORT, VOLATILE, 10)
HAND_TYPE_NIMBLE = 1                       # HandType.NIMBLE_HANDS: OmniHand en modo diestro
LEFT_HAND_JOINTS, RIGHT_HAND_JOINTS = H.LEFT_HAND_JOINTS, H.RIGHT_HAND_JOINTS

# --- IMU de cuerpo y odometría -------------------------------------------------------------------
IMU_TOPICS = {"torso": "/aima/hal/imu/torso/state", "chest": "/aima/hal/imu/chest/state"}
IMU_RATE = 500.0
IMU_QOS = (BEST_EFFORT, TRANSIENT_LOCAL, 1)
IMU_FRAME_ID = "base_link"                 # en el robot real los dos usan base_link (sensor.html)
# Posición de cada IMU en su cuerpo, sin rotación: lo que publica el MC en /tf_static (imu_in_pelvis_link e
# imu_in_torso_link) y lo que usa sim_mujoco. La IMU "torso" de AimDK está en la pelvis.
IMU_MOUNTS = {"torso": ("pelvis", (0.0239465, -0.000228682, 0.04171)),
              "chest": ("torso_link", (-0.0248787, 0.00198528, 0.105938))}

ODOM_TOPIC = "/aima/hal/odom/state"        # solo simulación (sim_mujoco); el robot real no lo tiene
ODOM_RATE = 500.0
ODOM_QOS = (BEST_EFFORT, TRANSIENT_LOCAL, 1)
ODOM_FRAME_ID, ODOM_CHILD_FRAME_ID = "odom", "base_link"

# --- Cámaras --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class CameraSpec:
    name: str                      # espacio de nombres del topic: /aima/hal/sensor/<name>/...
    frame_id: str
    width: int                     # resolución real (X2 Ultra-N, "new version")
    height: int
    fovy_deg: float                # campo de visión vertical de la cámara estenopeica que lo aproxima
    rate: float
    image_qos: tuple
    info_qos: tuple
    info_rate: float
    info_topic: str = "camera_info"
    depth: bool = False
    roll_deg: float = 0.0          # giro de la imagen respecto al marco óptico (cámara montada girada)


# Marcos ópticos (z adelante, x derecha, y abajo) respecto a head_pitch_link, tal como los publica el MC en /tf_static
# (lx2501_3_t2d5); coinciden con el URDF del Ultra-N salvo milímetros. (pos, quat xyzw)
CAMERA_MOUNTS = {
    "rgbd_head_front": ((0.0576, -0.0112, -0.0484), (0.6409, 0.6409, 0.2988, 0.2988)),
    "stereo_head_front": ((0.0680, 0.0298, 0.0500), (-0.4992, 0.5008, -0.5008, 0.4992)),
    "rgb_head_rear": ((-0.0834, 0.0003, 0.0000), (-0.5008, -0.4992, 0.4992, 0.5008)),
}
STEREO_BASELINE = 0.065            # [m] izquierda→derecha sobre el eje x óptico (estimada del URDF; sin dato oficial)

CAMERAS = {
    # Orbbec Gemini 335: color 1280×720 RGB8 (~86°×55°), profundidad 1280×720 16UC1 en mm (~90°×59°).
    "rgbd_color": CameraSpec("rgbd_head_front", "rgbd_head_front", 1280, 720, 55.0, 30.0,
                             (RELIABLE, VOLATILE, 1), (RELIABLE, VOLATILE, 1), 30.0, info_topic="rgb_camera_info"),
    "rgbd_depth": CameraSpec("rgbd_head_front", "rgbd_head_front", 1280, 720, 58.7, 30.0,
                             (RELIABLE, VOLATILE, 1), (RELIABLE, VOLATILE, 1), 30.0,
                             info_topic="depth_camera_info", depth=True),
    # Estéreo SenYun: 2048×1536 RGB8 a 10 Hz, objetivo de 156°×120° (aquí estenopeica de 120° en vertical).
    "stereo_left": CameraSpec("stereo_head_front_left", "stereo_head_front", 2048, 1536, 120.0, 10.0,
                              (RELIABLE, TRANSIENT_LOCAL, 1), (RELIABLE, TRANSIENT_LOCAL, 1), 1.0),
    "stereo_right": CameraSpec("stereo_head_front_right", "stereo_head_front_right", 2048, 1536, 120.0, 10.0,
                               (RELIABLE, TRANSIENT_LOCAL, 1), (RELIABLE, TRANSIENT_LOCAL, 1), 1.0),
    # Trasera: 2048×1536 RGB8 a 10 Hz; en el Ultra-N va montada girada 90° (sensor.html).
    "rear": CameraSpec("rgb_head_rear", "rgb_head_rear", 2048, 1536, 120.0, 10.0,
                       (RELIABLE, TRANSIENT_LOCAL, 1), (RELIABLE, TRANSIENT_LOCAL, 1), 1.0, roll_deg=90.0),
}
DEPTH_RANGE = (0.26, 6.0)          # [m] fuera de este rango la profundidad vale 0 (sin dato)
JPEG_QUALITY = 85
# Resoluciones del gemelo: "real" como el robot; "reducida" (por defecto) para ir en tiempo real con 5 cámaras.
CAMERA_SCALE = {"real": 1.0, "media": 0.5, "reducida": 0.25}
CAMERA_SCALE_RGBD = {"real": 1.0, "media": 1.0, "reducida": 0.5}


def camera_topic(spec: CameraSpec, kind: str) -> str:
    """kind: 'image' | 'compressed' | 'info'."""
    base = f"/aima/hal/sensor/{spec.name}"
    if kind == "info":
        return f"{base}/{spec.info_topic}"
    leaf = "depth_image" if spec.depth else "rgb_image"
    return f"{base}/{leaf}" + ("/compressed" if kind == "compressed" else "")


RGBD_IMU_TOPIC = "/aima/hal/sensor/rgbd_head_front/imu"
RGBD_IMU_FRAME = "camera_gyro_optical_frame"
RGBD_IMU_QOS = (RELIABLE, VOLATILE, 10)
SENSOR_IMU_RATE = 200.0

# --- LiDAR de pecho (RoboSense E1R) --------------------------------------------------------------
LIDAR_TOPIC = "/aima/hal/sensor/lidar_chest_front/lidar_pointcloud"
LIDAR_IMU_TOPIC = "/aima/hal/sensor/lidar_chest_front/imu"
LIDAR_FRAME = "lidar_chest_front"
LIDAR_IMU_FRAME = "lidar_imu_chest_front"
LIDAR_QOS = (RELIABLE, TRANSIENT_LOCAL, 1)
LIDAR_IMU_QOS = (RELIABLE, TRANSIENT_LOCAL, 10)
LIDAR_RATE = 10.0
LIDAR_MOUNT = ("torso_link", (0.1026, 0.0, 0.1816), (-0.7071068, 0.0, 0.0, 0.7071068))   # /tf_static del MC
LIDAR_FOV_DEG = (120.0, 90.0)      # horizontal × vertical en el marco propio del sensor (x adelante, z arriba)
LIDAR_RES_DEG = 0.625              # 192 × 144 haces por barrido
LIDAR_RANGE = (0.1, 30.0)          # zona ciega 0,1 m; ~30 m con reflectividad baja
LIDAR_RESOLUTION_M = 0.005

# --- Audio y voz ------------------------------------------------------------------------------------
AUDIO_PLAYBACK_TOPIC = "/aima/hal/audio/playback"
AUDIO_FOCUS_TOPIC = "/aima/hal/audio/focus_response"
AUDIO_PLAY_STATE_TOPIC = "/aima/hal/audio/play_state"
AUDIO_CAPTURE_TOPIC = "/aima/hal/audio/capture"
AUDIO_VAD_TOPIC = "/agent/process_audio_output"
AUDIO_PLAYBACK_QOS = (RELIABLE, VOLATILE, 50)
AUDIO_FOCUS_QOS = (RELIABLE, TRANSIENT_LOCAL, 10)
AUDIO_PLAY_STATE_QOS = (BEST_EFFORT, TRANSIENT_LOCAL, 10)
AUDIO_CAPTURE_QOS = (RELIABLE, VOLATILE, 10)
AUDIO_VAD_QOS = (BEST_EFFORT, TRANSIENT_LOCAL, 50)
AUDIO_RATE_HZ = 16000              # formato fijo del sistema: 16 kHz, S16LE, mono
AUDIO_CAPTURE_PERIOD = 0.03        # 33,3 Hz
AUDIO_CAPTURE_CHANNELS = (4, 2)    # micrófono integrado: 4 de micrófono + 2 de referencia (eco), entrelazados

# --- Tren superior (MC) -------------------------------------------------------------------------
UPPER_BODY_TOPIC = "/mc/upper_body_command"
UPPER_BODY_QOS = (BEST_EFFORT, VOLATILE, 10)
UPPER_BODY_MAX_AGE = 0.2           # [s] el MC descarta órdenes con header.stamp a más de 200 ms
HAND_CLAW, HAND_DEXTEROUS_JOINT, HAND_DEXTEROUS_GESTURE = 1, 2, 3

# --- Servicios ------------------------------------------------------------------------------------


def service(name: str) -> str:
    """Nombre ROS de un servicio de AimDK: /aimdk_5Fmsgs/srv/<Tipo> ('_' se escapa como _5F)."""
    return f"/aimdk_5Fmsgs/srv/{name}"


SIM_OPTION_TOPIC = "/aima/sim/option/command"   # std_msgs/String, como sim_mujoco ("gantry off", "reset"...)
