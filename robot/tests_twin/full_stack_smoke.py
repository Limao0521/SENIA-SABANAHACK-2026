#!/usr/bin/env python3
"""Comprueba en vivo cámaras, LiDAR, TF y audio del gemelo con MC activo.

Ejecutar dentro de ``x2-twin`` con el gemelo arrancado. Las suscripciones
activan el render que el gemelo evita cuando no hay consumidores.
"""
from __future__ import annotations

import argparse
import json
import time

import numpy as np
import rclpy
from aimdk_msgs.msg import AudioCapture
from aimdk_msgs.msg import TtsPriorityLevel
from aimdk_msgs.srv import GetVolume, PlayTts
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import CameraInfo, Image, PointCloud2
from tf2_msgs.msg import TFMessage

from x2_ultra.twin import contract as C
from x2_ultra.twin.ros_hal import qos
from x2_ultra.twin.sensors import POINT_FIELDS, camera_setups


def stamp(msg) -> float:
    t = msg.header.stamp
    return t.sec + t.nanosec * 1e-9


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--camaras", choices=("reducida", "media", "real"), default="reducida")
    ap.add_argument("--segundos", type=float, default=20.0)
    ap.add_argument("--require-mic", action="store_true", help="fallar si no llegan muestras del micrófono")
    ap.add_argument("--tts", action="store_true", help="reproducir una frase corta y exigir respuesta exitosa")
    args = ap.parse_args()
    if args.segundos <= 0:
        ap.error("--segundos debe ser positivo")
    rclpy.init()
    node = Node("x2_twin_full_stack_smoke")
    errors: list[str] = []
    frames: dict[str, list[dict]] = {}
    infos: dict[str, list[dict]] = {}
    image_counts: dict[str, int] = {}
    subscriptions = []

    for setup in camera_setups(args.camaras):
        key = setup.key
        frames[key], infos[key], image_counts[key] = [], [], 0

        def on_image(msg: Image, s=setup):
            image_counts[s.key] += 1
            if len(frames[s.key]) >= 5:
                return
            data = np.frombuffer(msg.data, dtype=np.uint8)
            sample = data[::max(1, len(data) // 4096)]
            frames[s.key].append({"stamp": stamp(msg), "frame_id": msg.header.frame_id,
                                  "width": msg.width, "height": msg.height, "encoding": msg.encoding,
                                  "step": msg.step, "bytes": len(data),
                                  "variedad": int(np.unique(sample).size),
                                  "datos_no_cero": bool(np.any(sample))})

        def on_info(msg: CameraInfo, s=setup):
            if len(infos[s.key]) < 2:
                infos[s.key].append({"stamp": stamp(msg), "frame_id": msg.header.frame_id,
                                     "width": msg.width, "height": msg.height,
                                     "fx": msg.k[0], "fy": msg.k[4]})

        subscriptions.append(node.create_subscription(
            Image, C.camera_topic(setup.spec, "image"), on_image, qos(setup.spec.image_qos)))
        subscriptions.append(node.create_subscription(
            CameraInfo, C.camera_topic(setup.spec, "info"), on_info, qos(setup.spec.info_qos)))

    lidar: list[dict] = []
    lidar_count = 0

    def on_lidar(msg: PointCloud2):
        nonlocal lidar_count
        lidar_count += 1
        if len(lidar) >= 5:
            return
        sampled = np.frombuffer(msg.data, dtype="<f4").reshape(-1, 8)[::max(1, msg.width // 2048), :3] \
            if msg.point_step == 32 and len(msg.data) % 32 == 0 else np.empty((0, 3))
        valid = np.isfinite(sampled).all(axis=1) & (np.linalg.norm(sampled, axis=1) > 0.05)
        lidar.append({"stamp": stamp(msg), "frame_id": msg.header.frame_id, "width": msg.width,
                      "point_step": msg.point_step, "row_step": msg.row_step, "bytes": len(msg.data),
                      "fields": [(f.name, f.offset, f.datatype) for f in msg.fields],
                      "puntos_validos_muestra": int(valid.sum())})

    subscriptions.append(node.create_subscription(PointCloud2, C.LIDAR_TOPIC, on_lidar, qos(C.LIDAR_QOS)))
    tf_children: set[str] = set()

    def on_tf(msg: TFMessage):
        tf_children.update(t.child_frame_id for t in msg.transforms)

    subscriptions.append(node.create_subscription(
        TFMessage, "/tf_static", on_tf,
        QoSProfile(depth=10, reliability=ReliabilityPolicy.RELIABLE,
                   durability=DurabilityPolicy.TRANSIENT_LOCAL)))
    audio: list[dict] = []
    audio_count = 0

    def on_audio(msg: AudioCapture):
        nonlocal audio_count
        audio_count += 1
        if len(audio) >= 5:
            return
        audio.append({"stamp": msg.stamps.sec + msg.stamps.nanosec * 1e-9,
                      "mic_channels": msg.mic_channels, "ref_channels": msg.ref_channels,
                      "channels": msg.info.channels, "rate": msg.info.sample_rate,
                      "bytes": len(msg.data.data)})

    subscriptions.append(node.create_subscription(AudioCapture, C.AUDIO_CAPTURE_TOPIC, on_audio,
                                                  qos(C.AUDIO_CAPTURE_QOS)))
    volume_service = node.create_client(GetVolume, C.service("GetVolume"))
    audio_service = volume_service.wait_for_service(timeout_sec=3.0)
    tts_result = None
    if args.tts:
        tts_client = node.create_client(PlayTts, C.service("PlayTts"))
        if tts_client.wait_for_service(timeout_sec=3.0):
            req = PlayTts.Request()
            req.header.header.stamp = node.get_clock().now().to_msg()
            req.tts_req.text = "Prueba breve del gemelo X2"
            req.tts_req.domain, req.tts_req.trace_id = "x2_twin_smoke", "full_stack"
            req.tts_req.is_interrupted = True
            req.tts_req.priority_level.value = TtsPriorityLevel.INTERACTION_L6
            future = tts_client.call_async(req)
            rclpy.spin_until_future_complete(node, future, timeout_sec=8.0)
            response = future.result() if future.done() and future.exception() is None else None
            tts_result = bool(response and response.tts_resp.is_success)
        else:
            tts_result = False
    start = time.monotonic()
    until = start + args.segundos
    while time.monotonic() < until:
        rclpy.spin_once(node, timeout_sec=0.1)
    duration = time.monotonic() - start

    for setup in camera_setups(args.camaras):
        key = setup.key
        images, camera_infos = frames[key], infos[key]
        if len(images) < 3 or not camera_infos:
            errors.append(f"{key}: menos de tres imágenes o sin camera_info")
            continue
        enc, bpp = ("16UC1", 2) if setup.spec.depth else ("rgb8", 3)
        for image in images:
            if (image["width"], image["height"], image["encoding"], image["step"], image["bytes"]) != (
                    setup.width, setup.height, enc, setup.width * bpp, setup.width * setup.height * bpp):
                errors.append(f"{key}: formato o tamaño incorrecto")
                break
            if image["frame_id"] != setup.spec.frame_id or image["stamp"] <= 0:
                errors.append(f"{key}: frame_id o tiempo incorrecto")
                break
            if not image["datos_no_cero"] or image["variedad"] < 2:
                errors.append(f"{key}: imagen vacía o uniforme")
                break
        if any(b["stamp"] <= a["stamp"] for a, b in zip(images, images[1:])):
            errors.append(f"{key}: tiempo no creciente")
        if (camera_infos[0]["width"], camera_infos[0]["height"], camera_infos[0]["frame_id"]) != (
                setup.width, setup.height, setup.spec.frame_id):
            errors.append(f"{key}: camera_info no coincide")

    if len(lidar) < 3:
        errors.append("LiDAR: menos de tres nubes")
    else:
        fields = list(POINT_FIELDS)
        for cloud in lidar:
            if (cloud["frame_id"] != C.LIDAR_FRAME or cloud["stamp"] <= 0 or cloud["width"] == 0 or
                    cloud["point_step"] != 32 or cloud["row_step"] != cloud["bytes"] or
                    cloud["fields"] != fields or cloud["puntos_validos_muestra"] == 0):
                errors.append("LiDAR: datos o campos incorrectos")
                break
    if not audio_service:
        errors.append("servicio de audio GetVolume ausente")
    if args.tts and not tts_result:
        errors.append("PlayTts no respondió con éxito")
    if args.require_mic and len(audio) < 3:
        errors.append("micrófono: menos de tres bloques")
    for item in audio:
        if (item["mic_channels"], item["ref_channels"], item["channels"], item["rate"]) != (4, 2, 6, 16000):
            errors.append("micrófono: formato incorrecto")
            break
        if item["bytes"] == 0 or item["bytes"] % (6 * 2):
            errors.append("micrófono: bloque PCM incompleto")
            break
    if not tf_children:
        errors.append("no llegó /tf_static")

    report = {"camaras": frames, "camera_info": infos, "lidar": lidar, "audio_capture": audio,
              "audio_service": audio_service, "tts_result": tts_result,
              "duracion_observada_s": round(duration, 3),
              "frecuencias_observadas_hz": {
                  **{key: round(count / duration, 2) for key, count in image_counts.items()},
                  "lidar": round(lidar_count / duration, 2),
                  "microfono": round(audio_count / duration, 2),
              },
              "tf_static_children": sorted(tf_children),
              "aprobado": not errors, "fallos": errors}
    print(json.dumps(report, ensure_ascii=False), flush=True)
    node.destroy_node()
    rclpy.shutdown()
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
