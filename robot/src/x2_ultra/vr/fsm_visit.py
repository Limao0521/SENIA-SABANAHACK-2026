"""Recepción del reto 2 en VR con la máquina de estados HRI de Luis (``hri_runtime.fsm``).

1. **Check-in** en la webcam del PC, antes de ponerse las gafas: Face (UniFace) reconoce la cara.
   **Credencial**: al llegar al mostrador el robot le pide de viva voz el QR de su credencial («muéstreme el código
   QR…») y espera a que la misma webcam lo lea (``hriego_v2.show_face_stream``; se puede enseñar levantando un poco
   las gafas, o ya en el check-in). ``resolve_visitor`` lo verifica con ``hri_runtime.confirm``: el QR tiene que
   ser válido y de esa misma persona; si no, pide otro una vez. El trabajador externo no trae QR: se le lee la cédula
   (simulada) y queda como WORKER. Si algo no cuadra (cara desconocida, ningún QR en ``QR_TIMEOUT``, QR de otra
   persona) el visitante queda **rechazado**: el robot no lo saluda por un rango y avisa a personal humano.
2. **Saludo**: al llegar al mostrador la FSM (``ClubFSM``) saluda según el rango con el LLM local (contrato B: frase y
   gesto del catálogo) y Supertonic lo dice. El robot atiende desde su puesto, sin dar un paso.
3. **Conversación**: micrófono de las gafas → Whisper → ``fsm.on_utterance``. La FSM decide el acto con la biblioteca
   de espacios del club (``hri_runtime/club_kb.json``) y la tabla de accesos (``hri_runtime.access``): responde
   preguntas sin guiar, niega con la cabeza lo que su rango no permite (y propone una alternativa), ofrece
   acompañamiento o se despide.
4. **Quién acompaña** (``show.guide.ESCORT``): al VIP, el robot (si acepta); a socios e invitados, el perro D1 (si
   acepta). Si no lo quieren, o es personal del club, el robot señala hacia la ruta de ese espacio en el club 3D
   (``show.spaces``). Al trabajador externo (y al rechazado) se le dice que se avisará a personal humano.
5. **Despedida** de la FSM: tras señalar, o al final del recorrido si hay acompañamiento.

Todo lo lento (Whisper, el LLM, Supertonic) va en el hilo de ``FsmVoice``; la simulación solo mira si ya terminó.
"""
from __future__ import annotations

from concurrent.futures import Future
from dataclasses import dataclass, field

import re
import unicodedata

import numpy as np

from x2_ultra.hri_runtime import access, contract, identity, intents
from x2_ultra.hri_runtime.fsm import HriFSM, State
from x2_ultra.show import spaces
from x2_ultra.show.conversation import OFFER_QUESTION, accepts
from x2_ultra.show.guide import Pause, Say, WaitForGuest, escort_steps
from x2_ultra.show.hri.data import TurnData, VisitStore, load_identities
from x2_ultra.show.hri.motion import READY_ARMS
from x2_ultra.show.hri.player import HriPlayer, plan_turn
from x2_ultra.show.hri.tour import HriTurns, Settle
from x2_ultra.vr.conversation_visit import ConversationTour, Listener, LiveTurns, LiveVoice
from x2_ultra.vr.hri_visit import UNKNOWN

RANK_CATEGORY = {"VIP": "vip", "REGULAR": "socio", "GUEST": "invitado", "STAFF": "personal", "WORKER": "externo"}
TURN_RANK = {"VIP": "vip", "REGULAR": "regular", "GUEST": "guest", "STAFF": "staff", "WORKER": "external"}
EMOTE_ALIAS = {"GREETING_WORKER": "GREETING_EXTERNAL"}       # el nombre del gesto en nuestro catálogo
LISTEN_TIMEOUT = 12.0       # s de simulación escuchando sin oír nada antes de volver a preguntar
MAX_SILENCES = 2            # repreguntas sin respuesta antes de despedirse
MAX_EXCHANGES = 8           # intercambios antes de despedirse
REASK = "¿En qué más puedo ayudarle?"
NOT_HEARD = "Perdone, no le he oído bien. ¿Me lo repite, por favor?"
FAILED = "Disculpe, no le he entendido. ¿Me lo repite, por favor?"
# Frases con la voz del club (``club_kb.json`` → ``identidad``): nadie se siente fuera, ni quien aún no se confirma.
REJECTED = "Queremos recibirle bien: un compañero de nuestro equipo le ayuda ahora a completar su registro."
ASK_QR = "Hola, {name}, soy Alfred. Para recibirle como se merece en nuestro club, ¿me enseña el código QR de su credencial?"
REMIND_QR = "Tómese su tiempo. Cuando lo tenga, muestre el código QR de su credencial a la cámara."
RETRY_QR = "Ese código parece de otra credencial. ¿Me enseña el suyo, por favor? Lo resolvemos juntos."
QR_REMIND, QR_TIMEOUT = 20.0, 45.0      # s de simulación esperando el QR: recordatorio y rechazo
QR_TRIES = 2                            # QR leídos (distintos) antes de rechazar
OFFER_TRIES = 2                         # respuestas a la oferta sin un sí o un no claros: luego cuenta como sí
ACCEPTED = {"robot": "Con mucho gusto. Le acompaño hasta {place}.",
            "perro": "Con mucho gusto. Draco, nuestro perro guía y parte del equipo, le acompañará hasta {place}."}
# Quién es quién en la recepción: frase fija (el LLM se presenta con el nombre del visitante).
WHO = {"robot": "Soy Alfred, el anfitrión de nuestro club. A socios e invitados les acompaña Draco, nuestro perro guía.",
       "perro": "Se llama Draco: es nuestro perro guía y parte del equipo. Yo soy Alfred, su anfitrión."}
ASKS_WHO = {"perro": re.compile(r"\b(perr\w*|draco)\b.*\b(llama|nombre)\b|\b(llama|nombre)\b.*\b(perr\w*)\b"),
            "robot": re.compile(r"como te llamas|como se llama usted|quien eres|quien es usted|\btu nombre\b"
                                r"|\bsu nombre\b|como te dicen|eres alfred")}
OFFER = "{name}, {place} queda {where}, en el piso {floor}."   # si el LLM ofrece otro sitio que el pedido
WHERE = {"right": "a mi derecha", "left": "a mi izquierda", "front": "al frente"}


# ------------------------------------------------------------------------------------------- identidad
def resolve_visitor(person: str, qr: str | None = None, require_qr: bool = True) -> dict:
    """Persona confirmada para la FSM a partir de la cara (``person``: clave de ``identities.json``) y el QR.

    Con ``require_qr`` (identidad real) solo pasa quien trae un QR válido de esa misma persona; el trabajador externo
    pasa con la cédula simulada. Sin ``require_qr`` (identidad simulada) basta la ficha de Face, como antes."""
    from x2_ultra.hri_runtime import confirm

    entry = load_identities().get(person) or {}
    known = entry.get("status") == "KNOWN" and bool(entry.get("person_id"))
    name = entry.get("display_name") or person
    if not require_qr:
        if not known:
            return {"person_id": None, "name": "", "rank": "GUEST", "worker_subtype": None, "work_order": None,
                    "status": confirm.STATUS_CONFIRMED, "source": "simulada",
                    "identity_mode": identity.MODE_SIMULATED}
        rank, subtype = access.normalize_rank(entry["rank_code"])
        return {"person_id": entry["person_id"], "name": name, "rank": rank, "worker_subtype": subtype,
                "work_order": None, "status": confirm.STATUS_CONFIRMED, "source": "cara",
                "identity_mode": identity.MODE_SIMULATED}
    if not known:
        return identity.rejected_person("FACE_UNKNOWN")
    try:
        if qr:
            result = confirm.confirm_identity(entry, qr_image=qr)
        elif entry.get("rank_code") == "EXTERNAL":
            result = confirm.confirm_identity(entry, cedula={"name": name, "seed": entry["person_id"]})
        else:
            return identity.rejected_person("NO_QR")
    except Exception as exc:                                   # noqa: BLE001 - sin respaldo silencioso
        return identity.rejected_person("IDENTITY_ERROR", f"{type(exc).__name__}: {exc}")
    if result.get("status") != confirm.STATUS_CONFIRMED:
        return identity.rejected_person(result.get("reason") or "NOT_CONFIRMED", result.get("detail"))
    return {**result, "identity_mode": identity.MODE_REAL}


# ------------------------------------------------------------------------------------------- FSM del club 3D
class _Acts:
    """Backend de la FSM que no mueve nada: el robot lo mueve el tour (los actos quedan en ``fsm.log``)."""

    def __getattr__(self, name):
        return lambda *args, **kw: None


class ClubFSM(HriFSM):
    """La FSM de Luis con la biblioteca de espacios llevada al club 3D (``show.spaces.kb``: las direcciones salen de
    la ruta de cada espacio) y la regla de acompañamiento del club: al trabajador externo no se le señala su orden
    de trabajo, se avisa a personal humano."""

    def __init__(self, llm=None, **kw):
        kw.setdefault("kb", spaces.kb())
        super().__init__(_Acts(), llm, **kw)

    def _destino_de_rol(self) -> None:
        pass

    def _canned(self, state, direction=None, alternatives=None):
        """Frases de respaldo de Luis con el nombre del espacio, no su id (``centro_convenciones``)."""
        names = [spaces.name(a).lower() if a in spaces.spaces() else a for a in alternatives or []]
        return super()._canned(state, direction, names or None)

    def _kb_grounded(self, r: dict, area, text: str = "") -> dict:
        """Si la pregunta nombra un espacio («cuéntame del restaurante»), la ficha de ese espacio: la plantilla de Luis
        listaría todos los espacios permitidos cuando la pregunta suena general. Solo si el visitante lo nombra: el
        área que propone el LLM para «¿qué hay en el club?» suele ser la recepción."""
        named = intents.match_area(text)
        if named not in self.kb.get("espacios", {}) or _mentions(r.get("speech") or "", named):
            return super()._kb_grounded(r, area, text)
        area = named
        info = self.kb["espacios"][area]
        speech = (f"Con gusto: {info['descripcion']} {info['nombre']} queda {WHERE[info['direccion']]}, en el piso "
                  f"{info['piso']}; horario: {str(info['horario']).lower()}.")
        self.log.append({"act": "KB_GROUNDED", "area": area, "t": self._t()})
        return {**r, "speech": speech, "type": "NONE", "trigger_text": ""}

    def _handle_offer_reply(self, text: str) -> None:
        """Sí o no a la oferta de acompañamiento: primero nuestro reconocedor (``conversation.accepts``: «sí, que me
        guíe el perrito», «no, gracias, ya sé llegar»); si no queda claro, el de Luis (LLM + reglas). Si tampoco,
        se repregunta (y a la segunda cuenta como sí) en vez de perder la oferta."""
        accept = accepts(text)
        if accept is not None:
            return super()._handle_offer_reply("Sí, por favor." if accept else "No, gracias.")
        pending, mark = dict(self._pending or {}), len(self.log)
        super()._handle_offer_reply(text)
        if any(e["act"] in ("WALK_WITH", "DISPATCH_D1", "POINT", "END") for e in self.log[mark:]):
            return
        self.offer_tries = getattr(self, "offer_tries", 0) + 1
        self._pending = pending
        if self.offer_tries >= OFFER_TRIES:
            return super()._handle_offer_reply("Sí, por favor.")
        self.state = State.GUIDANCE_OFFER
        mode = "robot" if pending.get("mode") == "walk" else "perro"
        self._say(f"Perdone, no le he entendido. {OFFER_QUESTION[mode]}")

    def _pide_destino(self, area: str) -> None:
        person = self.person or {}
        if spaces.route(area) == "aqui" and access.allowed(area, person["rank"], person.get("worker_subtype")):
            self._say(f"Ya está en {spaces.name(area).lower()}; con gusto le atiendo aquí mismo.")
            return
        super()._pide_destino(area)


@dataclass
class Reply:
    """Lo que el robot dice tras escuchar (o al empezar) y qué pasa después."""
    turns: list = field(default_factory=list)       # TurnData, en orden
    outcome: str | None = None      # None (sigue), "robot", "perro", "señalar", "llamar" o "fin"
    area: str | None = None         # espacio de la KB del acompañamiento o de la indicación
    heard: str = ""


class FsmVoice(LiveVoice):
    """Whisper → ``ClubFSM`` (LLM local) → Supertonic en el hilo de ``LiveVoice``. Una FSM por visita (``begin``)."""

    def __init__(self, audio_dir, llm=None):
        self.llm = llm
        self.fsm: ClubFSM | None = None
        self.exchanges = 0
        self.unheard = 0
        super().__init__(audio_dir)

    def _warm(self) -> None:
        super()._warm()
        if self.llm is None:
            from x2_ultra.hri_runtime.llm_client import HriLLM
            self.llm = HriLLM(kb=spaces.kb())          # las direcciones del club 3D también en el prompt

    def begin(self, visitor: dict) -> Future:
        return self._pool.submit(self._begin, dict(visitor))

    def hear(self, wav: bytes) -> Future:
        return self._pool.submit(self._hear, wav)

    def farewell(self) -> Future:
        return self._pool.submit(self._farewell)

    def farewell_turn(self) -> Future:
        """La despedida de la FSM (un ``TurnData``), para el final del recorrido; ``None`` si no hay FSM (rechazado)."""
        return self._pool.submit(lambda: next(iter(self._farewell().turns), None))

    # ------------------------------------------------------------------- en el hilo de la voz
    def _begin(self, visitor: dict) -> Reply:
        if visitor.get("status") == identity.STATUS_REJECTED:
            self.fsm = None
            return Reply([self._turn(REJECTED, "GREETING", "NONE", "guest")], "llamar")
        self.fsm = ClubFSM(self.llm, confirm_fn=lambda *_: dict(visitor))
        self.exchanges = self.unheard = 0
        mark = len(self.fsm.log)
        self.fsm.arrival({"name": visitor.get("name")})
        reply = self._collect(mark)
        if self.fsm.person["rank"] == "WORKER":
            reply.outcome = "llamar"
        return reply

    def _hear(self, wav: bytes) -> Reply:
        heard = self._transcribe(wav)
        if not heard:
            self.unheard += 1
            if self.unheard > MAX_SILENCES:            # ruido o nadie habla: se despide
                return self._farewell()
            return Reply([self._turn(NOT_HEARD, "DIALOGUE", "NONE", self._rank())])
        self.unheard = 0
        who = next((k for k, pat in ASKS_WHO.items() if pat.search(_fold(heard))), None)
        if who:                                        # «¿cómo te llamas?»: no pasa por la FSM, sigue donde estaba
            self.exchanges += 1
            reply = Reply([self._turn(WHO[who], "DIALOGUE", "NONE", self._rank())])
            reply.heard = heard
            return reply
        pending = dict(self.fsm._pending or {})
        mark = len(self.fsm.log)
        self.fsm.on_utterance(heard)
        reply = self._collect(mark, pending)
        reply.heard = heard
        self.exchanges += 1
        if reply.outcome == "señalar" or (reply.outcome is None and self.exchanges >= MAX_EXCHANGES):
            reply.turns += self._farewell().turns      # indicado el camino (o ya basta): se despide
            reply.outcome = reply.outcome or "fin"
        if not reply.turns:
            reply.turns = [self._turn(FAILED, "DIALOGUE", "NONE", self._rank())]
        return reply

    def _farewell(self) -> Reply:
        if self.fsm is None or self.fsm.state == State.END:
            return Reply()
        mark = len(self.fsm.log)
        self.fsm._farewell()
        reply = self._collect(mark)
        reply.outcome = "fin"
        return reply

    def _rank(self) -> str:
        return TURN_RANK[self.fsm.person["rank"]] if self.fsm is not None and self.fsm.person else "guest"

    def _collect(self, mark: int, pending: dict | None = None) -> Reply:
        """Las frases (con su gesto) y el desenlace de lo que la FSM hizo desde ``mark``."""
        fsm = self.fsm
        entries = fsm.log[mark:]
        acts = [e["act"] for e in entries]
        reply = Reply()
        if "END" in acts:
            reply.outcome = "fin"
        elif "WALK_WITH" in acts or "DISPATCH_D1" in acts:
            reply.outcome = "robot" if "WALK_WITH" in acts else "perro"
            reply.area = next(e["area"] for e in entries if e["act"] in ("WALK_WITH", "DISPATCH_D1"))
        elif "POINT" in acts:
            reply.outcome = "señalar"
            intent = next((e for e in entries if e["act"] == "INTENT"), {})
            reply.area = (pending or {}).get("area") or intent.get("area")
        said, gesture = [], None
        for e in entries:
            if e["act"] == "EMOTE":
                gesture = e["type"]
            elif e["act"] == "POINT":
                gesture = contract.DIRECTION_TO_TYPE[e["direction"]]
            elif e["act"] == "SAY":
                said.append([e["text"], EMOTE_ALIAS.get(gesture, gesture or "NONE")])
                gesture = None
        motto = re.compile(r"\s*" + re.escape(spaces.identity().get("lema", "")).rstrip("\\.") + r"[.;:]?\s*", re.I)
        for item in said:                              # el lema, solo al saludar y al despedirse
            if not item[1].startswith(("GREETING_", "FAREWELL", "SOCIAL_HEART")):
                item[0] = motto.sub(" ", item[0]).strip() or item[0]
        if reply.outcome in ACCEPTED:                  # el sí: frase fija con el destino real (el LLM lo confunde)
            said = [[ACCEPTED[reply.outcome].format(place=spaces.name(reply.area).lower()), "NONE"]]
        if fsm.state == State.GUIDANCE_OFFER and said:
            offered = (fsm._pending or {}).get("area")
            mode = "robot" if (fsm._pending or {}).get("mode") == "walk" else "perro"
            if offered and not _mentions(said[-1][0], offered):
                said[-1][0] = OFFER.format(name=fsm.person.get("name") or "Con gusto",
                                           place=spaces.name(offered).lower(),
                                           where=WHERE[spaces.direction(offered)], floor=spaces.floor(offered))
            if "?" not in said[-1][0]:
                said[-1][0] = f"{said[-1][0].rstrip()} {OFFER_QUESTION[mode]}"
        rank = self._rank()
        for text, emote in said:
            if emote.startswith("GREETING_"):
                state = "GREETING"
            elif emote in ("FAREWELL_WAVE", "SOCIAL_HEART") and reply.outcome == "fin":
                state = "FAREWELL"
            elif emote.startswith("POINT_"):
                state = "GUIDANCE"
            else:
                state = "DIALOGUE"
            reply.turns.append(self._turn(text, state, emote, rank))
        return reply


def _fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn").casefold()


def _mentions(text: str, space: str) -> bool:
    """¿Nombra la frase el espacio ``space``? Basta la raíz (6 letras) de una palabra larga de su nombre:
    restaurante(s), habitación/habitaciones."""
    words = set(re.findall(r"\w{6,}", _fold(f"{spaces.name(space)} {spaces.SHORT.get(space, '')}"))) - {"centro"}
    folded = _fold(text)
    return any(w[:6] in folded for w in words)


# ------------------------------------------------------------------------------------------- reproductor
class FsmPlayer(HriPlayer):
    """Turnos en vivo: lo que la FSM dice al empezar y tras cada intervención del visitante, que escucha entre turno
    y turno, hasta un desenlace (``on_outcome``) o la despedida."""

    def __init__(self, key, voice: FsmVoice, listener: Listener, store: VisitStore, person: str, on_outcome,
                 on_identity=None, qr_fn=None, require_qr: bool = True, on_cue=None,
                 speech_by_wav: dict | None = None, **kw):
        super().__init__(key, [], **kw)
        self.voice, self.listener, self.store = voice, listener, store
        self.person = person
        self.qr_fn = qr_fn or (lambda: None)
        self.require_qr = require_qr
        entry = load_identities().get(person) or {}
        self.name = entry.get("display_name") or ""
        self.needs_qr = (require_qr and entry.get("status") == "KNOWN" and entry.get("rank_code") != "EXTERNAL")
        self.visitor: dict | None = None
        self.tried: list[str] = []          # QR ya probados
        self.after = "listen"               # qué hacer al terminar de hablar: "listen" o "qr" (esperar el QR)
        self.qr_t0, self.reminded = 0.0, False
        self.on_identity = on_identity or (lambda visitor: None)
        self.on_outcome = on_outcome
        self.on_cue = on_cue or (lambda text: None)
        self.speech_by_wav = speech_by_wav if speech_by_wav is not None else {}
        self.phase = "thinking"          # thinking → speaking → listening → thinking … → finished
        self.holding = True
        self.pending: Future | None = None
        self.listen_t0 = 0.0
        self.silences = 0
        self.transcripts: list[str] = []
        self.outcomes: list[tuple] = []

    def _ending(self, t: float) -> bool:
        return (not np.isnan(self.t0) and self.index == len(self.plans) - 1
                and t - self.plans[self.index].t0 >= self.plans[self.index].end)

    def _listen(self, t: float) -> None:
        self.phase, self.listen_t0 = "listening", t
        self.listener.arm()
        self.status = "escuchando al visitante"
        self.on_cue("Te escucho…")

    def tick(self, t: float, rig) -> bool:
        if not self.holding and self.phase != "finished" and self.standing(rig) and self._ending(t):
            plan = self.plans[self.index]
            self.log("turn_end", t, turn_id=plan.turn.turn_id, emote=plan.turn.emote)
            self.index += 1
            self.holding = True
            if self.after == "qr":
                self._wait_qr(t)
            else:
                self._listen(t)
        if not self.holding:
            return super().tick(t, rig)
        if not self.standing(rig):
            return True
        if np.isnan(self.t0):
            self.t0 = t
            self.log("hri_sequence_start", t, turn_count=0)
            self.status = "preparando el saludo"
            qr = self.qr_fn()
            if self.needs_qr and not qr:              # pide la credencial de viva voz
                self._say(ASK_QR.format(name=self.name).replace("Hola, .", "Hola."), "qr")
            else:
                self._identify(qr)
        rig.set_arm_targets(READY_ARMS)              # atento mientras escucha o piensa
        rig.set_waist_delta(np.zeros(3))
        rig.set_head_targets(0.0, 0.0)
        self._last_arms = np.asarray(rig.arm_cmd, dtype=float).copy()
        if self.phase == "qr":
            qr = self.qr_fn()
            if qr and qr not in self.tried:
                self._identify(qr)
            elif t - self.qr_t0 > QR_TIMEOUT:
                self.log("qr_timeout", t)
                self._identify(None)
            elif t - self.qr_t0 > QR_REMIND and not self.reminded:
                self.reminded = True
                self._say(REMIND_QR, "qr")
        elif self.phase == "listening":
            if self.listener.opened:
                self.listener.armed = False
                self.phase = "thinking"
                self.status = "pensando la respuesta"
                self.on_cue("…")
                self.pending = self.voice.hear(self.listener.utterance_wav())
            elif t - self.listen_t0 > LISTEN_TIMEOUT and not self.listener.heard:
                self.listener.armed = False
                self.phase = "thinking"
                self.silences += 1
                self.pending = (self.voice.say(REASK, rank=self.voice._rank()) if self.silences <= MAX_SILENCES
                                else self.voice.farewell())
        elif self.phase == "thinking" and self.pending is not None and self.pending.done():
            self._answer(t, rig)
        return True

    def _say(self, text: str, after: str) -> None:
        self.phase, self.after = "thinking", after
        self.pending = self.voice.say(text, "GREETING", "NONE", "guest")

    def _wait_qr(self, t: float) -> None:
        self.phase, self.qr_t0 = "qr", t
        self.status = "esperando el QR de la credencial"
        self.on_cue("Muestra el código QR de tu credencial a la cámara del PC")

    def _identify(self, qr: str | None) -> None:
        """Verifica la cara con ``qr`` y empieza la FSM (o pide otro QR si no corresponde)."""
        if qr:
            self.tried.append(qr)
        visitor = resolve_visitor(self.person, qr, self.require_qr)
        self.log("identity", self.t0, status=visitor.get("status"), reason=visitor.get("reason"),
                 source=visitor.get("source"))
        print(f"VR: identidad de {self.person}: {visitor.get('status')} {visitor.get('rank') or ''} "
              f"({visitor.get('source') or visitor.get('reason')})", flush=True)
        rejected = visitor.get("status") == identity.STATUS_REJECTED
        if rejected and qr and self.needs_qr and len(self.tried) < QR_TRIES:
            self._say(RETRY_QR, "qr")
            return
        self.visitor = visitor
        self.on_identity(visitor)
        self.on_cue("")
        self.phase, self.after = "thinking", "listen"
        self.pending = self.voice.begin(visitor)

    def _answer(self, t: float, rig) -> None:
        try:
            result = self.pending.result()
        except Exception as exc:                       # noqa: BLE001 - LLM o Supertonic caídos: se vuelve a escuchar
            self.log("live_error", t, error=f"{type(exc).__name__}: {exc}")
            print(f"VR: error de la voz en vivo: {type(exc).__name__}: {exc}", flush=True)
            self.pending = None
            if not self.plans:                         # ni siquiera hubo saludo: no hay nada que hacer
                self.phase, self.done = "finished", True
                self.status = f"recepción interrumpida: {type(exc).__name__}"
                return
            self._listen(t)
            return
        self.pending = None
        reply = Reply([result]) if isinstance(result, TurnData) else result
        if reply.heard:
            print(f"VR: visitante «{reply.heard}»", flush=True)
            self.silences = 0
            self.transcripts.append(reply.heard)
            self.on_cue(f"Tú: «{reply.heard}»")
        else:
            self.on_cue("")
        for turn in reply.turns:
            print(f"VR: robot [{turn.emote}] «{turn.speech}»", flush=True)
        if reply.outcome is not None:
            print(f"VR: desenlace {reply.outcome} {reply.area or ''}", flush=True)
            self.outcomes.append((reply.outcome, reply.area))
            if reply.outcome in ("robot", "perro", "llamar"):
                self.on_outcome(reply.outcome, reply.area)
            self.phase = "finished"
        for turn in reply.turns:
            self.log("live_turn", t, speech=turn.speech, emote=turn.emote, heard=self.transcripts[-1:])
            self.speech_by_wav[str(turn.wav)] = turn.speech
            self.plans.append(plan_turn(turn, self.store))
        if not reply.turns:                            # nada que decir (p. ej. ya se había despedido)
            self.phase, self.done = "finished", True
            return
        self.holding = False
        if self.phase != "finished":
            self.phase = "speaking"
        self._enter(t, rig)


# ------------------------------------------------------------------------------------------- recorrido
def fsm_reception(person: str, guest, audio_fn, voice: FsmVoice, listener: Listener, on_cue=None,
                  qr=None, require_qr: bool = True, come_back: bool = True,
                  key: str = "ultra") -> tuple[ConversationTour, dict]:
    """La visita de quien hizo el check-in (``person``, de Face) conducida por la FSM de Luis. ``qr`` es el texto del
    QR leído o una función que lo devuelve (el último que leyó la webcam): si aún no hay, el robot lo pide."""
    qr_fn = qr if callable(qr) else (lambda: qr)
    entry = load_identities().get(person) or {}
    name = entry.get("display_name") or (person if person != UNKNOWN else "visitante")
    store = VisitStore()
    speech_by_wav: dict = {}
    summary = {"person": person, "name": name, "category": "por confirmar", "rank": None, "identity": {},
               "speech_by_wav": speech_by_wav, "destination": None, "escort": None}

    def on_identity(visitor: dict) -> None:
        rejected = visitor.get("status") == identity.STATUS_REJECTED
        rank = visitor.get("rank") or "GUEST"
        summary.update(category="rechazado" if rejected else RANK_CATEGORY.get(rank, "invitado"),
                       rank=None if rejected else rank,
                       identity={k: visitor.get(k) for k in ("status", "reason", "source", "identity_mode",
                                                             "person_id")})
        if not rejected and visitor.get("name"):
            summary["name"] = visitor["name"]

    def on_outcome(mode: str, area: str | None) -> None:
        category = summary["category"] if summary["rank"] else "invitado"
        turn_rank = TURN_RANK.get(summary["rank"] or "GUEST", "guest")
        if mode == "llamar":
            escort, _ = escort_steps("externo", None, come_back=False)
        else:
            escort, _ = escort_steps(category, spaces.floor(area), come_back, route=spaces.route(area), mode=mode,
                                     place=spaces.name(area))
            summary["destination"] = area
        summary["escort"] = mode
        steps, last = [], None
        for step in escort:                            # las frases, en vivo con la misma voz
            if isinstance(step, Say) and step.text:
                emote = "POINT_FRONT" if step.pose == "señalar" else "NONE"
                last = LiveTurns([voice.say(step.text, "GUIDANCE", emote, turn_rank)], [], store, audio_fn)
                steps.append(last)
            else:
                steps.append(step)
        farewell = voice.farewell_turn()               # la despedida de la FSM, tras la indicación final
        if last is not None:
            last.futures.append(farewell)
        else:
            steps.append(LiveTurns([farewell], [], store, audio_fn))
        tour.steps.extend(steps)

    converse = FsmPlayer(key, voice, listener, store, person, on_outcome, on_identity=on_identity, qr_fn=qr_fn,
                         require_qr=require_qr, on_cue=on_cue, speech_by_wav=speech_by_wav, audio_fn=audio_fn)
    steps = [
        WaitForGuest(),
        Pause(1.0, f"check-in: {name}" if person != UNKNOWN else "cara no reconocida"),
        Settle(0.8),                                   # el robot atiende desde su puesto, sin dar un paso
        HriTurns(plans=[], audio_fn=audio_fn, status="conversación"),
    ]
    tour = ConversationTour(key, steps, label=f"{name} · recepción HRI", guest=guest, converse=converse)
    return tour, summary
