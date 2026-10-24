"""Serialización CDR rápida de los mensajes HAL del gemelo (sin ROS).

Con objetos de rclpy, Python no llega a tiempo real con el tráfico de la HAL real: ~3 900 mensajes/s publicados
(estados a 500 Hz, IMU…) y ~1 600 órdenes/s del MC. Aquí cada mensaje que se publica es una plantilla de bytes CDR
(little endian, XCDR1, como FastRTPS) con los nombres ya escritos; al publicar solo se copian la marca de tiempo y
los números en sus posiciones. Las órdenes se leen directamente del CDR. ``tests/test_twin_cdr.py`` y
``twin.sh test`` comprueban que los bytes coinciden con ``rclpy.serialization``.

Reglas de CDR usadas: cabecera de 4 bytes (00 01 00 00); cada primitivo alineado a su tamaño contando desde el
final de esa cabecera; string = uint32 longitud (con el '\\0') + bytes + '\\0'; secuencia = uint32 cuenta + elementos;
array fijo = elementos sin cuenta.
"""
from __future__ import annotations

import struct

import numpy as np

ENCAPSULATION = b"\x00\x01\x00\x00"


class CdrWriter:
    def __init__(self):
        self.buf = bytearray(ENCAPSULATION)

    def _align(self, n: int) -> None:
        self.buf += b"\x00" * ((-(len(self.buf) - 4)) % n)

    def u8(self, v: int = 0) -> int:
        off = len(self.buf)
        self.buf += struct.pack("<B", v)
        return off

    def u8_array(self, n: int) -> int:
        off = len(self.buf)
        self.buf += b"\x00" * n
        return off

    def u16(self, v: int = 0) -> int:
        self._align(2)
        off = len(self.buf)
        self.buf += struct.pack("<H", v)
        return off

    def i32(self, v: int = 0) -> int:
        self._align(4)
        off = len(self.buf)
        self.buf += struct.pack("<i", v)
        return off

    def u32(self, v: int = 0) -> int:
        self._align(4)
        off = len(self.buf)
        self.buf += struct.pack("<I", v)
        return off

    def f64(self, v: float = 0.0) -> int:
        self._align(8)
        off = len(self.buf)
        self.buf += struct.pack("<d", v)
        return off

    def f64_array(self, n: int) -> list[int]:
        return [self.f64() for _ in range(n)]

    def string(self, s: str) -> None:
        data = s.encode() + b"\x00"
        self.u32(len(data))
        self.buf += data

    def time(self) -> int:
        off = self.i32()
        self.u32()
        return off


class Template:
    """Mensaje CDR fijo con huecos: marca de tiempo (sec, nsec) y un vector de float64 por campo."""

    def __init__(self, w: CdrWriter, stamp_offsets: list[int], fields: dict[str, list[int]],
                 bytes_fields: dict[str, int] | None = None):
        self.template = bytes(w.buf)
        self.stamp_offsets = stamp_offsets
        for offs in fields.values():
            assert all((o - 4) % 8 == 0 for o in offs)
        self.idx = {k: (np.array(v) - 4) // 8 for k, v in fields.items()}
        self.bytes_fields = bytes_fields or {}

    def pack(self, sec: int, nsec: int, values: dict[str, np.ndarray],
             byte_values: dict[str, bytes] | None = None) -> bytes:
        buf = bytearray(self.template)
        for off in self.stamp_offsets:
            struct.pack_into("<iI", buf, off, sec, nsec)
        view = np.frombuffer(buf, dtype="<f8", offset=4, count=(len(buf) - 4) // 8)
        for k, v in values.items():
            view[self.idx[k]] = v
        if byte_values:
            for k, data in byte_values.items():
                off = self.bytes_fields[k]
                buf[off:off + len(data)] = data
        return bytes(buf)


def _message_header(w: CdrWriter, frame_id: str = "") -> int:
    """aimdk_msgs/MessageHeader: stamp, frame_id, sequence, meas_stamp."""
    off = w.time()
    w.string(frame_id)
    w.u32(0)
    w.time()
    return off


def _std_header(w: CdrWriter, frame_id: str) -> int:
    off = w.time()
    w.string(frame_id)
    return off


def joint_state_array(names) -> Template:
    """aimdk_msgs/JointStateArray con los nombres dados; campos 'position', 'velocity', 'effort'."""
    w = CdrWriter()
    st = _message_header(w)
    w.u8(0)                                   # DomainErrorState.value
    w.u32(len(names))
    pos, vel, eff = [], [], []
    for n in names:
        w.string(n)
        pos.append(w.f64())
        vel.append(w.f64())
        eff.append(w.f64())
        w.u16(0)                              # error_code
    return Template(w, [st], {"position": pos, "velocity": vel, "effort": eff})


TOUCH_FIELDS = (("palm_touch_data", 36), ("back_of_hand_touch_data", 36), ("thumb_touch_data", 16),
                ("index_finger_touch_data", 16), ("middle_finger_touch_data", 16), ("ring_finger_touch_data", 16),
                ("little_finger_touch_data", 16))


def hand_state_array(left_names, right_names, hand_type: int) -> Template:
    """aimdk_msgs/HandStateArray; campos 'position', 'velocity', 'effort' (izquierda + derecha) y el tacto como
    bytes ('L_palm_touch_data', 'R_index_finger_touch_data'...)."""
    w = CdrWriter()
    st = _message_header(w)
    pos, vel, eff, touch = [], [], [], {}
    for side, names in (("L", left_names), ("R", right_names)):
        w.u8(hand_type)
        w.u32(len(names))
        for n in names:
            w.string(n)
            pos.append(w.f64())
            vel.append(w.f64())
            eff.append(w.f64())
            w.i32(0)                          # state
            w.i32(0)                          # faultcode
        for fld, n in TOUCH_FIELDS:
            touch[f"{side}_{fld}"] = w.u8_array(n)
    return Template(w, [st], {"position": pos, "velocity": vel, "effort": eff}, touch)


def imu(frame_id: str) -> Template:
    """sensor_msgs/Imu; campos 'orientation' (x, y, z, w), 'angular_velocity', 'linear_acceleration'."""
    w = CdrWriter()
    st = _std_header(w, frame_id)
    ori = w.f64_array(4)
    w.f64_array(9)
    gyro = w.f64_array(3)
    w.f64_array(9)
    acc = w.f64_array(3)
    w.f64_array(9)
    return Template(w, [st], {"orientation": ori, "angular_velocity": gyro, "linear_acceleration": acc})


def odometry(frame_id: str, child_frame_id: str) -> Template:
    """nav_msgs/Odometry; campos 'position', 'orientation' (x, y, z, w), 'linear', 'angular'."""
    w = CdrWriter()
    st = _std_header(w, frame_id)
    w.string(child_frame_id)
    pos = w.f64_array(3)
    ori = w.f64_array(4)
    w.f64_array(36)
    lin = w.f64_array(3)
    ang = w.f64_array(3)
    w.f64_array(36)
    return Template(w, [st], {"position": pos, "orientation": ori, "linear": lin, "angular": ang})


# --- lectura de órdenes ---------------------------------------------------------------------------


def _align(p: int, n: int) -> int:
    return p + ((-(p - 4)) % n)


def _read_string(b: bytes, p: int) -> tuple[str, int]:
    p = _align(p, 4)
    (n,) = struct.unpack_from("<I", b, p)
    return b[p + 4:p + 3 + n].decode(), p + 4 + n


def _skip_message_header(b: bytes) -> int:
    p = 4 + 8                                  # stamp
    _, p = _read_string(b, p)                  # frame_id
    p = _align(p, 4) + 4                       # sequence
    p = _align(p, 4) + 8                       # meas_stamp
    return p


def parse_joint_command_array(b: bytes) -> tuple[tuple[str, ...], np.ndarray]:
    """aimdk_msgs/JointCommandArray -> (nombres, matriz n×5 [position, velocity, effort, stiffness, damping])."""
    p = _skip_message_header(b)
    p = _align(p, 4)
    (count,) = struct.unpack_from("<I", b, p)
    p += 4
    names, vals = [], np.empty((count, 5))
    for i in range(count):
        name, p = _read_string(b, p)
        p = _align(p, 8)
        vals[i] = struct.unpack_from("<5d", b, p)
        p += 40
        names.append(name)
    return tuple(names), vals


def parse_hand_command_array(b: bytes) -> tuple[tuple[str, ...], np.ndarray]:
    """aimdk_msgs/HandCommandArray -> (nombres izq. + der., matriz n×5 [position, velocity, acceleration,
    deceleration, effort])."""
    p = _skip_message_header(b)
    names, rows = [], []
    for _side in range(2):
        p += 1                                 # HandType (uint8)
        p = _align(p, 4)
        (count,) = struct.unpack_from("<I", b, p)
        p += 4
        for _ in range(count):
            name, p = _read_string(b, p)
            p = _align(p, 8)
            rows.append(struct.unpack_from("<5d", b, p))
            p += 40
            names.append(name)
    return tuple(names), np.array(rows).reshape(-1, 5)
