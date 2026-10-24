#!/usr/bin/env python3
"""Alarma hablada de la bóveda: reproduce un WAV por los altavoces del X2 (AimDK).

Autónomo a propósito: NO importa nada del repo (el repo vive dentro de la bóveda
cifrada y puede estar cerrado). Hace lo mismo que hriego_v2.robot_io.AimdkAudioPlayer:
  1. pide el foco de audio (/aimdk_5Fmsgs/srv/RequestAudioFocus, FocusRequester
     con pkg_name propio y priority=8);
  2. envía el WAV como bloques PCM S16LE a /aima/hal/audio/playback
     (aimdk_msgs/AudioPlayback), a ritmo de tiempo real;
  3. suelta el foco (/aimdk_5Fmsgs/srv/AbandonAudioFocus).

Solo publica en el topic de audio y llama a los dos servicios de foco: no puede
mover el robot. Lo lanza `boveda.sh` en un contenedor desechable de x2-hri:latest
(red/ipc del host + perfil DDS de AgiBot), montando solo ~/.x2_boveda_tools (ro):

  python3 /boveda/boveda_alarma.py /boveda/alarma.wav [repeticiones]

Códigos de salida: 0 ok · 2 WAV inválido · 3 ROS/servicio no disponible · 4 foco denegado.
"""
from __future__ import annotations

import os
import sys
import threading
import time
import wave

TOPIC = "/aima/hal/audio/playback"
SRV_REQUEST = "/aimdk_5Fmsgs/srv/RequestAudioFocus"
SRV_ABANDON = "/aimdk_5Fmsgs/srv/AbandonAudioFocus"
PRIORITY = 8          # igual que AimdkAudioPlayer; debe coincidir al pedir y al soltar
CHUNK_S = 0.04        # bloques de 40 ms, como AimdkAudioPlayer / play_audio_stream.py
LEAD_S = 0.2          # adelanto con el que se envía respecto al tiempo real


def leer_wav(path: str) -> tuple[int, int, bytes]:
    with wave.open(path, "rb") as wav:
        channels, width, rate = wav.getnchannels(), wav.getsampwidth(), wav.getframerate()
        pcm = wav.readframes(wav.getnframes())
    if width != 2:
        raise ValueError(f"WAV de {8 * width} bits; se esperaba S16LE")
    return channels, rate, pcm


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    try:
        channels, rate, pcm = leer_wav(sys.argv[1])
    except Exception as exc:  # noqa: BLE001
        print(f"alarma: WAV inválido: {exc}", file=sys.stderr)
        return 2
    repeticiones = max(1, int(sys.argv[2])) if len(sys.argv) > 2 else 1

    import rclpy
    from rclpy.executors import SingleThreadedExecutor
    from rclpy.qos import QoSProfile, QoSReliabilityPolicy
    from aimdk_msgs.msg import AudioPlayback, CommonState, FocusRequester
    from aimdk_msgs.srv import AbandonAudioFocus, RequestAudioFocus

    rclpy.init()
    pkg_name = f"x2_boveda_alarma_{os.getpid()}"
    node = rclpy.create_node(pkg_name)
    executor = SingleThreadedExecutor()
    executor.add_node(node)
    spin = threading.Thread(target=executor.spin, daemon=True)
    spin.start()

    def cerrar_ros() -> None:
        executor.shutdown(timeout_sec=1.0)
        spin.join(timeout=2.0)
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()

    pub = node.create_publisher(AudioPlayback, TOPIC,
                                QoSProfile(depth=10, reliability=QoSReliabilityPolicy.RELIABLE))
    cli_req = node.create_client(RequestAudioFocus, SRV_REQUEST)
    cli_rel = node.create_client(AbandonAudioFocus, SRV_ABANDON)

    def llamar(cliente, peticion, timeout_s: float = 3.0):
        if not cliente.wait_for_service(timeout_sec=timeout_s):
            raise RuntimeError(f"servicio de audio AimDK no disponible: {cliente.srv_name}")
        future = cliente.call_async(peticion)
        hecho = threading.Event()
        future.add_done_callback(lambda _f: hecho.set())
        if not hecho.wait(timeout_s):
            raise TimeoutError(f"sin respuesta de {cliente.srv_name}")
        return future.result()

    def requester() -> FocusRequester:
        r = FocusRequester()
        r.pkg_name = pkg_name
        r.priority = PRIORITY
        return r

    # Espera breve a que DDS empareje el publicador con hal_audio (si no, se pierden
    # los primeros bloques). No es fatal si no aparece: se intenta igualmente.
    t_fin = time.monotonic() + 3.0
    while pub.get_subscription_count() == 0 and time.monotonic() < t_fin:
        time.sleep(0.05)

    try:
        concedido, ultimo_error = False, ""
        for _ in range(5):  # si HRIEGO está hablando, reintenta unos segundos
            try:
                peticion = RequestAudioFocus.Request()
                peticion.focus_requester = requester()
                resp = llamar(cli_req, peticion)
            except Exception as exc:  # noqa: BLE001
                print(f"alarma: {exc}", file=sys.stderr)
                return 3
            if resp.reponse.status.value == CommonState.SUCCESS and resp.focus_response.focus_gain:
                concedido = True
                break
            ultimo_error = resp.reponse.message
            time.sleep(1.0)
        if not concedido:
            print(f"alarma: AimDK no concedió el foco de audio: {ultimo_error}", file=sys.stderr)
            return 4

        try:
            bytes_por_s = rate * channels * 2
            trozo = max(2 * channels, int(bytes_por_s * CHUNK_S) // (2 * channels) * (2 * channels))
            for rep in range(repeticiones):
                t0 = time.monotonic()
                for i, offset in enumerate(range(0, len(pcm), trozo)):
                    bloque = pcm[offset:offset + trozo]
                    msg = AudioPlayback()
                    msg.stamps = node.get_clock().now().to_msg()
                    msg.info.channels = channels
                    msg.info.sample_rate = rate
                    msg.info.size = len(bloque)
                    msg.info.sample_format = "s16le"
                    msg.info.coding_format = "pcm"
                    msg.data.data = bloque
                    msg.pkg_name = pkg_name
                    pub.publish(msg)
                    adelanto = (i + 1) * CHUNK_S - (time.monotonic() - t0) - LEAD_S
                    if adelanto > 0:
                        time.sleep(adelanto)
                cola = len(pcm) / bytes_por_s - (time.monotonic() - t0)
                if cola > 0:
                    time.sleep(cola)
                if rep + 1 < repeticiones:
                    time.sleep(0.8)
        finally:
            try:
                peticion = AbandonAudioFocus.Request()
                peticion.focus_requester = requester()
                llamar(cli_rel, peticion)
            except Exception as exc:  # noqa: BLE001
                print(f"alarma: no se pudo liberar el foco de audio: {exc}", file=sys.stderr)
    finally:
        if rclpy.ok():
            cerrar_ros()
    print(f"alarma: reproducida ({len(pcm) / (rate * channels * 2):.1f} s x {repeticiones})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
