"""OmniHand 2025 (AgiBot): 10 ejes activos por mano + 6 acoplados (mimic) en el URDF.

Orden de los ejes activos = orden de los arrays del SDK (AimDK v1.1.0):
``UpperBodyCommandArray.hand_pos`` con ``hand_sub_mode = 2`` (HAND_DEXTEROUS_JOINT) lleva
izquierda 10 + derecha 10, y ``HandCommand[]``/``HandState[]`` van por índice de eje activo
(AimDK pide fiarse del índice, no del campo ``name``).

Los valores normalizados ``u`` de este módulo van de 0 (mano abierta/neutra) a 1
(eje en el extremo de su rango más alejado de 0: dedo flexionado, pulgar oponiéndose,
dedos separados). Así las mismas poses valen para las dos manos aunque los signos se espejen.
"""
from __future__ import annotations

import numpy as np

HAND_SIDE_JOINTS = ("thumb_roll", "thumb_abad", "thumb_mcp", "index_abad", "index_pip",
                    "middle_pip", "ring_abad", "ring_pip", "pinky_abad", "pinky_pip")
LEFT_HAND_JOINTS = tuple(f"L_{j}_joint" for j in HAND_SIDE_JOINTS)
RIGHT_HAND_JOINTS = tuple(f"R_{j}_joint" for j in HAND_SIDE_JOINTS)
HAND_JOINTS = LEFT_HAND_JOINTS + RIGHT_HAND_JOINTS

# Etiquetas para la interfaz.
JOINT_LABELS = {
    "thumb_roll": "Pulgar · rotación", "thumb_abad": "Pulgar · abducción", "thumb_mcp": "Pulgar · flexión",
    "index_abad": "Índice · separación", "index_pip": "Índice · flexión",
    "middle_pip": "Corazón · flexión",
    "ring_abad": "Anular · separación", "ring_pip": "Anular · flexión",
    "pinky_abad": "Meñique · separación", "pinky_pip": "Meñique · flexión",
}

# Rango [rad] de los ejes activos (URDF X2-Ultra_omnihand v1.4.0).
HAND_RANGE = {
    "L_thumb_roll_joint": (-0.8727, 0.1745), "R_thumb_roll_joint": (-0.1745, 0.8727),
    "L_thumb_abad_joint": (0.0, 1.7453), "R_thumb_abad_joint": (-1.7453, 0.0),
    "L_thumb_mcp_joint": (-0.8552, 0.0), "R_thumb_mcp_joint": (0.0, 0.8552),
    "L_index_abad_joint": (0.0, 0.2094), "R_index_abad_joint": (-0.2094, 0.0),
    "L_index_pip_joint": (0.0, 1.5708), "R_index_pip_joint": (0.0, 1.5708),
    "L_middle_pip_joint": (0.0, 1.5708), "R_middle_pip_joint": (0.0, 1.5708),
    "L_ring_abad_joint": (-0.1745, 0.0), "R_ring_abad_joint": (0.0, 0.1745),
    "L_ring_pip_joint": (0.0, 1.5708), "R_ring_pip_joint": (0.0, 1.5708),
    "L_pinky_abad_joint": (-0.1745, 0.0), "R_pinky_abad_joint": (0.0, 0.1745),
    "L_pinky_pip_joint": (0.0, 1.5708), "R_pinky_pip_joint": (0.0, 1.5708),
}

# Ejes acoplados del URDF: q_mimic = multiplicador * q_activo (offset 0). En MuJoCo son <equality joint>.
MIMIC = {}
for _s in ("L", "R"):
    MIMIC[f"{_s}_thumb_pip_joint"] = (f"{_s}_thumb_mcp_joint", 1.33)
    MIMIC[f"{_s}_thumb_dip_joint"] = (f"{_s}_thumb_mcp_joint", 1.30)
    for _f in ("index", "middle", "ring", "pinky"):
        MIMIC[f"{_s}_{_f}_dip_joint"] = (f"{_s}_{_f}_pip_joint", 1.097)

# Regiones táctiles del mensaje HandStateArray de AimDK (uint8 por celda, 152 por mano).
TACTILE_REGIONS = (("palm", 36), ("back_of_hand", 36), ("thumb", 16), ("index", 16),
                   ("middle", 16), ("ring", 16), ("little", 16))
TACTILE_FIELDS = ("palm_touch_data", "back_of_hand_touch_data", "thumb_touch_data",
                  "index_finger_touch_data", "middle_finger_touch_data",
                  "ring_finger_touch_data", "little_finger_touch_data")
# En HandStateArray, left_hand_type/right_hand_type == 1 indica una mano diestra (OmniHand).
HAND_TYPE_DEXTEROUS = 1


def closed_end(joint: str) -> float:
    """Extremo del rango más alejado de 0 (dedo cerrado / máxima separación)."""
    lo, hi = HAND_RANGE[joint]
    return lo if abs(lo) > abs(hi) else hi


def to_radians(side: str, u) -> np.ndarray:
    """Convierte 10 valores normalizados [0, 1] de una mano ('L' o 'R') a radianes (orden SDK)."""
    u = np.clip(np.asarray(u, dtype=float), 0.0, 1.0)
    joints = LEFT_HAND_JOINTS if side == "L" else RIGHT_HAND_JOINTS
    return np.array([ui * closed_end(j) for ui, j in zip(u, joints)])


def to_normalized(side: str, q) -> np.ndarray:
    joints = LEFT_HAND_JOINTS if side == "L" else RIGHT_HAND_JOINTS
    return np.clip(np.array([qi / closed_end(j) for qi, j in zip(q, joints)]), 0.0, 1.0)


# Poses de mano en unidades normalizadas, orden HAND_SIDE_JOINTS:
#   thumb_roll, thumb_abad, thumb_mcp, index_abad, index_pip, middle_pip, ring_abad, ring_pip, pinky_abad, pinky_pip
HAND_POSES = {
    "abierta":       (0.00, 0.00, 0.00, 0.00, 0.00, 0.00, 0.00, 0.00, 0.00, 0.00),
    "puño":          (0.30, 0.90, 0.80, 0.00, 1.00, 1.00, 0.00, 1.00, 0.00, 1.00),
    # pinza: punta del pulgar a ~13 mm de la del índice (búsqueda por cinemática, ver docs/02-omnihand.md)
    "pinza":         (0.50, 0.00, 0.50, 0.00, 0.50, 0.10, 0.00, 0.10, 0.00, 0.10),
    "agarre":        (0.90, 0.00, 0.40, 0.00, 0.70, 0.75, 0.00, 0.80, 0.00, 0.85),
    "señalar":       (0.30, 0.90, 0.80, 0.00, 0.00, 1.00, 0.00, 1.00, 0.00, 1.00),
    "ok":            (0.50, 0.00, 0.50, 0.00, 0.50, 0.00, 0.50, 0.00, 0.80, 0.00),
    "pulgar_arriba": (0.00, 0.00, 0.00, 0.00, 1.00, 1.00, 0.00, 1.00, 0.00, 1.00),
    "separar":       (0.00, 0.00, 0.00, 1.00, 0.00, 0.00, 1.00, 0.00, 1.00, 0.00),
}


def pose_radians(side: str, pose: str) -> np.ndarray:
    return to_radians(side, HAND_POSES[pose])
