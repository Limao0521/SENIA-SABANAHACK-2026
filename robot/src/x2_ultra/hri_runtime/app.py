"""Punto de entrada del runtime HRI ONLINE del Club N (T6).

Cadena completa por turno (todo online, sin paquetes precomputados):

    WAV de usuario → ASR (T3, worker en .venv-asr) → FSM (T1) + x2-llm streaming
    (contrato B con intents, T1/llm_client) → TTS por frase (Supertonic según prosodia
    de personas.VOICE; fallbacks espeak-ng/null) → ancla del gesto (estimación
    proporcional del trigger, ctc online) → scheduling de gesto (emote/POINT en el
    instante anclado) → servidor de movimiento online (T4, co-speech 50 Hz) →
    backend (ShowBackend del reto 2 / AimdkBackend real) .

El reloj de simulación lo AVANZA el app (headless determinista): cada frame del
servidor de movimiento se entrega a ``backend.stream_upper_body`` y la simulación
se adelanta hasta su instante; el audio de cada frase se guarda para el mux del
MP4. Métricas de latencia por turno (reloj de pared): fin de habla → texto ASR →
type/primera frase del LLM → primer audio TTS → primer frame de movimiento.
"""
from __future__ import annotations

import json
import os
import re
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np

from . import contract, identity
from .fsm import HriFSM
from .tts import anchor_estimate, make_tts, split_sentences, voice_for

AUDIO_LEAD_S = 0.25          # margen entre gesto/track y arranque de audio
GAP_S = 0.15                 # pausa entre frases
DT = 0.02                    # 50 Hz


def split_first_clause(sentences: list[str], min_chars: int = 10, min_rest: int = 20) -> list[str]:
    """Parte la 1ª frase en su primera coma si quedan dos trozos con sentido."""
    first = sentences[0]
    i = first.find(",", min_chars)
    if i < 0 or len(first) - i - 1 < min_rest:
        return sentences
    return [first[:i + 1].strip(), first[i + 1:].strip()] + list(sentences[1:])


# --------------------------------------------------------------------------- métricas
@dataclass
class TurnMetrics:
    t_end_speech: float = 0.0        # perf_counter del fin de habla del usuario
    t_asr_text: Optional[float] = None
    asr_latency_s: Optional[float] = None
    t_llm_type: Optional[float] = None
    t_llm_first_sentence: Optional[float] = None
    t_tts_first_audio: Optional[float] = None
    t_first_motion_frame: Optional[float] = None
    tts_estimated: bool = False
    text: str = ""
    spoken: list[str] = field(default_factory=list)

    def latencies(self) -> dict:
        base = self.t_end_speech
        out = {"asr_text_s": None, "llm_type_s": None, "llm_first_sentence_s": None,
               "tts_first_audio_s": None, "first_motion_frame_s": None}
        for key, val in (("asr_text_s", self.t_asr_text), ("llm_type_s", self.t_llm_type),
                         ("llm_first_sentence_s", self.t_llm_first_sentence),
                         ("tts_first_audio_s", self.t_tts_first_audio),
                         ("first_motion_frame_s", self.t_first_motion_frame)):
            if val is not None and base:
                out[key] = round(val - base, 3)
        out["tts_estimated"] = self.tts_estimated
        return out


# --------------------------------------------------------------------------- backend nulo
class NullBackend:
    """Backend sin física (solo diálogo): tests offline y modo ``--no-sim``."""

    def __init__(self):
        self.events: list[dict] = []

    def state(self) -> dict:
        return {"t": 0.0, "pelvis_z": 0.75, "tilt_deg": 0.0, "mode": "idle",
                "stable": True, "walking": False, "fall": False}

    def stream_upper_body(self, t, arms14, waist3, head2) -> None:
        pass

    def play_emote(self, motion_id, area, t_dispatch) -> None:
        self.events.append({"event": "emote_dispatch", "motion_id": motion_id, "area": area,
                            "t_dispatch": t_dispatch})

    def point(self, direction, t_stroke, hold_s=1.5) -> None:
        self.events.append({"event": "point_dispatch", "direction": direction, "t_stroke": t_stroke})

    def walk_to(self, area) -> None:
        self.events.append({"event": "walk_start", "area": area})

    def set_velocity(self, vx, wz) -> None:
        pass

    def dispatch_d1(self, area) -> None:
        self.events.append({"event": "dispatch_d1", "area": area, "stub": True})

    def stop(self) -> None:
        pass

    def estop(self) -> None:
        pass

    def close(self) -> None:
        pass


# --------------------------------------------------------------------------- proxy de gestos
class GestureProxy:
    """Envuelve el backend: captura play_emote/point de la FSM (los re-programa anclados
    al audio en ``_speak``) y registra los frames de stream para las comprobaciones de
    seguridad (velocidad por articulación fuera de emotes)."""

    def __init__(self, backend: Any):
        self.backend = backend
        self.pending: list[dict] = []          # {"kind":"emote"|"point", ...}
        self.stream_vmax = 0.0                 # rad/s máx de frames co-speech (no emotes)
        self.stream_frames = 0
        self._last: Optional[np.ndarray] = None

    def __getattr__(self, name):               # state/walk_to/dispatch_d1/stop/estop/...
        return getattr(self.backend, name)

    def play_emote(self, motion_id: int, area: int, t_dispatch: float) -> None:
        self.pending.append({"kind": "emote", "motion_id": int(motion_id), "area": int(area),
                             "t_request": float(t_dispatch)})

    def point(self, direction: str, t_stroke: float, hold_s: float = 1.5) -> None:
        self.pending.append({"kind": "point", "direction": str(direction),
                             "t_request": float(t_stroke), "hold_s": float(hold_s)})

    def stream_upper_body(self, t: float, arms14, waist3, head2) -> None:
        v = np.concatenate([np.asarray(arms14, float).reshape(-1),
                            np.asarray(waist3, float).reshape(-1),
                            np.asarray(head2, float).reshape(-1)])
        if self._last is not None and self._last.shape == v.shape:
            self.stream_vmax = max(self.stream_vmax, float(np.max(np.abs(v - self._last)) / DT))
        self._last = v
        self.stream_frames += 1
        self.backend.stream_upper_body(t, arms14, waist3, head2)


# --------------------------------------------------------------------------- app
class ClubHriApp:
    def __init__(self, backend: Any, *, llm: Any = None, asr: Any = None, motion: Any = None,
                 tts: Any = None, engine: Any = None, kb: Optional[dict] = None,
                 recorder: Any = None, confirm_fn: Optional[Callable[..., dict]] = None,
                 conversation_timeout_s: float = 30.0, owns_io: bool = True,
                 identity_real: Optional[bool] = None,
                 audio_sink: Optional[Callable[[np.ndarray, int, dict], None]] = None,
                 realtime: bool = False):
        # Opt-in para el runner en vivo (live_robot.py); por defecto (None/False) el
        # comportamiento es el de siempre (wavtest/PC): reloj virtual y sin reproducción.
        #   audio_sink(pcm, sr, meta): recibe cada frase sintetizada (p. ej. la encola
        #     hacia los altavoces del robot); no debe bloquear el hilo del app.
        #   realtime=True (solo sin engine): el reloj de la FSM es el de pared
        #     (time.monotonic) y advance()/advance_to() no simulan nada.
        self.audio_sink = audio_sink
        self.realtime = bool(realtime) and engine is None
        # En vivo: la 1ª cláusula de la 1ª frase se sintetiza aparte para que el primer audio
        # salga antes (HRI_TTS_PRIMERA_CLAUSULA=1); por defecto, sin cambios.
        self.first_clause = os.environ.get("HRI_TTS_PRIMERA_CLAUSULA", "0") == "1" and self.realtime
        self._t0_real = time.monotonic()
        # flag de identidad: env HRI_IDENTITY_REAL (default "0") si no se pasa explícito
        self.identity_real = (identity.identity_real_from_env()
                              if identity_real is None else bool(identity_real))
        self.owns_io = owns_io      # False ⇒ asr/motion son compartidos: no cerrarlos en close()
        self.engine = engine                       # ShowEngine (o None ⇒ reloj virtual)
        self.backend_raw = backend
        self.backend = GestureProxy(backend)
        self.asr = asr                             # AsrClient (T3) | Asr | None
        self.motion = motion                       # MotionServer (T4) | None
        self.tts = tts or make_tts()
        # En vivo y opt-in (HRI_TTS_PREFETCH=1): la 1ª frase se pre-sintetiza en cuanto el
        # campo speech se cierra en el stream del LLM, sin esperar al resto del JSON.
        if (os.environ.get("HRI_TTS_PREFETCH", "0") == "1" and self.realtime
                and llm is not None and hasattr(llm, "on_speech")):
            from .tts import TtsPrefetch
            if not isinstance(self.tts, TtsPrefetch):
                self.tts = TtsPrefetch(self.tts)
            llm.on_speech = self._prefetch_speech
            llm.on_speech_partial = self._prefetch_partial
        self.recorder = recorder                   # VideoRecorder | None
        self._vt = 0.0                             # reloj virtual (sin engine)
        self.metrics = TurnMetrics()
        self.turns: list[dict] = []
        self.audio_segments: list[tuple[Path, float]] = []   # (wav, t_sim) para el mux
        self._llm_metrics: list[dict] = []
        if llm is not None and hasattr(llm, "on_attempt"):
            prev = llm.on_attempt
            llm.on_attempt = lambda att: (self._llm_metrics.append(att.get("metrics") or {}),
                                          prev(att) if prev else None)
        elif llm is not None:
            llm.on_attempt = lambda att: self._llm_metrics.append(att.get("metrics") or {})
        self.fsm = HriFSM(self.backend, llm, kb=kb, confirm_fn=confirm_fn,
                          clock=self._clock, conversation_timeout_s=conversation_timeout_s,
                          say=self._say)
        self._speaking = False

    # ------------------------------------------------------------------ reloj / avance
    def _clock(self) -> float:
        if self.engine is not None:
            return float(self.engine.sim_time)
        if self.realtime:
            return time.monotonic() - self._t0_real
        return self._vt

    def advance(self, seconds: float) -> None:
        target = self._clock() + max(0.0, seconds)
        self.advance_to(target)

    def advance_to(self, t_target: float) -> None:
        if self.realtime:          # reloj de pared: el tiempo pasa solo (audio real)
            return
        guard = 0
        while self._clock() < t_target - 1e-9 and guard < 20000:
            guard += 1
            if self.engine is not None:
                self.engine.step_control()
            else:
                self._vt += DT
            if self.recorder is not None:
                self.recorder.maybe_grab(self._clock())

    # ------------------------------------------------------------------ admisión
    def admit(self, qr_image=None, cedula=None, face_result: Optional[dict] = None,
              photo=None) -> dict:
        """Admisión. ``identity_real`` activo ⇒ cara (foto) + QR/confirm real; si la
        confirmación falla devuelve persona REJECTED y la FSM NO saluda como rango
        conocido (sin fallback silencioso). Desactivado ⇒ simulado (passthrough)."""
        self._begin_turn(text=None)
        if self.identity_real:
            person = identity.resolve_identity(
                identity_real=True, face_result=face_result, photo=photo,
                qr_image=qr_image, cedula=cedula)
            if person.get("status") == identity.STATUS_REJECTED:
                self.fsm.log.append({"act": "IDENTITY_REJECTED",
                                     "reason": person.get("reason"), "t": self._clock()})
                rec = self._end_turn(kind="admission", identity_mode=identity.MODE_REAL)
                rec["person"] = person
                return person
            prev = self.fsm.confirm_fn       # ya confirmado: la FSM no re-confirma
            self.fsm.confirm_fn = lambda *a, **k: dict(person)
            try:
                person = self.fsm.arrival(face_result, qr_image, cedula)
            finally:
                self.fsm.confirm_fn = prev
            mode = identity.MODE_REAL
        else:
            person = self.fsm.arrival(face_result, qr_image, cedula)
            mode = identity.MODE_SIMULATED
        rec = self._end_turn(kind="admission", identity_mode=mode)
        rec["person"] = person
        person.setdefault("identity_mode", mode)
        return person

    # ------------------------------------------------------------------ turno de usuario
    def user_turn(self, wav_path: Optional[str] = None, text: Optional[str] = None) -> dict:
        if text is None:
            if self.asr is None:
                raise RuntimeError("sin ASR: pasar text= o configurar asr=")
            t_end = time.perf_counter()          # fin de habla = momento de entregar el WAV
            res = self.asr.transcribe(str(wav_path))
            if not res.get("text") and "error" in res:
                raise RuntimeError(f"ASR: {res['error']}")
            text = (res.get("text") or "").strip()
            self._begin_turn(text)
            self.metrics.t_end_speech = t_end
            self.metrics.t_asr_text = time.perf_counter()
            self.metrics.asr_latency_s = res.get("latency_s")
        else:
            self._begin_turn(text)
        n_llm0 = len(self._llm_metrics)
        acts = self.fsm.on_utterance(text)
        for m in self._llm_metrics[n_llm0:]:
            if self.metrics.t_llm_type is None and m.get("t_type_known") is not None:
                self.metrics.t_llm_type = self.metrics.t_end_speech + m["t_type_known"]
            if self.metrics.t_llm_first_sentence is None and m.get("t_first_sentence") is not None:
                self.metrics.t_llm_first_sentence = self.metrics.t_end_speech + m["t_first_sentence"]
        rec = self._end_turn(kind="user", acts=acts, user_text=text)
        if self.fsm.state.value in ("FAREWELL", "END"):
            self.advance(1.0)
        return rec

    def _begin_turn(self, text: Optional[str]) -> None:
        self.backend.pending.clear()
        self._llm_metrics.clear()
        self.metrics = TurnMetrics(t_end_speech=time.perf_counter(), text=text or "")
        self._speaking = False

    def _end_turn(self, kind: str, acts: Optional[list] = None, **extra) -> dict:
        acts = acts if acts is not None else list(self.fsm.log[-8:])
        rec = {"kind": kind, "acts": acts, "metrics": self.metrics.latencies(),
               "spoken": list(self.metrics.spoken), "state": self.fsm.state.value,
               "t_sim": self._clock(), **extra}
        self.turns.append(rec)
        return rec

    # ------------------------------------------------------------------ habla (say_fn de la FSM)
    def _chunks(self, text: str) -> list[str]:
        sentences = split_sentences(text)
        if self.first_clause and sentences:
            sentences = split_first_clause(sentences)
        return sentences

    _pf_len = 0
    _pf_done = False

    def _prefetch_partial(self, partial: str) -> None:
        """Mientras el LLM aún escribe speech: en cuanto el primer trozo ya no puede cambiar (hay
        un segundo trozo con ≥ 3 palabras, así que ni la unión de fragmentos cortos ni la de la
        cola lo alteran) se pre-sintetiza. Si aun así no coincidiera con el corte final, la caché
        falla y ``_say`` sintetiza como siempre: mismo audio, solo antes."""
        if len(partial) < self._pf_len:        # stream nuevo (otra llamada o reintento)
            self._pf_done = False
        self._pf_len = len(partial)
        if self._pf_done:
            return
        chunks = self._chunks(partial)
        if len(chunks) >= 2 and len(re.findall(r"\w+", chunks[1])) >= 3 and hasattr(self.tts, "prefetch"):
            self._pf_done = True
            rank = (self.fsm.person or {}).get("rank", "GUEST")
            self.tts.prefetch(chunks[0], voice_for(rank))

    def _prefetch_speech(self, text: str) -> None:
        """Pre-síntesis del primer trozo exactamente como lo cortará ``_say``."""
        chunks = self._chunks(text)
        if chunks and hasattr(self.tts, "prefetch"):
            rank = (self.fsm.person or {}).get("rank", "GUEST")
            self.tts.prefetch(chunks[0], voice_for(rank))

    def _say(self, text: str) -> None:
        """TTS por frase + movimiento online + gestos anclados; avanza la simulación."""
        if not text:
            return
        self.metrics.spoken.append(text)
        if self.engine is not None:
            try:
                self.engine.say(text, getattr(self.backend, "key", "ultra"))
            except Exception:
                pass
        rank = (self.fsm.person or {}).get("rank", "GUEST")
        voice = voice_for(rank)
        sentences = self._chunks(text)
        t_audio0 = self._clock() + AUDIO_LEAD_S
        gestures = list(self.backend.pending)
        self.backend.pending.clear()
        # gestos anclados al trigger de la 1ª frase (aprox. proporcional del ancla CTC)
        first_dur = max(0.4, len(sentences[0]) / 13.5) if sentences else 0.0
        trig = self._last_trigger(text)
        anchor = anchor_estimate(sentences[0] if sentences else text, trig, first_dur)
        walking_now = bool(self.backend.state().get("walking"))
        for g in gestures:
            t_g = t_audio0 + anchor
            if walking_now:
                continue                          # interlock: la FSM no debería, pero por seguridad
            if g["kind"] == "emote":
                self.backend.backend.play_emote(g["motion_id"], g["area"], t_g)
            else:
                self.backend.backend.point(g["direction"], t_g, g.get("hold_s", 1.5))
        offset = 0.0
        for i, sent in enumerate(sentences):
            pcm, sr, meta = self.tts.synthesize(sent, voice)
            if i == 0:
                self.metrics.t_tts_first_audio = time.perf_counter()
                self.metrics.tts_estimated = bool(meta.get("estimated"))
            dur = float(len(pcm) / sr) if len(pcm) else float(meta.get("dur_s", 0.5))
            self._save_segment(pcm, sr, t_audio0 + offset)
            if self.audio_sink is not None and len(pcm):
                self.audio_sink(pcm, sr, dict(meta, text=sent, index=i, gap_s=GAP_S))
            t_frame0 = t_audio0 + offset
            if self.motion is not None and not walking_now:
                self._stream_motion(pcm, sr, t_frame0)
                self.advance_to(t_frame0 + dur)
            else:
                self.advance_to(t_frame0 + dur)
            offset += dur + GAP_S
        self.advance(GAP_S)
        self._last_trigger_cache = None

    _last_trigger_cache: Optional[str] = None

    def _last_trigger(self, text: str) -> str:
        """trigger_text del último acto SAY: la FSM registra el objeto completo en su log;
        aquí usamos el primer segmento de la frase (igual que el respaldo _canned)."""
        for rec in reversed(self.fsm.log):
            if rec.get("act") == "SAY" and rec.get("text") == text:
                break
        m = re.match(r"([^,.;:]{3,40})", text.strip("¡!¿?"))
        return (m.group(1) if m else text[:24]).strip()

    def _stream_motion(self, pcm: np.ndarray, sr: int, t_frame0: float) -> None:
        pcm16 = (np.clip(pcm, -1, 1) * 32767).astype(np.int16)
        for t, arms, waist, head in self.motion.start_turn(pcm16, sr, t_frame0):
            if self.metrics.t_first_motion_frame is None:
                self.metrics.t_first_motion_frame = time.perf_counter()
            self.backend.stream_upper_body(t, arms, waist, head)
            if t > self._clock():
                self.advance_to(t)

    def _save_segment(self, pcm: np.ndarray, sr: int, t_sim: float) -> None:
        if self.recorder is None or len(pcm) == 0 or not np.any(pcm):
            return
        d = self.recorder.audio_dir
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"seg_{len(self.audio_segments):03d}.wav"
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sr)
            w.writeframes((np.clip(pcm, -1, 1) * 32767).astype(np.int16).tobytes())
        self.audio_segments.append((path, t_sim))

    # ------------------------------------------------------------------ cierre
    def safety_summary(self) -> dict:
        st = self.backend.state()
        ev = list(getattr(self.backend.backend, "events", []))
        suppressed = sum(1 for e in ev if e.get("event") == "safety_gesture_suppressed")
        falls = sum(1 for e in ev if e.get("event") == "fall") or (1 if st.get("fall") else 0)
        return {"falls": int(falls), "gestures_while_walking": int(suppressed),
                "stream_vmax_rad_s": round(self.backend.stream_vmax, 2),
                "stream_frames": self.backend.stream_frames,
                "velocity_ok_outside_emotes": bool(self.backend.stream_vmax <= 10.0 + 1e-6),
                "tilt_deg": round(float(st.get("tilt_deg", 0.0)), 1)}

    def close(self) -> None:
        for obj in ((self.motion, self.asr) if self.owns_io else ()):
            try:
                if obj is not None:
                    obj.stop()
            except Exception:
                pass
        b = getattr(self, "backend", None)
        if b is not None and hasattr(b.backend, "close"):
            try:
                b.backend.close()
            except Exception:
                pass


# --------------------------------------------------------------------------- vídeo
class VideoRecorder:
    """Render EGL headless del engine a MP4 (25 fps) + subtítulos; mux de audio al final."""

    def __init__(self, engine: Any, out: Path, fps: int = 25, width: int = 960, height: int = 540,
                 camera: str = "Recepción"):
        import mujoco
        from x2_ultra.show import club as C
        self.engine, self.fps = engine, fps
        self.out = Path(out)
        self.audio_dir = self.out.parent / (self.out.stem + "_audio")
        self.out.parent.mkdir(parents=True, exist_ok=True)
        self.renderer = mujoco.Renderer(engine.model, height=height, width=width)
        import imageio
        self.writer = imageio.get_writer(str(self.out.with_suffix(".video.mp4")), fps=fps,
                                         codec="libx264", quality=8, macro_block_size=1)
        self.cam = mujoco.MjvCamera()
        lookat, dist, azim, elev, _c = C.CAMERAS[camera]
        self.cam.lookat[:] = lookat
        self.cam.distance, self.cam.azimuth, self.cam.elevation = dist, azim, elev
        self.steps_per_frame = max(1, int(round(1.0 / (fps * DT))))
        self._speech_seen = 0
        self._subtitle, self._subtitle_until = "", 0.0
        self._frames = 0
        self._t0 = None

    def maybe_grab(self, t: float) -> None:
        if self._t0 is None:
            self._t0 = t
        if int(round((t - self._t0) / DT)) % self.steps_per_frame != 0:
            return
        import numpy as _np
        while self._speech_seen < len(self.engine.speech):
            _, _, text = self.engine.speech[self._speech_seen]
            self._speech_seen += 1
            if text:
                self._subtitle, self._subtitle_until = text, t + max(1.5, 0.09 * len(text))
        if t > self._subtitle_until:
            self._subtitle = ""
        self.renderer.update_scene(self.engine.data, camera=self.cam)
        frame = self.renderer.render()
        self.writer.append_data(frame)
        self._frames += 1

    def finish(self, app: "ClubHriApp") -> Path:
        self.writer.close()
        video = self.out.with_suffix(".video.mp4")
        from x2_ultra.show.hri.render import _mux_audio
        ok = _mux_audio(video, [(p, ts - (self._t0 or 0.0)) for p, ts in app.audio_segments],
                        max(1.0, (app._clock() - (self._t0 or 0.0))), self.out)
        if not ok:
            video.rename(self.out)
        return self.out


# --------------------------------------------------------------------------- memoria
def sample_memory(pids: Optional[list[int]] = None) -> dict:
    """PSS total (MB) de los PIDs + VRAM por proceso (nvidia-smi)."""
    import os
    import subprocess
    pids = pids or [os.getpid()]
    pss = 0.0
    for pid in pids:
        try:
            for line in Path(f"/proc/{pid}/smaps_rollup").read_text().splitlines():
                if line.startswith("Pss:"):
                    pss += float(line.split()[1]) / 1024.0
        except OSError:
            pass
    vram = {}
    try:
        out = subprocess.run(["nvidia-smi", "--query-compute-apps=pid,used_memory",
                              "--format=csv,noheader,nounits"], capture_output=True, text=True,
                             timeout=5).stdout
        for line in out.splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) == 2 and parts[0].isdigit():
                vram[int(parts[0])] = float(parts[1])
    except (OSError, subprocess.SubprocessError):
        pass
    return {"pss_mb": round(pss, 1), "vram_mib_by_pid": vram,
            "vram_mib_total": round(sum(vram.values()), 1)}


def default_routes() -> dict:
    """Rutas walk_to por área (paseo corto desde recepción; el club del show es fijo)."""
    straight = ([(5.0, 2.5), (5.0, 4.5)], 90.0)
    return {sp: straight for sp in contract.load_kb()["espacios"]}
