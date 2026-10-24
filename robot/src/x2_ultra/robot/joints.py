"""Articulaciones del X2 Ultra-N: nombres, orden SDK, límites, ganancias y postura por defecto.

Fuentes:
- URDF/MJCF oficiales ``X2_URDF-v1.4.0`` (límites de posición y de par).
- AimDK v1.1.0: orden de los arrays por topic HAL (pierna, cintura, brazo, cabeza).
- ``mc-rl/x2_config/joint_gains.yaml`` (AgiBot, Apache-2.0): ganancias PD de marcha RL.
"""
from __future__ import annotations

_LEG = ("hip_pitch", "hip_roll", "hip_yaw", "knee", "ankle_pitch", "ankle_roll")
_ARM = ("shoulder_pitch", "shoulder_roll", "shoulder_yaw", "elbow", "wrist_yaw", "wrist_pitch", "wrist_roll")

# Orden de los arrays JointStateArray/JointCommandArray de cada topic HAL de AimDK.
LEG_JOINTS = tuple(f"{s}_{j}_joint" for s in ("left", "right") for j in _LEG)          # /aima/hal/joint/leg/*
WAIST_JOINTS = ("waist_yaw_joint", "waist_pitch_joint", "waist_roll_joint")           # /aima/hal/joint/waist/*
ARM_JOINTS = tuple(f"{s}_{j}_joint" for s in ("left", "right") for j in _ARM)          # /aima/hal/joint/arm/*
HEAD_JOINTS = ("head_yaw_joint", "head_pitch_joint")                                  # /aima/hal/joint/head/*
LEFT_ARM_JOINTS = ARM_JOINTS[:7]
RIGHT_ARM_JOINTS = ARM_JOINTS[7:]

# 31 articulaciones del cuerpo (sin manos), en el orden SDK de los grupos.
BODY_JOINTS = LEG_JOINTS + WAIST_JOINTS + ARM_JOINTS + HEAD_JOINTS

# X2 EDU: el mismo cuerpo sin cuello (29 articulaciones).
EDU_BODY_JOINTS = LEG_JOINTS + WAIST_JOINTS + ARM_JOINTS

# Lo que controla nuestra policy de marcha: piernas + cintura (15). Brazos, cabeza y manos quedan libres.
POLICY_JOINTS = LEG_JOINTS + WAIST_JOINTS

# Límites de posición [rad] del URDF X2-Ultra_omnihand (v1.4.0).
JOINT_RANGE = {
    "left_hip_pitch_joint": (-2.7053, 2.5569), "right_hip_pitch_joint": (-2.7053, 2.5569),
    "left_hip_roll_joint": (-0.2356, 2.9060), "right_hip_roll_joint": (-2.9060, 0.2356),
    "left_hip_yaw_joint": (-1.8588, 3.6041), "right_hip_yaw_joint": (-3.6041, 1.8588),
    "left_knee_joint": (0.0, 2.4086), "right_knee_joint": (0.0, 2.4086),
    "left_ankle_pitch_joint": (-0.8029, 0.4538), "right_ankle_pitch_joint": (-0.8029, 0.4538),
    "left_ankle_roll_joint": (-0.2967, 0.2967), "right_ankle_roll_joint": (-0.2967, 0.2967),
    "waist_yaw_joint": (-3.4296, 2.1206), "waist_pitch_joint": (-0.3142, 0.3142),
    "waist_roll_joint": (-0.4887, 0.4887),
    "head_yaw_joint": (-0.4887, 0.4887), "head_pitch_joint": (-0.4014, 0.4887),
    "left_shoulder_pitch_joint": (-3.0805, 2.0333), "right_shoulder_pitch_joint": (-3.0805, 2.0333),
    "left_shoulder_roll_joint": (-0.0611, 3.0456), "right_shoulder_roll_joint": (-3.0456, 0.0611),
    "left_shoulder_yaw_joint": (-2.5569, 2.5569), "right_shoulder_yaw_joint": (-2.5569, 2.5569),
    "left_elbow_joint": (-2.3562, 0.0), "right_elbow_joint": (-2.3562, 0.0),
    "left_wrist_yaw_joint": (-2.5569, 2.5569), "right_wrist_yaw_joint": (-2.5569, 2.5569),
    "left_wrist_pitch_joint": (-0.5236, 0.5236), "right_wrist_pitch_joint": (-0.5236, 0.5236),
    "left_wrist_roll_joint": (-1.5097, 0.7243), "right_wrist_roll_joint": (-0.7243, 1.5097),
}

# Par máximo [N·m] del X2 Ultra-N (ctrlrange del MJCF oficial v1.4.0; motores mejorados respecto al v1.3.0).
EFFORT_LIMIT = {
    **{f"{s}_{j}_joint": 120.0 for s in ("left", "right") for j in ("hip_pitch", "hip_roll", "hip_yaw", "knee")},
    "left_ankle_pitch_joint": 60.0, "right_ankle_pitch_joint": 60.0,
    "left_ankle_roll_joint": 36.0, "right_ankle_roll_joint": 36.0,
    "waist_yaw_joint": 120.0, "waist_pitch_joint": 36.0, "waist_roll_joint": 36.0,
    "head_yaw_joint": 3.13125, "head_pitch_joint": 2.0,
    **{f"{s}_{j}_joint": 60.0 for s in ("left", "right") for j in ("shoulder_pitch", "shoulder_roll")},
    **{f"{s}_{j}_joint": 36.0 for s in ("left", "right") for j in ("shoulder_yaw", "elbow", "wrist_yaw")},
    **{f"{s}_{j}_joint": 6.0 for s in ("left", "right") for j in ("wrist_pitch", "wrist_roll")},
}

# Ganancias PD (rl_gains de AgiBot). La cabeza no está en la marcha RL de AgiBot (kp=0);
# aquí se controla en posición con las ganancias de "standup" (20/1).
KP = {
    **{f"{s}_hip_pitch_joint": 120.0 for s in ("left", "right")},
    **{f"{s}_{j}_joint": 100.0 for s in ("left", "right") for j in ("hip_roll", "hip_yaw")},
    **{f"{s}_knee_joint": 150.0 for s in ("left", "right")},
    **{f"{s}_{j}_joint": 40.0 for s in ("left", "right") for j in ("ankle_pitch", "ankle_roll")},
    "waist_yaw_joint": 40.18, "waist_pitch_joint": 200.0, "waist_roll_joint": 200.0,
    **{f"{s}_{j}_joint": 50.0 for s in ("left", "right") for j in ("shoulder_pitch", "shoulder_roll", "shoulder_yaw", "elbow")},
    **{f"{s}_{j}_joint": 20.0 for s in ("left", "right") for j in ("wrist_yaw", "wrist_pitch", "wrist_roll")},
    "head_yaw_joint": 20.0, "head_pitch_joint": 20.0,
}
KD = {
    **{f"{s}_hip_pitch_joint": 5.0 for s in ("left", "right")},
    **{f"{s}_{j}_joint": 4.0 for s in ("left", "right") for j in ("hip_roll", "hip_yaw")},
    **{f"{s}_knee_joint": 5.0 for s in ("left", "right")},
    **{f"{s}_{j}_joint": 2.0 for s in ("left", "right") for j in ("ankle_pitch", "ankle_roll")},
    "waist_yaw_joint": 2.56, "waist_pitch_joint": 2.0, "waist_roll_joint": 2.0,
    **{f"{s}_{j}_joint": 3.0 for s in ("left", "right") for j in ("shoulder_pitch", "shoulder_roll", "shoulder_yaw", "elbow")},
    **{f"{s}_{j}_joint": 2.0 for s in ("left", "right") for j in ("wrist_yaw", "wrist_pitch", "wrist_roll")},
    "head_yaw_joint": 1.0, "head_pitch_joint": 1.0,
}

# Postura de referencia de la marcha (semicuclillas de AgiBot; brazos ligeramente abiertos y codos flexionados).
DEFAULT_POSE = {name: 0.0 for name in BODY_JOINTS}
DEFAULT_POSE.update({
    "left_hip_pitch_joint": -0.312, "right_hip_pitch_joint": -0.312,
    "left_knee_joint": 0.669, "right_knee_joint": 0.669,
    "left_ankle_pitch_joint": -0.363, "right_ankle_pitch_joint": -0.363,
    "left_shoulder_roll_joint": 0.2, "right_shoulder_roll_joint": -0.2,
    "left_elbow_joint": -0.3, "right_elbow_joint": -0.3,
})

# Altura nominal de la pelvis de pie en la postura por defecto [m].
NOMINAL_PELVIS_HEIGHT = 0.68


def mirror_name(name: str) -> str:
    if name.startswith("left_"):
        return "right_" + name[5:]
    if name.startswith("right_"):
        return "left_" + name[6:]
    return name
