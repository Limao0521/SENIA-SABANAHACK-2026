"""Recepción del reto 2 con la estructura HRI de Luis pero conversacional: el visitante le dice al robot adónde va.

Estructura (los gestos y la voz son los de ``show.hri``; el reproductor es ``HriPlayer``):

1. Llegada: el visitante de las gafas llega al mostrador; el check-in de Face dice quién es. El robot lo espera en
   su puesto (sin dar ningún paso) y se asienta.
2. Saludo **pregrabado** de su visita (voz Supertonic + gestos SONIC): «Bienvenido, Sebastian. ¿En qué puedo
   ayudarte hoy?».
3. Conversación **en vivo** (``ConversePlayer``): el robot escucha el micrófono de las gafas (``Listener``), Whisper
   transcribe, Qwen responde y decide el destino (``show.conversation.answer``, solo destinos del directorio del
   club) y Supertonic lo dice con la misma voz (``LiveVoice``). Si el visitante pregunta algo, responde y vuelve a
   preguntar; si dice su destino, señala hacia esa ruta (POINT_LEFT/RIGHT/FRONT del catálogo) y lo invita a
   seguirlo; si el destino no corresponde a su categoría, niega con la cabeza (NEGATION_NORMAL) y lo explica.
   Al VIP y a socios e invitados el robot no da el acompañamiento por hecho: tras nombrar el destino pregunta
   «¿Desea que le acompañe?» (o que le acompañe el perro guía D1) y escucha la respuesta
   (``conversation.accepts``). Si no lo quiere, solo le indica hacia dónde ir.
4. Lo que toca según su categoría (``show.guide.ESCORT``) se añade entonces al recorrido: al VIP lo acompaña el
   robot por la ruta del destino elegido; a socios e invitados los acompaña el perro guía D1; al personal le
   señala hacia dónde ir; al trabajador externo le dice que avisará a personal humano del club.
5. Las frases de ese tramo (la indicación final, «Tome el ascensor de la izquierda…») suenan en vivo con la misma
   voz, seguidas de la despedida pregrabada de su visita.

Todo lo lento (Whisper, Qwen por HTTP, Supertonic) va en un hilo aparte; la simulación solo mira si ya terminó.
"""
from __future__ import annotations

import io
import os
import sys
import tempfile
import threading
import time
import wave
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

import numpy as np

from x2_ultra.show import club as C
from x2_ultra.show.guide import Pause, Say, WaitForGuest, escort_steps
from x2_ultra.show.hri.data import TurnData, VisitStore, load_identities, use_person_visits
from x2_ultra.show.hri.llm_script import RANK_CATEGORY
from x2_ultra.show.hri.motion import READY_ARMS
from x2_ultra.show.hri.player import HriPlayer, plan_turn
from x2_ultra.show.hri.tour import HriReceptionTour, HriTurns, Settle
from x2_ultra.vr.hri_visit import UNKNOWN, VoiceGate

LISTEN_TIMEOUT = 12.0       # s de simulación escuchando sin oír nada antes de volver a preguntar
MAX_EXCHANGES = 6           # intercambios antes de acompañarlo al destino de su ficha
REASK = "¿A dónde se dirige hoy?"
NOT_HEARD = "Perdone, no le he oído bien. ¿Me repite a dónde se dirige?"
FAILED = "Disculpe, no he podido entenderle. ¿Me repite a dónde se dirige?"
ACCEPTED, DECLINED = "Con mucho gusto.", "De acuerdo, le indico el camino."
OFFER_TRIES = 2             # sin un sí o un no claro tras repreguntar, se acompaña
LIVE_WAIT = 25.0            # s (reloj de pared) esperando una frase en vivo antes de seguir el recorrido sin ella
ROUTE_OF = {"personal": ("personal", "RIGHT"), "servicio": ("externo", "FRONT")}   # el resto: ascensores (LEFT)


class Listener(VoiceGate):
    """``VoiceGate`` que además guarda lo que oye mientras está armada (con 0,3 s antes de la primera voz)."""
    PREROLL = 15
    MAX_FRAMES = 50 * 15

    def arm(self) -> None:
        super().arm()
        self._frames: deque = deque(maxlen=self.MAX_FRAMES)
        self._first: int | None = None
        self._lock = threading.Lock()

    def feed(self, pcm: bytes, ptt: bool) -> None:
        listening = self.armed and not self.opened
        before = self._speech
        super().feed(pcm, ptt)
        if listening:
            with self._lock:
                self._frames.append(pcm)
                if self._first is None and self._speech > before:
                    self._first = len(self._frames) - 1

    @property
    def heard(self) -> bool:
        return self._speech > 0

    def utterance_wav(self) -> bytes:
        """Lo que dijo el visitante, en WAV 16 kHz mono."""
        with self._lock:
            frames = list(self._frames)
            first = self._first or 0
        pcm = b"".join(frames[max(0, first - self.PREROLL):])
        out = io.BytesIO()
        with wave.open(out, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(pcm)
        return out.getvalue()


class LiveVoice:
    """Whisper → Qwen → Supertonic en un hilo propio, de uno en uno. ``say`` y ``reply`` devuelven un ``Future``."""

    def __init__(self, audio_dir: Path):
        self.audio_dir = Path(audio_dir)
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="vr-voz")
        self._tts = None
        self._n = 0
        self.ready = self._pool.submit(self._warm)

    def _warm(self) -> None:
        repo = str(Path(__file__).resolve().parents[3])        # hriego_v2/ y HRIEGO_gesture/ viven en la raíz
        if repo not in sys.path:
            sys.path.insert(0, repo)
        from hriego_v2.settings import Settings

        from HRIEGO_gesture.tts import GestureTTS
        from x2_ultra.show.conversation import _whisper
        self._tts = GestureTTS(Settings(), self.audio_dir)
        self._tts.synth(speech="Hola.", state="GREETING", turn_id="calentar", visit_id="vivo")
        _whisper()

    def _turn(self, text: str, state: str, emote: str, rank: str) -> TurnData:
        self._n += 1
        turn_id = f"vivo_{self._n:03d}_{state.lower()}"
        r = self._tts.synth(speech=text, state=state, turn_id=turn_id, visit_id="vivo")
        wav = Path(r["wav_path"])
        if not wav.is_absolute():
            from HRIEGO_gesture.paths import ROOT
            wav = Path(ROOT) / wav
        return TurnData(turn_id=turn_id, rank=rank, state=state, emote=emote, speech=text, wav=wav,
                        sample_rate=int(r["sample_rate"]), duration_s=float(r["duration_s"]), anchor_start_s=None,
                        anchor_end_s=None, plan_status="live", delay_s=0.0, sonic=None)

    def say(self, text: str, state: str = "DIALOGUE", emote: str = "NONE", rank: str = "guest") -> Future:
        return self._pool.submit(self._turn, text, state, emote, rank)

    def reply(self, wav: bytes, history: list, category: str, rank: str = "guest", offer: bool = False) -> Future:
        return self._pool.submit(self._reply, wav, list(history), category, rank, offer)

    def confirm(self, wav: bytes, question: str, rank: str = "guest", last: bool = False) -> Future:
        """¿Acepta el acompañamiento ofrecido? {'heard', 'accept' (True/False/None), 'text', 'turn'}. Con ``last``
        una respuesta que no se entiende cuenta como un sí."""
        return self._pool.submit(self._confirm, wav, question, rank, last)

    @staticmethod
    def _transcribe(wav: bytes) -> str:
        from x2_ultra.show.conversation import transcribe
        fd, name = tempfile.mkstemp(prefix="x2-vr-visitante-", suffix=".wav")
        with os.fdopen(fd, "wb") as f:
            f.write(wav)
        return transcribe(Path(name))                   # borra el archivo

    def _confirm(self, wav: bytes, question: str, rank: str, last: bool) -> dict:
        from x2_ultra.show.conversation import accepts
        heard = self._transcribe(wav)
        accept = accepts(heard) if heard else None
        if accept is None and last:
            accept = True
        text = {True: ACCEPTED, False: DECLINED}.get(accept, f"Perdone, no le he entendido. {question}")
        return {"heard": heard, "accept": accept, "text": text, "turn": self._turn(text, "DIALOGUE", "NONE", rank)}

    def _reply(self, wav: bytes, history: list, category: str, rank: str, offer: bool = False) -> dict:
        """{'heard', 'text', 'destination', 'floor', 'turn'} para lo que dijo el visitante."""
        from HRIEGO_gesture.club_visits import CLUB_FACTS
        from x2_ultra.show.conversation import answer, route_floor
        heard = self._transcribe(wav)
        out = {"heard": heard, "destination": None, "floor": None}
        if not heard:
            out.update(text=NOT_HEARD, turn=self._turn(NOT_HEARD, "DIALOGUE", "NONE", rank))
            return out
        try:
            reply = answer(heard, history, category, facts=CLUB_FACTS, offer=offer)
        except Exception:                              # noqa: BLE001 - Qwen caído o lento: el robot lo dice
            out.update(text=FAILED, turn=self._turn(FAILED, "DIALOGUE", "NONE", rank))
            return out
        emote = "NONE"
        if reply.destination:
            try:
                floor = route_floor(reply.destination, category)
                out.update(destination=reply.destination, floor=floor)
                emote = f"POINT_{ROUTE_OF.get(reply.destination, ('', 'LEFT'))[1]}"
            except ValueError as exc:
                reply = type(reply)(f"{exc} {REASK}", None)
                emote = "NEGATION_NORMAL"
        out.update(text=reply.text, turn=self._turn(reply.text, "DIALOGUE", emote, rank))
        return out

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
        if self._tts is not None:
            try:
                self._tts.close()
            except Exception:                          # noqa: BLE001
                pass


class ConversePlayer(HriPlayer):
    """Turnos que se van añadiendo: tras cada intervención del robot escucha al visitante y prepara la respuesta,
    hasta que el visitante da un destino válido (``on_destination``) y el robot lo invita a seguirlo."""

    def __init__(self, key, plans, voice: LiveVoice, listener: Listener, store: VisitStore, category: str,
                 rank: str, on_destination, on_cue=None, speech_by_wav: dict | None = None, **kw):
        super().__init__(key, plans, **kw)
        self.voice, self.listener, self.store = voice, listener, store
        self.category, self.rank = category, rank
        self.on_destination = on_destination
        self.on_cue = on_cue or (lambda text: None)
        self.speech_by_wav = speech_by_wav if speech_by_wav is not None else {}
        self.history: list[dict] = []
        self.phase = "speaking"          # speaking → listening → thinking → speaking … → finished
        self.holding = False
        self.pending: Future | None = None
        self.listen_t0 = 0.0
        self.exchanges = 0
        self.transcripts: list[str] = []
        from x2_ultra.show.conversation import OFFER_QUESTION
        from x2_ultra.show.guide import ESCORT
        self.question = OFFER_QUESTION.get(ESCORT.get(category, ""))     # None: no se ofrece acompañamiento
        self.offered: tuple | None = None        # (destino, piso) a la espera del sí o el no
        self.offer_tries = 0

    def _ending(self, t: float) -> bool:
        return (not np.isnan(self.t0) and self.index == len(self.plans) - 1
                and t - self.plans[self.index].t0 >= self.plans[self.index].end)

    def _listen(self, t: float) -> None:
        self.phase, self.listen_t0 = "listening", t
        self.listener.arm()
        self.status = "escuchando al visitante"
        self.on_cue("Te escucho…")

    def tick(self, t: float, rig) -> bool:
        if self.phase != "finished" and not self.holding and self.standing(rig) and self._ending(t):
            plan = self.plans[self.index]
            self.log("turn_end", t, turn_id=plan.turn.turn_id, emote=plan.turn.emote)
            self.index += 1
            self.holding = True
            self._listen(t)
        if not self.holding:
            return super().tick(t, rig)
        if not self.standing(rig):
            return True
        rig.set_arm_targets(READY_ARMS)              # atento mientras escucha o piensa
        rig.set_waist_delta(np.zeros(3))
        rig.set_head_targets(0.0, 0.0)
        self._last_arms = np.asarray(rig.arm_cmd, dtype=float).copy()
        if self.phase == "listening":
            if self.listener.opened:
                self.listener.armed = False
                self.phase = "thinking"
                self.status = "pensando la respuesta"
                self.on_cue("…")
                if self.offered is not None:
                    self.offer_tries += 1
                    self.pending = self.voice.confirm(self.listener.utterance_wav(), self.question, self.rank,
                                                      last=self.offer_tries >= OFFER_TRIES)
                else:
                    self.pending = self.voice.reply(self.listener.utterance_wav(), self.history, self.category,
                                                    self.rank, offer=self.question is not None)
            elif t - self.listen_t0 > LISTEN_TIMEOUT and not self.listener.heard:
                self.listener.armed = False
                self.phase = "thinking"
                if self.offered is not None:
                    self.offer_tries += 1
                    text = ACCEPTED if self.offer_tries >= OFFER_TRIES else self.question   # sin respuesta: sí
                    self.pending = self.voice.say(text, rank=self.rank)
                else:
                    self.pending = self.voice.say(REASK, rank=self.rank)
        elif self.phase == "thinking" and self.pending is not None and self.pending.done():
            self._answer(t, rig)
        return True

    def _answer(self, t: float, rig) -> None:
        try:
            result = self.pending.result()
        except Exception as exc:                       # noqa: BLE001 - Supertonic caído: se reintenta escuchar
            self.log("live_error", t, error=f"{type(exc).__name__}: {exc}")
            self.pending = None
            self._listen(t)
            return
        self.pending = None
        if isinstance(result, TurnData):              # repregunta tras silencio
            turn = result
            self.on_cue("")
            if self.offered is not None and self.offer_tries >= OFFER_TRIES:
                self.on_destination(*self.offered, True)
                self.phase = "finished"
        elif "accept" in result:                       # respuesta a «¿Desea que le acompañe?»
            turn = result["turn"]
            if result.get("heard"):
                self.transcripts.append(result["heard"])
                self.history.append({"role": "user", "content": result["heard"]})
                self.on_cue(f"Tú: «{result['heard']}»")
            if result["accept"] is not None:
                self.on_destination(*self.offered, result["accept"])
                self.phase = "finished"
        else:
            turn = result["turn"]
            if result.get("heard"):
                self.transcripts.append(result["heard"])
                self.history.append({"role": "user", "content": result["heard"]})
                self.on_cue(f"Tú: «{result['heard']}»")
            self.exchanges += 1
            if result.get("destination"):
                if self.question is not None and turn.speech.rstrip().endswith(self.question):
                    self.offered = (result["destination"], result["floor"])     # ofrecido: falta el sí o el no
                else:
                    self.on_destination(result["destination"], result["floor"])
                    self.phase = "finished"
            elif self.exchanges >= MAX_EXCHANGES:
                self.on_destination(None, None)
                self.phase = "finished"
        self.history.append({"role": "assistant", "content": turn.speech})
        self.log("live_turn", t, speech=turn.speech, emote=turn.emote, heard=getattr(self, "transcripts", [])[-1:])
        self.speech_by_wav[str(turn.wav)] = turn.speech
        self.plans.append(plan_turn(turn, self.store))
        self.holding = False
        if self.phase != "finished":
            self.phase = "speaking"
        self._enter(t, rig)


class LiveTurns(HriTurns):
    """Turnos que se terminan de preparar mientras el robot camina (``futures`` → TurnData) más turnos ya hechos."""

    def __init__(self, futures: list, extra_plans: list, store: VisitStore, audio_fn):
        super().__init__(plans=[], audio_fn=audio_fn, status="llegando al destino")
        self.futures, self.extra_plans, self.store = futures, extra_plans, store
        self.player = None
        self.t_wait: float | None = None


class ConversationTour(HriReceptionTour):
    def __init__(self, *args, converse: ConversePlayer, **kw):
        super().__init__(*args, **kw)
        self.converse = converse
        self.follow_from = 1 << 30
        self._step_player: dict[int, object] = {}

    def update(self, engine, t: float) -> None:
        if self.done or self.index >= len(self.steps):
            return super().update(engine, t)
        step = self.steps[self.index]
        if isinstance(step, LiveTurns) and step.player is None:
            rig = engine.rigs[self.key]
            rig.stop_walking()
            if step.t_wait is None:
                step.t_wait = time.monotonic()
            if not all(f.done() for f in step.futures):
                self.status = "preparando la indicación"
                if time.monotonic() - step.t_wait < LIVE_WAIT:
                    return
                print(f"VR: la voz en vivo no respondió en {LIVE_WAIT:.0f} s; el recorrido sigue sin esa frase",
                      flush=True)
            plans = []
            for f in step.futures:
                if not f.done():
                    continue
                try:
                    turn = f.result()
                    if turn is None:                   # nada que decir (la despedida de un rechazado)
                        continue
                    self.converse.speech_by_wav[str(turn.wav)] = turn.speech
                    plans.append(plan_turn(turn, step.store))
                    print(f"VR: robot [{turn.emote}] «{turn.speech}»", flush=True)
                except Exception as exc:               # noqa: BLE001 - sin voz en vivo: solo la despedida
                    print(f"VR: frase en vivo fallida: {type(exc).__name__}: {exc}", flush=True)
            step.player = HriPlayer(self.key, plans + step.extra_plans, audio_fn=step.audio_fn,
                                    log_event=self.log_event or (lambda **kw: None))
        if isinstance(step, HriTurns):
            wanted = self.converse if not isinstance(step, LiveTurns) else step.player
            if self.player is not wanted:
                self.player = wanted
        super().update(engine, t)


def _person_visit(person: str, category: str) -> tuple[VisitStore, list, list]:
    """Saludo y despedida pregrabados de la visita de ``person``."""
    previous = os.environ.get("HRI_VISITS_ROOT")
    try:
        use_person_visits(person)
        store = VisitStore()
        turns = store.visit_plan(category)
        greeting = [plan_turn(t, store) for t in turns if t.state == "GREETING"]
        farewell = [plan_turn(t, store) for t in turns if t.state == "FAREWELL"]
    finally:
        if previous is None:
            os.environ.pop("HRI_VISITS_ROOT", None)
        else:
            os.environ["HRI_VISITS_ROOT"] = previous
    return store, greeting, farewell


def conversational_reception(person: str, guest, audio_fn, voice: LiveVoice, listener: Listener, on_cue=None,
                             floor: int | None = None, come_back: bool = True,
                             key: str = "ultra") -> tuple[ConversationTour, dict]:
    entry = load_identities()[person]
    category = RANK_CATEGORY.get(entry.get("rank_code") or "", "invitado")
    rank = {"vip": "vip", "socio": "regular", "invitado": "guest", "personal": "staff",
            "externo": "external"}[category]
    default_floor = entry.get("floor") or floor or 2
    store, greeting, farewell = _person_visit(person, category)
    speech_by_wav = {str(p.turn.wav): p.turn.speech for p in greeting + farewell}
    name = entry.get("display_name") or person
    sx, sy, syaw = C.ROBOT_START
    summary = {"person": person, "name": name, "category": category, "speech_by_wav": speech_by_wav,
               "destination": None}

    def on_destination(destination: str | None, dest_floor: int | None, accepted: bool = True) -> None:
        if destination is None:                        # no se entendió: a lo de su ficha
            route_category = category
            dest_floor = default_floor if category in ("vip", "socio", "invitado") else None
        elif destination in ROUTE_OF:
            route_category = ROUTE_OF[destination][0]
        else:
            route_category = category if category in ("vip", "socio", "invitado") else "invitado"
        escort, _ = escort_steps(category, dest_floor, come_back, route=route_category,
                                 mode=None if accepted else "señalar")
        summary["escort"] = "aceptado" if accepted else "rechazado"
        summary["destination"] = destination or "ficha"
        steps, last = [], None
        for step in escort:                            # las frases, en vivo con la voz de la visita
            if isinstance(step, Say) and step.text:
                emote = "POINT_FRONT" if step.pose == "señalar" else "NONE"
                last = LiveTurns([voice.say(step.text, "GUIDANCE", emote, rank)], [], store, audio_fn)
                steps.append(last)
            else:
                steps.append(step)
        if last is not None:
            last.extra_plans = farewell                # la despedida pregrabada, tras la indicación final
        tour.steps.extend(steps)

    converse = ConversePlayer(key, greeting, voice, listener, store, category, rank, on_destination,
                              on_cue=on_cue, speech_by_wav=speech_by_wav, audio_fn=audio_fn)
    steps = [
        WaitForGuest(),
        Pause(1.0, f"reconocimiento facial: {name}" if person != UNKNOWN else "cara no reconocida: invitado"),
        Settle(0.8),                                   # el robot atiende desde su puesto, sin dar un paso
        HriTurns(plans=greeting, audio_fn=audio_fn, status="conversación"),
    ]
    tour = ConversationTour(key, steps, label=f"{name} · conversación", guest=guest, converse=converse)
    summary["speech_by_wav"] = speech_by_wav
    return tour, summary
