"""Contrato común de backends del runtime HRI (T5) — EXACTO según docs/reto-2/HRI_RUNTIME_SPEC.md.

Tres implementaciones: ``ShowBackend`` (motor del show del reto 2, MuJoCo), ``TwinBackend``
(gemelo oficial x2-twin) y ``AimdkBackend`` (robot real vía AimDK ROS 2; ``dry_run=True`` por
defecto: registra las órdenes y nunca publica).

Reglas de seguridad de movimiento del spec (válidas para TODOS los backends):
- límite por articulación **10 rad/s** fuera de emotes;
- emotes limitados por especificación: cadera roll/pitch 40, cadera yaw 30, cintura 20,
  brazos 20, cabeza 30 rad/s;
- torque máx 120 N·m; bucle bajo nivel 500 Hz (los backends mandan objetivos a 50 Hz);
- **nunca gestos mientras camina**;
- cintura acotada (±0.10/0.05/0.03) con filtro existente; reverencia con tope 0.10 rad/0.06 rad/s.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

# --- Constantes de seguridad del spec (compartidas por los backends) -------------------------
TICK = 0.02                  # s: bucle de comandos a 50 Hz
JOINT_VMAX = 10.0            # rad/s por articulación fuera de emotes
EMOTE_VMAX = {               # rad/s por grupo durante emotes (spec)
    "hip_roll_pitch": 40.0,
    "hip_yaw": 30.0,
    "waist": 20.0,
    "arms": 20.0,
    "head": 30.0,
}
JOINT_AMAX = 40.0            # rad/s²: límite de aceleración (motor_fix --amax por defecto)
TORQUE_MAX_NM = 120.0        # N·m (lo aplica el MC/HAL; se documenta aquí)
LOW_LEVEL_HZ = 500.0         # Hz del bucle bajo nivel (MC; no del backend)
WAIST_BOUNDS = (0.10, 0.05, 0.03)          # rad (yaw, pitch, roll): canal SONIC
WAIST_BOUNDS_GENTLE = (0.18, 0.10, 0.05)   # rad: presets cinemáticos (venia, D2 fix a)
WAIST_SLEW = (0.4, 0.2, 0.2)               # rad/s por eje
WAIST_SLEW_GENTLE = (0.4, 0.06, 0.06)      # rad/s: reverencia ≤ 0.06 rad/s
BOW_CAP_RAD = 0.10           # rad: tope de la reverencia (pitch)
BOW_RATE = 0.06              # rad/s


@runtime_checkable
class Backend(Protocol):
    def state(self) -> dict: ...            # {"t","pelvis_z","tilt_deg","mode","stable","walking":bool,"fall":bool}
    def stream_upper_body(self, t: float, arms14, waist3, head2) -> None: ...   # 50 Hz, aplica límites
    def play_emote(self, motion_id: int, area: int, t_dispatch: float) -> None: ...  # cinemático por CSV (sim y real vía URS)
    def point(self, direction: str, t_stroke: float, hold_s: float = 1.5) -> None: ...
    def walk_to(self, area: str) -> None: ...   # bloquea gestos mientras camina
    def set_velocity(self, vx: float, wz: float) -> None: ...
    def dispatch_d1(self, area: str) -> None: ...  # stub (API D1 desconocida)
    def stop(self) -> None: ...
    def estop(self) -> None: ...
