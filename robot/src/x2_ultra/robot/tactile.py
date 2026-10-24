"""Lectura del tacto de las OmniHand (robot real o simulador oficial) — solo lectura, sin publicar nada.

``HandStateArray`` (``/aima/hal/joint/hand/state``) trae por mano 7 campos de códigos uint8 sin calibrar
(ver ``omnihand.TACTILE_REGIONS``). Este módulo los separa por región y no crea publicadores ni timers: a
diferencia del ejemplo ``omnihand_touch`` de AimDK, no puede mover la mano.

    import rclpy
    from x2_ultra.robot.tactile import subscribe_tactile
    rclpy.init(); node = rclpy.create_node("tacto")
    subscribe_tactile(node, lambda frame: print(frame.summary()))
    rclpy.spin(node)
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from x2_ultra.robot.omnihand import HAND_TYPE_DEXTEROUS, TACTILE_FIELDS, TACTILE_REGIONS

HAND_STATE_TOPIC = "/aima/hal/joint/hand/state"
# Celdas que la OmniHand usa realmente dentro de cada campo (AimDK §5.1.8).
USED_CELLS = {"palm": 25, "back_of_hand": 0, "thumb": 16, "index": 16, "middle": 16, "ring": 16, "little": 16}


@dataclass
class HandTactile:
    side: str
    hand_type: int
    available: bool
    regions: dict = field(default_factory=dict)      # región -> np.ndarray uint8 (celdas usadas)

    def summary(self) -> dict:
        if not self.available:
            return {"side": self.side, "available": False, "hand_type": self.hand_type}
        return {"side": self.side, "available": True,
                **{r: int(v.max()) if v.size else 0 for r, v in self.regions.items()}}


@dataclass
class TactileFrame:
    sequence: int
    stamp_ns: int
    left: HandTactile
    right: HandTactile

    def summary(self) -> dict:
        return {"seq": self.sequence, "left": self.left.summary(), "right": self.right.summary()}


def _decode_side(side: str, hand_type: int, data) -> HandTactile:
    # Con tipo 0 (sin mano) o 0xFF (error) los arrays conservan el último valor válido: no son ceros reales.
    if hand_type != HAND_TYPE_DEXTEROUS:
        return HandTactile(side, hand_type, False)
    regions = {}
    for fld, (region, size) in zip(TACTILE_FIELDS, TACTILE_REGIONS):
        raw = np.asarray(getattr(data, fld), dtype=np.uint8)
        if raw.size != size:
            raise ValueError(f"{side}/{fld}: {raw.size} celdas, se esperaban {size}")
        regions[region] = raw[:USED_CELLS[region]]
    return HandTactile(side, hand_type, True, regions)


def decode(msg) -> TactileFrame:
    """Convierte un ``aimdk_msgs/HandStateArray`` (o un objeto con los mismos campos) en un ``TactileFrame``."""
    stamp = msg.header.stamp
    return TactileFrame(int(msg.header.sequence), int(stamp.sec) * 1_000_000_000 + int(stamp.nanosec),
                        _decode_side("left", int(msg.left_hand_type.value), msg.left_touch_sensors),
                        _decode_side("right", int(msg.right_hand_type.value), msg.right_touch_sensors))


def subscribe_tactile(node, callback):
    """Suscripción BEST_EFFORT de solo lectura en un nodo rclpy ya creado (ROS 2 Humble + aimdk_msgs)."""
    from aimdk_msgs.msg import HandStateArray
    from rclpy.qos import QoSProfile, ReliabilityPolicy
    qos = QoSProfile(depth=10, reliability=ReliabilityPolicy.BEST_EFFORT)
    return node.create_subscription(HandStateArray, HAND_STATE_TOPIC, lambda m: callback(decode(m)), qos)
