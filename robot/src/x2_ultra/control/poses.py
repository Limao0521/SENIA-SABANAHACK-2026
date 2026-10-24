"""Poses predefinidas de brazos (y de manos asociadas) y pequeñas animaciones.

Convenciones del X2 (URDF v1.4.0): ``shoulder_pitch`` negativo adelanta/levanta el brazo;
``shoulder_roll`` abre el brazo hacia fuera con signo + en el izquierdo y − en el derecho;
``elbow`` negativo flexiona el codo.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from x2_ultra.robot import joints as J


def _arms(left: dict, right: dict | None = None, mirror: bool = True) -> dict:
    """Construye una pose de 14 ejes; si ``mirror``, el brazo derecho es el reflejo del izquierdo."""
    pose = {n: J.DEFAULT_POSE[n] for n in J.ARM_JOINTS}
    for k, v in left.items():
        pose[f"left_{k}_joint"] = v
    if right is None and mirror:
        sign = {"shoulder_pitch": 1, "shoulder_roll": -1, "shoulder_yaw": -1, "elbow": 1,
                "wrist_yaw": -1, "wrist_pitch": 1, "wrist_roll": -1}
        right = {k: sign[k] * v for k, v in left.items()}
    for k, v in (right or {}).items():
        pose[f"right_{k}_joint"] = v
    return pose


@dataclass
class ArmPose:
    label: str
    key: str
    arms: dict
    hands: dict = field(default_factory=dict)      # {"L": pose, "R": pose} de omnihand.HAND_POSES
    wave: dict = field(default_factory=dict)       # articulación -> (amplitud rad, frecuencia Hz)

    def target(self, t: float) -> np.ndarray:
        q = np.array([self.arms[n] for n in J.ARM_JOINTS])
        for name, (amp, freq) in self.wave.items():
            q[J.ARM_JOINTS.index(name)] += amp * math.sin(2 * math.pi * freq * t)
        return q


ARM_POSES = {
    "reposo": ArmPose("Reposo", "0", _arms({}), {"L": "abierta", "R": "abierta"}),
    "saludar": ArmPose("Saludar", "1", _arms({}, {"shoulder_pitch": -0.2, "shoulder_roll": -1.3,
                                                  "shoulder_yaw": -1.5, "elbow": -1.4}, mirror=False),
                       {"R": "abierta"}, wave={"right_shoulder_yaw_joint": (0.3, 1.2)}),
    "al_frente": ArmPose("Brazos al frente", "2", _arms({"shoulder_pitch": -1.45, "shoulder_roll": 0.12,
                                                        "elbow": -0.15})),
    "arriba": ArmPose("Brazos arriba", "3", _arms({"shoulder_pitch": -2.85, "shoulder_roll": 0.15, "elbow": -0.1})),
    "cruz": ArmPose("Cruz (T)", "4", _arms({"shoulder_pitch": 0.0, "shoulder_roll": 1.5, "elbow": 0.0})),
    "cargar": ArmPose("Cargar caja", "5", _arms({"shoulder_pitch": -0.85, "shoulder_roll": 0.32,
                                                 "shoulder_yaw": -0.25, "elbow": -1.25, "wrist_roll": -0.4}),
                      {"L": "agarre", "R": "agarre"}),
    "señalar": ArmPose("Señalar", "6", _arms({}, {"shoulder_pitch": -1.45, "shoulder_roll": -0.15, "elbow": -0.05},
                                             mirror=False), {"R": "señalar"}),
    "dar_mano": ArmPose("Dar la mano", "7", _arms({}, {"shoulder_pitch": -0.75, "shoulder_roll": -0.1,
                                                      "elbow": -0.85, "wrist_roll": -0.7}, mirror=False),
                        {"R": "agarre"}),
    # jarras y aplaudir: resueltas con ArmIK.solve_global a partir de objetivos de la mano en el marco del torso
    "jarras": ArmPose("Manos a la cintura", "8", _arms(
        {"shoulder_pitch": 0.864, "shoulder_roll": 0.004, "shoulder_yaw": -0.01, "elbow": -1.187,
         "wrist_yaw": 0.106, "wrist_pitch": -0.208, "wrist_roll": -0.194},
        {"shoulder_pitch": 0.853, "shoulder_roll": -0.111, "shoulder_yaw": 0.17, "elbow": -1.185,
         "wrist_yaw": -0.243, "wrist_pitch": -0.228, "wrist_roll": 0.087})),
    "aplaudir": ArmPose("Aplaudir", "9", _arms(
        {"shoulder_pitch": -0.012, "shoulder_roll": -0.031, "shoulder_yaw": -0.685, "elbow": -1.868,
         "wrist_yaw": -0.079, "wrist_pitch": -0.058, "wrist_roll": 0.694}),
        {"L": "abierta", "R": "abierta"},
        wave={"left_shoulder_yaw_joint": (0.18, 1.5), "right_shoulder_yaw_joint": (-0.18, 1.5)}),
}

# Gestos de la demo HRI; no ocupan teclas del panel de poses manuales.
HRI_POSES = {
    "señalar_izquierda": ArmPose("Señalar a la izquierda", "", _arms(
        {"shoulder_pitch": -1.3, "shoulder_roll": 0.65, "elbow": -0.1}, {}, mirror=False),
        {"L": "señalar"}),
    "señalar_derecha": ArmPose("Señalar a la derecha", "", _arms(
        {}, {"shoulder_pitch": -1.3, "shoulder_roll": -0.65, "elbow": -0.1}, mirror=False),
        {"R": "señalar"}),
    "corazon": ArmPose("Corazón", "", _arms(
        {"shoulder_pitch": -2.3, "shoulder_roll": 0.45, "elbow": -1.25}),
        {"L": "abierta", "R": "abierta"}),
    "chocar_cinco": ArmPose("Chocar los cinco", "", _arms(
        {}, {"shoulder_pitch": -1.75, "shoulder_roll": -0.2, "elbow": -0.3}, mirror=False),
        {"R": "abierta"}),
}

POSE_BY_KEY = {p.key: name for name, p in ARM_POSES.items()}


def clamp_arms(q: np.ndarray) -> np.ndarray:
    lo = np.array([J.JOINT_RANGE[n][0] for n in J.ARM_JOINTS])
    hi = np.array([J.JOINT_RANGE[n][1] for n in J.ARM_JOINTS])
    return np.clip(q, lo, hi)
