"""Servidor del visor VR: páginas y escena por HTTPS, y un WebSocket con las gafas.

Un solo puerto TLS (por defecto 8443) sirve la página WebXR (``web/``), la escena exportada (``export.py``) y el
WebSocket ``/ws``. El certificado es autofirmado (``ensure_certificate``): la primera vez el navegador de las Quest
pide confirmarlo («Avanzado → Continuar»). Por cable USB, ``adb reverse tcp:8443 tcp:8443`` y ``https://localhost:8443``.

Mensajes con las gafas (``/ws``):

- PC → gafas, binario ``[1,0,0,0] + float32[]``: estado a 50 Hz (``state_layout`` en scene.json): tiempo, generación
  del huésped, su pose, las cajas de las puertas (para las colisiones), la posición de los robots y la pose
  (pos + cuaternión wxyz) de cada cuerpo móvil.
- PC → gafas, binario ``[2,0,0,0] + WAV``: audio que suena en las gafas (la voz del robot).
- PC → gafas, JSON: ``teleport`` (vuelve a la salida), ``subtitle``, ``status``, ``cue`` (lo que le toca
  decir al visitante).
- Gafas → PC, binario ``[1, gatillo, 0, 0] + PCM s16le 16 kHz mono``: el micrófono (``mic.QuestMic``).
- Gafas → PC, JSON: ``pose`` (x, y, yaw del huésped), ``ptt`` (gatillo pulsado o suelto), ``reset``.

API local para el compañero de la conversación (``ws://127.0.0.1:8766``, sin TLS, solo desde el PC): recibe en binario
el PCM del micrófono de las gafas y en JSON los eventos (``ptt``, ``quest`` conectadas, ``speech`` lo que dice el
robot); acepta ``{"t": "say", "text": ...}`` (el robot lo dice: subtítulo + voz), ``{"t": "subtitle", "text": ...}``
y binario WAV (suena en las gafas y en el PC).
"""
from __future__ import annotations

import asyncio
import base64
import json
import mimetypes
import queue
import socket
import ssl
import subprocess
import threading
import time
from pathlib import Path

import mujoco
import numpy as np
from websockets.asyncio.server import broadcast, serve
from websockets.datastructures import Headers
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Response

from x2_ultra.show import guest as G
from x2_ultra.vr.export import export_scene
from x2_ultra.vr.mic import QuestMic

WEB_DIR = Path(__file__).resolve().parent / "web"
QR_DIR = Path(__file__).resolve().parents[3] / "tests" / "fixtures"     # QR de prueba de la demo (/qr/ en el celular)
QR_LABELS = {"qr_vip_compacto.png": "Laura · VIP", "qr_regular_compacto.png": "Sebastian · socio",
             "qr_invitado_compacto.png": "Mario · invitado", "qr_club_staff_compacto.png": "Luis · personal"}
STATE_HZ = 50
MSG_STATE, MSG_AUDIO = 1, 2
MSG_MIC = 1


def lan_ips() -> list[str]:
    """IPv4 de las interfaces de red (sin la de bucle ni las de Docker)."""
    try:
        out = subprocess.run(["ip", "-4", "-o", "addr"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        out = ""
    ips = []
    for line in out.splitlines():
        parts = line.split()
        name, addr = parts[1], parts[3].split("/")[0]
        if name != "lo" and not name.startswith(("docker", "br-", "veth")):
            ips.append(addr)
    if not ips:                                # sin `ip`: la IP de la ruta por defecto
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            try:
                s.connect(("10.255.255.255", 1))
                ips.append(s.getsockname()[0])
            except OSError:
                pass
    return ips


def ensure_certificate(directory: Path, ips: list[str]) -> tuple[Path, Path]:
    """Certificado autofirmado para las IPs del PC y localhost; se rehace si cambian las IPs."""
    directory.mkdir(parents=True, exist_ok=True)
    cert, key, names = directory / "cert.pem", directory / "key.pem", directory / "names.txt"
    wanted = ",".join(["DNS:localhost", "IP:127.0.0.1"] + [f"IP:{ip}" for ip in ips])
    if cert.is_file() and key.is_file() and names.is_file() and set(wanted.split(",")) <= \
            set(names.read_text().split(",")):
        return cert, key
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "825",
                    "-keyout", str(key), "-out", str(cert), "-subj", "/CN=x2-club-vr",
                    "-addext", f"subjectAltName={wanted}"], check=True, capture_output=True)
    key.chmod(0o600)
    names.write_text(wanted)
    return cert, key


class VRServer:
    def __init__(self, engine, out_dir: Path, port: int = 8443, api_port: int = 8766,
                 mic: QuestMic | None = None, cert_dir: Path | None = None):
        self.engine = engine
        self.out_dir = Path(out_dir)
        self.scene_dir = self.out_dir / "scene"
        self.port, self.api_port = port, api_port
        self.mic = mic
        self.cert_dir = cert_dir or self.out_dir / "cert"
        self.events: queue.Queue[tuple] = queue.Queue()     # para la app: (tipo, valor)
        self.ips = lan_ips()
        self.scene: dict | None = None
        self.ready = threading.Event()
        self.error: str | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._stop: asyncio.Event | None = None
        self._quest: set = set()
        self._api: set = set()
        self._driver = None
        self._last_status = ""
        self._generation = -1
        self.ptt = False
        self.mic_listeners: list = []                    # callable(pcm, ptt) en el hilo del servidor

    # ------------------------------------------------------------------------------ desde la app (otro hilo)
    @property
    def urls(self) -> list[str]:
        return [f"https://{ip}:{self.port}/" for ip in self.ips] + [f"https://localhost:{self.port}/ (USB)"]

    @property
    def connected(self) -> int:
        return len(self._quest)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="vr-server", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._loop is not None and self._stop is not None:
            self._loop.call_soon_threadsafe(self._stop.set)
        if self._thread is not None:
            self._thread.join(timeout=3)
        if self.mic is not None:
            self.mic.stop()

    def subtitle(self, text: str, who: str = "ultra") -> None:
        self._send_json({"t": "subtitle", "text": text, "who": who})
        self._send_api({"t": "speech", "text": text, "who": who})

    def cue(self, text: str) -> None:
        """Lo que le toca decir al visitante (vacío: nada); las gafas lo muestran donde los subtítulos."""
        self._send_json({"t": "cue", "text": text})

    def status(self, text: str) -> None:
        if text != self._last_status:
            self._last_status = text
            self._send_json({"t": "status", "text": text})

    def play_wav(self, wav: bytes, on_pc: bool = False) -> None:
        """Hace sonar un WAV en las gafas (y en los altavoces del PC si ``on_pc``)."""
        if self._loop is not None:
            self._loop.call_soon_threadsafe(broadcast, set(self._quest), bytes([MSG_AUDIO, 0, 0, 0]) + wav)
        if on_pc:
            threading.Thread(target=_play_on_pc, args=(wav,), daemon=True).start()

    def _send_json(self, msg: dict) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(broadcast, set(self._quest), json.dumps(msg, ensure_ascii=False))

    def _send_api(self, msg: dict) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(broadcast, set(self._api), json.dumps(msg, ensure_ascii=False))

    # ------------------------------------------------------------------------------ hilo del servidor
    def _run(self) -> None:
        try:
            self.scene = self._export()
            cert, key = ensure_certificate(self.cert_dir, self.ips)
            tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            tls.load_cert_chain(cert, key)
        except Exception as exc:                        # noqa: BLE001 - se informa a la app
            self.error = f"{type(exc).__name__}: {exc}"
            self.ready.set()
            return
        if self.mic is not None:
            self.events.put(("info", self.mic.start()))
        asyncio.run(self._main(tls))

    def _export(self) -> dict:
        """Exporta la escena (con su propio MjData en la postura inicial) y añade lo que el visor necesita para
        mover al huésped: el mapa de ocupación, sus velocidades y el orden de puertas y robots en el estado."""
        eng = self.engine
        model = eng.model
        data = mujoco.MjData(model)
        mujoco.mj_resetDataKeyframe(model, data, eng.home)
        scene = export_scene(model, data, self.scene_dir)
        guest = eng.guest
        occ = guest.occupancy
        scene["guest"] = {
            "start": list(guest.start), "radius": G.RADIUS, "robot_clearance": G.ROBOT_CLEARANCE,
            "walk": G.WALK_SPEED, "run": G.RUN_SPEED, "back": G.BACK_SPEED, "side": G.SIDE_SPEED,
            "occupancy": {"origin": occ.origin.tolist(), "cell": G.CELL, "shape": occ.shape.tolist(),
                          "bits": base64.b64encode(np.packbits(occ.grid.ravel())).decode()},
        }
        self._door_geoms = list(guest.door_geoms)
        self._rigs = list(eng.rigs.values())
        self._bodies = np.array(scene["bodies"], dtype=int)
        self._generation = guest.generation
        scene["state_layout"] = {"doors": len(self._door_geoms), "robots": len(self._rigs),
                                 "bodies": len(self._bodies)}
        (self.scene_dir / "scene.json").write_text(json.dumps(scene))
        return scene

    async def _main(self, tls: ssl.SSLContext) -> None:
        self._loop = asyncio.get_running_loop()
        self._stop = asyncio.Event()
        try:
            async with serve(self._quest_handler, "0.0.0.0", self.port, ssl=tls, process_request=self._http,
                             compression=None, max_size=2 ** 22, ping_interval=5, ping_timeout=10), \
                    serve(self._api_handler, "127.0.0.1", self.api_port, compression=None, max_size=2 ** 24):
                self.ready.set()
                state = asyncio.create_task(self._state_loop())
                await self._stop.wait()
                state.cancel()
        except OSError as exc:
            self.error = f"no se pudo abrir el puerto: {exc}"
            self.ready.set()

    def _http(self, connection, request):
        path = request.path.split("?", 1)[0]
        if path == "/ws":
            return None                                   # sigue el saludo WebSocket
        if path in ("", "/"):
            path = "/index.html"
        if path.startswith("/qr"):
            return self._qr_page(connection, path)
        rel = path.lstrip("/")
        for root in (WEB_DIR, self.scene_dir):
            file = (root / rel).resolve()
            if file.is_relative_to(root.resolve()) and file.is_file():
                body = file.read_bytes()
                ctype = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
                if file.suffix == ".js":
                    ctype = "text/javascript"
                headers = Headers([("Content-Type", ctype), ("Content-Length", str(len(body))),
                                   ("Cache-Control", "no-cache")])
                return Response(200, "OK", headers, body)
        return connection.respond(404, "No encontrado\n")

    @staticmethod
    def _qr_page(connection, path: str):
        """Los QR de prueba, para abrirlos en el celular y enseñárselos a la webcam del check-in."""
        name = path.rsplit("/", 1)[-1]
        if name in QR_LABELS and (QR_DIR / name).is_file():
            body = (QR_DIR / name).read_bytes()
            return Response(200, "OK", Headers([("Content-Type", "image/png"), ("Content-Length", str(len(body)))]),
                            body)
        if name not in ("", "qr"):
            return connection.respond(404, "No encontrado\n")
        items = "".join(f'<a href="/qr/{f}"><figure><img src="/qr/{f}"><figcaption>{label}</figcaption></figure></a>'
                        for f, label in QR_LABELS.items() if (QR_DIR / f).is_file())
        body = ("<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>"
                "<title>QR de prueba</title><style>body{font-family:sans-serif;background:#fff;margin:16px}"
                "figure{margin:0 0 24px;text-align:center}img{width:min(80vw,360px);image-rendering:pixelated}"
                "a{color:#000;text-decoration:none}</style><h1>Credenciales QR de prueba</h1>"
                "<p>Toca una para verla en grande y muéstrala a la webcam del PC.</p>" + items).encode()
        return Response(200, "OK", Headers([("Content-Type", "text/html; charset=utf-8"),
                                            ("Content-Length", str(len(body)))]), body)

    def _snapshot(self) -> bytes:
        eng = self.engine
        with eng.lock:
            d = eng.data
            guest = eng.guest
            head = [d.time, guest.generation, guest.x, guest.y, guest.yaw]
            doors = [G.Occupancy._aabb(eng.model, d, g)[[0, 1, 3, 4]] for g in self._door_geoms]
            robots = [d.qpos[r.qbase:r.qbase + 2] for r in self._rigs]
            poses = np.hstack([d.xpos[self._bodies], d.xquat[self._bodies]])
            generation = guest.generation
            start = (guest.x, guest.y, guest.yaw)
        if generation != self._generation:
            self._generation = generation
            broadcast(set(self._quest), json.dumps({"t": "teleport", "x": start[0], "y": start[1], "yaw": start[2]}))
        floats = np.concatenate([head] + doors + robots + [poses.ravel()]).astype(np.float32)
        return bytes([MSG_STATE, 0, 0, 0]) + floats.tobytes()

    async def _state_loop(self) -> None:
        period = 1.0 / STATE_HZ
        next_t = time.perf_counter()
        while True:
            if self._quest:
                broadcast(set(self._quest), self._snapshot())
            next_t += period
            await asyncio.sleep(max(0.0, next_t - time.perf_counter()))

    async def _quest_handler(self, ws) -> None:
        self._quest.add(ws)
        self.events.put(("connected", len(self._quest)))
        self._send_api({"t": "quest", "connected": len(self._quest)})
        g = self.engine.guest
        await ws.send(json.dumps({"t": "teleport", "x": g.x, "y": g.y, "yaw": g.yaw}))
        if self._last_status:
            await ws.send(json.dumps({"t": "status", "text": self._last_status}))
        try:
            async for msg in ws:
                if isinstance(msg, bytes):
                    if len(msg) > 4 and msg[0] == MSG_MIC:
                        self._mic(msg[1] != 0, msg[4:])
                    continue
                try:
                    m = json.loads(msg)
                except json.JSONDecodeError:
                    continue
                kind = m.get("t")
                if kind == "pose":
                    self._driver = ws
                    with self.engine.lock:
                        self.engine.guest.follow(m["x"], m["y"], m["yaw"])
                elif kind == "ptt":
                    self._set_ptt(bool(m.get("down")))
                elif kind == "reset":
                    self.events.put(("reset", None))
                elif kind == "hello":
                    self.events.put(("info", f"gafas: {m.get('ua', '?')[:80]}"))
        except ConnectionClosed:
            pass
        finally:
            self._quest.discard(ws)
            if self._driver is ws:
                self._driver = None
                with self.engine.lock:
                    self.engine.guest.release()
                self._set_ptt(False)
            self.events.put(("disconnected", len(self._quest)))
            self._send_api({"t": "quest", "connected": len(self._quest)})

    def _mic(self, ptt: bool, pcm: bytes) -> None:
        if self.mic is not None:
            self.mic.write(pcm)
        if ptt != self.ptt:
            self._set_ptt(ptt)
        for listener in self.mic_listeners:
            listener(pcm, ptt)
        if self._api:
            broadcast(set(self._api), pcm)

    def _set_ptt(self, down: bool) -> None:
        if down != self.ptt:
            self.ptt = down
            self._send_api({"t": "ptt", "down": down})
            self.events.put(("ptt", down))

    async def _api_handler(self, ws) -> None:
        self._api.add(ws)
        await ws.send(json.dumps({"t": "hello", "format": "pcm_s16le", "rate": 16000, "channels": 1,
                                  "frame_ms": 20, "quest": len(self._quest), "ptt": self.ptt}))
        try:
            async for msg in ws:
                if isinstance(msg, bytes):
                    self.play_wav(msg, on_pc=True)
                    continue
                try:
                    m = json.loads(msg)
                except json.JSONDecodeError:
                    continue
                if m.get("t") in ("say", "subtitle") and isinstance(m.get("text"), str):
                    self.events.put((m["t"], m["text"]))
        except ConnectionClosed:
            pass
        finally:
            self._api.discard(ws)


def _play_on_pc(wav: bytes) -> None:
    try:
        subprocess.run(["pw-cat", "-p", "-"], input=wav, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=120)
    except (OSError, subprocess.SubprocessError):
        pass
