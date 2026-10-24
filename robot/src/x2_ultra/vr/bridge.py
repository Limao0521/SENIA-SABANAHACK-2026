"""Une el servidor VR con la ventana del club (``show.app.ShowWindow``).

- Al conectarse unas gafas entra en modo huésped (la escena vuelve al principio) y la ventana pasa a la vista
  «Huésped: detrás», que es la que ve el público en el PC.
- Lo que dice el robot (``engine.speech``) va a las gafas como subtítulo y, si la voz está activa, como audio (se
  sintetiza con espeak-ng aparte; los altavoces del PC siguen con la voz de la ventana).
- **Check-in** (con ``--hri``): la persona se pone delante de la webcam del PC *antes* de ponerse las gafas; Face
  confirma quién es y luego enseña a la misma cámara el QR de su credencial. Con las gafas puestas Face ya no ve la
  cara y avisa «ausente»; mientras haya VR la ventana no borra por eso la identidad (``keep_profile``) ni el QR:
  valen hasta el siguiente check-in o hasta «Nuevo check-in». El robot la saluda por su nombre al llegar.
- **Recepción** (``reception``): ``"hri"`` (por defecto) es la recepción del reto 2 con la máquina de estados HRI
  de Luis (``fsm_visit``: cara + QR verificados, saludo por rango, conversación en vivo con la biblioteca de espacios
  y los accesos por rango, y quien acompaña según ``show.guide.ESCORT``); con ``require_qr=False`` el rango sale
  solo de la cara. ``"hri-qwen"`` es la recepción conversacional anterior (``conversation_visit``: saludo y
  despedida pregrabados y en medio Qwen); ``"guion"`` es su visita pregrabada completa (``hri_visit``: antes de cada
  diálogo espera a que el visitante diga la frase que le muestran las gafas); ``"conversacion"`` deja la
  conversación libre de la ventana (Whisper + Qwen).
- La API local (``ws://127.0.0.1:8766``, ver ``server.py``) puede hacer hablar al robot o mandar audio a las gafas.
"""
from __future__ import annotations

import queue
import shutil
import subprocess
import threading
import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QGroupBox, QLabel, QPushButton, QVBoxLayout

from x2_ultra.vr import conversation_visit, fsm_visit, hri_visit
from x2_ultra.vr.mic import NODE, QuestMic
from x2_ultra.vr.server import VRServer

CERT_DIR = Path.home() / ".local/share/x2_ultra/vr_cert"     # fuera del repo: las gafas recuerdan el certificado
CATEGORY_LABEL = {"vip": "socio VIP", "socio": "socio", "invitado": "invitado", "personal": "personal del club",
                  "externo": "trabajador externo"}


def checkin_text(profile: dict | None, qr: str | None = None) -> str:
    """«Nombre · categoría» del perfil confirmado por Face (y si ya enseñó el QR), o vacío."""
    if not profile:
        return "QR leído, falta la cara" if qr else ""
    from x2_ultra.show.hri import RANK_CATEGORY
    name = profile.get("display_name") or profile.get("person_id") or "visitante"
    category = CATEGORY_LABEL.get(RANK_CATEGORY.get(profile.get("rank_code"), ""), profile.get("rank_name") or "")
    text = f"{name} · {category}" if category else str(name)
    return text + (" · QR leído" if qr else "")


def _espeak_wav(text: str) -> bytes | None:
    if shutil.which("espeak-ng") is None:
        return None
    try:
        voices = subprocess.run(["espeak-ng", "--voices=es"], capture_output=True, text=True, timeout=5).stdout
        voice = "es-419" if "es-419" in voices else "es"
        out = subprocess.run(["espeak-ng", "-v", voice, "-s", "150", "--stdout", text], capture_output=True,
                             timeout=20)
        return out.stdout if out.returncode == 0 and out.stdout[:4] == b"RIFF" else None
    except (OSError, subprocess.SubprocessError):
        return None


class VRBridge:
    def __init__(self, win, out_dir: Path, port: int = 8443, api_port: int = 8766, mic: str = "defecto",
                 record_dir: Path | None = None, cert_dir: Path = CERT_DIR, reception: str = "hri",
                 require_qr: bool = True):
        from x2_ultra.show.app import GUEST_VIEW
        self.win = win
        self.guest_view = GUEST_VIEW
        quest_mic = None if mic == "no" else QuestMic(default=mic == "defecto", record_dir=record_dir)
        self.server = VRServer(win.engine, out_dir, port, api_port, quest_mic, cert_dir)
        self._speech_seen = len(win.engine.speech)
        self._voice_lock = threading.Lock()
        self._announced = False
        win.keep_profile = True
        self.reception = reception
        self.require_qr = require_qr
        live = reception in ("hri", "hri-qwen")
        self.gate = conversation_visit.Listener() if live else hri_visit.VoiceGate()
        self.server.mic_listeners.append(self.gate.feed)
        voice = fsm_visit.FsmVoice if reception == "hri" else conversation_visit.LiveVoice
        self.voice = voice(Path(out_dir) / "voz") if live else None
        self.visit = None                        # (tour, resumen) de la recepción HRI en curso
        self._visit_generation = None
        self._used_qrs: list[str] = []           # QR que ya probó la última recepción: no valen para la siguiente
        self._subtitles: queue.Queue[str] = queue.Queue()
        self._cue = ""
        if reception in ("hri", "hri-qwen", "guion"):
            win.conversation_enabled = False
        self._panel()
        self.server.start()
        self.timer = QTimer(win)
        self.timer.timeout.connect(self.poll)
        self.timer.start(30)

    # ------------------------------------------------------------------------------------ panel
    def _panel(self) -> None:
        box = QGroupBox("Gafas VR (Quest 3)")
        v = QVBoxLayout(box)
        self.label = QLabel("Preparando la escena para las gafas…")
        self.label.setWordWrap(True)
        self.label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        v.addWidget(self.label)
        self.checkin_label = QLabel("")
        self.checkin_label.setWordWrap(True)
        v.addWidget(self.checkin_label)
        if self.win.hri_enabled:
            b = QPushButton("Nuevo check-in")
            b.clicked.connect(self.new_checkin)
            v.addWidget(b)
        if self.reception in ("hri", "hri-qwen", "guion"):
            self.visit_label = QLabel("Recepción HRI: empieza cuando el visitante llegue al mostrador")
            self.visit_label.setWordWrap(True)
            v.addWidget(self.visit_label)
            b = QPushButton("El visitante ya habló ▶")
            b.clicked.connect(self.gate.force)
            v.addWidget(b)
        b = QPushButton("Reiniciar visita")
        b.clicked.connect(self.restart_visit)
        v.addWidget(b)
        for w in box.findChildren(QPushButton):
            w.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        lay = self.win.panel.layout()
        lay.insertWidget(1, box)

    def _refresh_label(self) -> None:
        srv = self.server
        if srv.error:
            self.label.setText(f"VR: {srv.error}")
            return
        if not srv.ready.is_set():
            return
        lines = ["Abre en el navegador de las Quest:"] + [f"  {u}" for u in srv.urls]
        lines.append(f"Gafas conectadas: {srv.connected}" + (" · gatillo" if srv.ptt else ""))
        if srv.mic is not None and srv.mic.available:
            lines.append(f"Micro Quest en el PC: nodo {NODE} · nivel {srv.mic.level:.2f}")
        lines.append(f"API local: ws://127.0.0.1:{srv.api_port}")
        self.label.setText("\n".join(lines))

    # ------------------------------------------------------------------------------------ acciones
    def new_checkin(self) -> None:
        self.win._hri_profile = None
        self.win._hri_qr = None
        self.win.flash("Check-in: que la persona mire a la cámara del PC sin las gafas y enseñe su QR", 5.0)

    def restart_visit(self) -> None:
        win = self.win
        self.visit = None
        if win.guest_mode:
            win.toggle_guest_mode()            # sale (para la visita) ...
        win.toggle_guest_mode()                # ... y entra de nuevo: escena al principio
        win.set_view(self.guest_view)

    # ------------------------------------------------------------------------------------ ciclo
    def poll(self) -> None:
        win, srv = self.win, self.server
        if srv.ready.is_set() and not self._announced:
            self._announced = True
            if srv.error:
                win.flash(f"VR: {srv.error}", 10.0)
            else:
                print("VR: abre en las Quest " + " o ".join(srv.urls), flush=True)
        while True:
            try:
                kind, value = srv.events.get_nowait()
            except Exception:                  # noqa: BLE001 - cola vacía
                break
            if kind == "connected":
                if not win.guest_mode:
                    win.toggle_guest_mode()
                win.set_view(self.guest_view)
                win.flash("Gafas conectadas: el visitante entra al club", 4.0)
            elif kind == "disconnected":
                win.flash("Gafas desconectadas", 3.0)
            elif kind == "reset":
                self.restart_visit()
                win.flash("Visita reiniciada desde las gafas", 3.0)
            elif kind == "say":
                win.engine.say(value, "ultra")
            elif kind == "subtitle":
                win.viewport.subtitle = value
                srv.subtitle(value)
            elif kind == "info":
                print(f"VR: {value}", flush=True)
        if self.reception in ("hri", "hri-qwen", "guion"):
            self._poll_visit()
        speech = win.engine.speech
        while self._speech_seen < len(speech):
            _, _, text = speech[self._speech_seen]
            self._speech_seen += 1
            srv.subtitle(text)
            voice = getattr(win.panel, "voice", None)
            if text and srv.connected and (voice is None or voice.isChecked()):
                threading.Thread(target=self._voice, args=(text,), daemon=True).start()
        profile = getattr(win, "_hri_profile", None)
        qr = getattr(win, "_hri_qr", None) if self.reception == "hri" and self.require_qr else None
        who = checkin_text(profile, qr)
        if win.hri_enabled:
            pending = ("Check-in pendiente: la persona mira a la webcam del PC sin las gafas"
                       + (" y enseña el QR de su credencial" if self.reception == "hri" and self.require_qr else ""))
            self.checkin_label.setText(f"Check-in: {who}" if who else pending)
        tour = win.engine.tour
        status = f"Check-in: {who}" if who else ("Sin check-in" if win.hri_enabled else "")
        if tour is not None and not tour.done and tour.status:
            status = (status + " · " if status else "") + tour.label
        srv.status(status)
        self._refresh_label()

    # ------------------------------------------------------------------------------------ recepción HRI
    def _poll_visit(self) -> None:
        win, eng = self.win, self.win.engine
        if not win.guest_mode or eng.guest is None:
            return
        if self.visit is not None and self._visit_generation != eng.guest.generation:
            self.visit = None                    # la escena volvió al principio: visita nueva
        if self.visit is None and hri_visit.guest_near(eng) and (eng.tour is None or eng.tour.done):
            person = hri_visit.person_for(getattr(win, "_hri_profile", None))
            if getattr(win, "_hri_qr", None) in self._used_qrs:
                win._hri_qr = None               # si la credencial sigue a la vista, Face la reenvía (QR_REPEAT_S)
            try:
                if self.reception == "hri":
                    tour, summary = fsm_visit.fsm_reception(
                        person, eng.guest, self._play_turn, self.voice, self.gate, on_cue=self._wait_visitor,
                        qr=lambda: getattr(win, "_hri_qr", None), require_qr=self.require_qr,
                        come_back=win.panel.come_back.isChecked())
                elif self.reception == "hri-qwen":
                    tour, summary = conversation_visit.conversational_reception(
                        person, eng.guest, self._play_turn, self.voice, self.gate, on_cue=self._wait_visitor,
                        floor=win.panel.destination.currentData(), come_back=win.panel.come_back.isChecked())
                else:
                    tour, summary = hri_visit.vr_reception(
                        person, eng.guest, self._play_turn, self.gate, on_wait=self._guion_cue,
                        floor=win.panel.destination.currentData(), come_back=win.panel.come_back.isChecked())
            except Exception as exc:             # noqa: BLE001 - datos HRI incompletos: se informa
                win.flash(f"Recepción HRI: {type(exc).__name__}: {exc}", 8.0)
                self.visit = (None, {})
                self._visit_generation = eng.guest.generation
                return
            eng.start_tour(tour)
            self.visit = (tour, summary)
            self._used_qrs = getattr(tour.converse, "tried", [])
            self._visit_generation = eng.guest.generation
            self._speech_by_wav = summary["speech_by_wav"]
            win.flash(f"Recepción HRI de {summary['name']} ({summary['category']})", 4.0)
            ident = summary.get("identity") or {}
            extra = f", identidad {ident.get('status')} {ident.get('reason') or ident.get('source') or ''}" if ident else ""
            print(f"VR: recepción HRI de {person} ({summary['category']}, {self.reception}{extra})", flush=True)
        while True:
            try:
                text = self._subtitles.get_nowait()
            except queue.Empty:
                break
            win.viewport.subtitle = text
            win._subtitle_until = time.time() + max(3.0, 0.075 * len(text))
            self.server.subtitle(text)
        if self.visit is not None and self.visit[0] is not None:
            tour, summary = self.visit
            cue = f"\n{self._cue}" if self._cue else ""
            dest = f" · destino: {summary['destination']}" if summary.get("destination") else ""
            self.visit_label.setText(f"Recepción HRI: {summary['name']}{dest} · {tour.status}{cue}")

    def _play_turn(self, wav: Path) -> float:
        """Audio de un turno HRI (hilo de la simulación): suena en el PC y en las gafas, con su subtítulo."""
        player = shutil.which("pw-play") or shutil.which("aplay")
        if player:
            subprocess.Popen([player, str(wav)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        try:
            self.server.play_wav(Path(wav).read_bytes())
        except OSError:
            pass
        text = getattr(self, "_speech_by_wav", {}).get(str(wav))
        if text:
            self._subtitles.put(text)
        return time.monotonic()

    def _wait_visitor(self, text: str) -> None:
        """Lo que las gafas muestran al visitante cuando el robot calla (vacío: nada). Hilo de la simulación."""
        self._cue = text
        self.server.cue(text)

    def _guion_cue(self, text: str) -> None:
        self._wait_visitor(f"Tu turno, di: «{text}»" if text else "")

    def _voice(self, text: str) -> None:
        with self._voice_lock:
            wav = _espeak_wav(text)
        if wav:
            self.server.play_wav(wav)

    def close(self) -> None:
        self.timer.stop()
        self.server.stop()
        if self.voice is not None:
            self.voice.close()
