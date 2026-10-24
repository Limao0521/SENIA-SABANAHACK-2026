"""Contrato de la policy de marcha "brazos libres": observación, acción y escalas.

Lo comparten el entrenamiento (``x2_ultra.rl.env``) y el simulador (``x2_ultra.control.locomotion``);
si cambia algo aquí hay que reentrenar. Sigue la estructura de la policy oficial de AgiBot (mc-rl) con dos
diferencias: la red solo mueve piernas y cintura (15 ejes) y observa los brazos para compensarlos.

Observación del actor = historial de 5 pasos por término (más antiguo primero), términos en este orden:
  base_ang_vel (3) ×0.25 · projected_gravity (3) · velocity_commands (3: vx, vy, wz)
  joint_pos_rel (31, BODY_JOINTS, menos DEFAULT_POSE) · joint_vel_rel (31) ×0.05 · last_action (15)
Acción: 15 valores -> objetivo = DEFAULT_POSE + 0.25·acción (orden POLICY_JOINTS), recortada a ±5.
"""
from __future__ import annotations

from x2_ultra.robot import joints as J

OBS_JOINTS = J.BODY_JOINTS
ACTION_JOINTS = J.POLICY_JOINTS
NUM_ACTIONS = len(ACTION_JOINTS)

OBS_TERMS = (
    ("base_ang_vel", 3),
    ("projected_gravity", 3),
    ("velocity_commands", 3),
    ("joint_pos_rel", len(OBS_JOINTS)),
    ("joint_vel_rel", len(OBS_JOINTS)),
    ("last_action", NUM_ACTIONS),
)
HISTORY = 5
FRAME_DIM = sum(d for _, d in OBS_TERMS)
OBS_DIM = FRAME_DIM * HISTORY

ANG_VEL_SCALE = 0.25
JOINT_VEL_SCALE = 0.05
ACTION_SCALE = 0.25
ACTION_CLIP = 5.0
OBS_CLIP = 100.0

SIM_DT_TRAIN = 0.005
DECIMATION_TRAIN = 4
POLICY_DT = SIM_DT_TRAIN * DECIMATION_TRAIN   # 50 Hz

# Rango de órdenes de velocidad del entrenamiento (m/s, m/s, rad/s).
CMD_LIMITS = {"vx": (-0.6, 1.0), "vy": (-0.4, 0.4), "wz": (-1.0, 1.0)}
