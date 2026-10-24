"""FSM de HRI del Club N (T1) según docs/reto-2/HRI_RUNTIME_SPEC.md.

LLEGADA → CARA → CONFIRMACION → GREETING → [STAFF/WORKER: DESTINO_DE_ROL con POINT]
→ CONVERSATION (bucle ASR; la FSM decide el acto, el LLM elige texto+gesto del enum)
→ FAREWELL (por DESPEDIDA o 30 s sin interacción) → END.

Backend-agnostic: usa el Protocolo estructural `Backend` (T5 crea backends/base.py
con la misma forma; aquí no se importa para no acoplar). En tests: FakeBackend.
"""
from __future__ import annotations

import time
from enum import Enum
from typing import Any, Callable, Optional, Protocol

from . import access, contract, intents


# Pregunta de cierre tras la admisión (tratamiento de personas.CARDS: STAFF tutea, el resto «usted»).
INVITE_QUESTION = {
    "VIP": "¿En qué puedo servirle hoy?",
    "REGULAR": "¿Qué le trae hoy por el club?",
    "GUEST": "¿Qué le gustaría conocer hoy?",
    "STAFF": "¿Necesitas algo más antes de empezar tu turno?",
    "WORKER": "¿Necesita algo más antes de empezar?",
    "*": "¿En qué puedo ayudarle hoy?",
}


class State(str, Enum):
    LLEGADA = "LLEGADA"
    CARA = "CARA"
    CONFIRMACION = "CONFIRMACION"
    GREETING = "GREETING"
    DESTINO_DE_ROL = "DESTINO_DE_ROL"
    CONVERSATION = "CONVERSATION"
    GUIDANCE_OFFER = "GUIDANCE_OFFER"
    FAREWELL = "FAREWELL"
    END = "END"


class Backend(Protocol):
    """Mismo shape que hri_runtime/backends/base.py (T5). Estructural, sin import."""

    def state(self) -> dict: ...
    def stream_upper_body(self, t: float, arms14, waist3, head2) -> None: ...
    def play_emote(self, motion_id: int, area: int, t_dispatch: float) -> None: ...
    def point(self, direction: str, t_stroke: float, hold_s: float = 1.5) -> None: ...
    def walk_to(self, area: str) -> None: ...
    def set_velocity(self, vx: float, wz: float) -> None: ...
    def dispatch_d1(self, area: str) -> None: ...
    def stop(self) -> None: ...
    def estop(self) -> None: ...


def default_confirm(face_result: dict, qr_image=None, cedula=None) -> dict:
    """Confirmación de identidad: usa confirm_identity de T2 si existe; si no, stub
    del spec: QR válido de esa persona ⇒ rango; sin QR ⇒ cédula simulada ⇒ WORKER
    + orden de trabajo simulada."""
    try:  # T2 (otro agente) crea hri_runtime/confirm
        from .confirm import confirm_identity  # type: ignore
        return confirm_identity(face_result, qr_image=qr_image, cedula=cedula)
    except Exception:
        pass
    if qr_image is not None and isinstance(qr_image, dict) and qr_image.get("rank"):
        return {"person_id": qr_image.get("person_id", face_result.get("person_id", "P-000")),
                "name": qr_image.get("name", face_result.get("name", "Visitante")),
                "rank": qr_image["rank"], "worker_subtype": qr_image.get("worker_subtype"),
                "work_order": qr_image.get("work_order"), "status": "ok_qr"}
    return {"person_id": "W-001", "name": (face_result or {}).get("name", "Josaed"),
            "rank": "WORKER", "worker_subtype": "logistica",
            "work_order": {"area": "almacenes_logistica", "task": "descarga de insumos (simulada)"},
            "status": "ok_cedula_simulada"}


# Frases deterministas de respaldo (pasan el validador) si el LLM falla.
_CANNED = {
    ("GREETING", "VIP"): "Buenas tardes, {name}. Le damos la bienvenida a casa: Club N, nuestro lugar de encuentro.",
    ("GREETING", "*"): "Le damos la bienvenida a Club N, {name}: nuestro lugar de encuentro. Qué gusto tenerle hoy aquí.",
    ("GREETING", "STAFF"): "¡Qué tal, {name}! ¡Dame esos cinco! Gracias por hacer de este nuestro lugar de encuentro.",
    ("GREETING", "WORKER"): "Le damos la bienvenida a Club N, {name}. Gracias por cuidar nuestra casa; hoy también es parte del equipo.",
    ("ACCESS_DENIAL", "*"): "{name}, para hoy le recomiendo {alt}, en nuestro club; ese otro espacio está reservado.",
    ("GUIDANCE_OFFER", "VIP"): "{name}, el espacio está disponible; ¿desea que le acompañe hasta allí, con gusto?",
    ("GUIDANCE_OFFER", "*"): "{name}, el espacio está disponible; ¿desea que Draco le guíe hasta allí ahora?",
    ("POINT_GUIDE", "*"): "{name}, queda en esa dirección que le señalo ahora mismo, siga por allí.",
    ("DESTINO_DE_ROL", "*"): "{name}, su destino de trabajo queda en la dirección que le señalo ahora; gracias por su trabajo.",
    ("FAREWELL", "*"): "Hasta pronto, {name}. Gracias por visitar nuestro club: esta también es su casa.",
    ("FAREWELL", "STAFF"): "Nos vemos luego, {name}; gracias por hacer de este nuestro lugar de encuentro.",
    ("CONVERSATION", "*"): "Entiendo, {name}. Estoy aquí para lo que necesite; tómese su tiempo.",
}


class HriFSM:
    def __init__(self, backend: Any, llm: Any = None, *, kb: Optional[dict] = None,
                 confirm_fn: Optional[Callable[..., dict]] = None,
                 clock: Callable[[], float] = time.monotonic,
                 conversation_timeout_s: float = 30.0,
                 say: Optional[Callable[[str], None]] = None):
        self.backend = backend
        self.llm = llm
        self.kb = kb if kb is not None else contract.load_kb()
        self.confirm_fn = confirm_fn or default_confirm
        self.clock = clock
        self.timeout_s = conversation_timeout_s
        self.say_fn = say
        self.state = State.LLEGADA
        self.person: Optional[dict] = None
        self.log: list[dict] = []
        self.spoken: list[str] = []
        self._last_interaction = 0.0
        self._pending: Optional[dict] = None  # {"area","direction","mode": walk|d1}

    # ------------------------------------------------------------ helpers
    def _t(self) -> float:
        return self.clock()

    def _say(self, text: str) -> None:
        if text:
            self.spoken.append(text)
            self.log.append({"act": "SAY", "text": text, "t": self._t()})
            if self.say_fn:
                self.say_fn(text)

    def _direction_of(self, area: str) -> Optional[str]:
        sp = self.kb.get("espacios", {}).get(area)
        return sp.get("direccion") if sp else None

    def _emit_gesture(self, typ: str) -> None:
        g = contract.GESTURES.get(typ)
        if not g or g["kind"] == "none":
            return
        if g["kind"] == "preset":
            self.backend.play_emote(g["motion_id"], g["area"], self._t())
            self.log.append({"act": "EMOTE", "type": typ, "t": self._t()})
        elif g["kind"] == "external_pointing":
            direction = contract.TYPE_TO_DIRECTION[typ]
            self.backend.point(direction, self._t())
            self.log.append({"act": "POINT", "direction": direction, "t": self._t()})

    def _canned(self, state: str, direction: Optional[str] = None,
                alternatives: Optional[list[str]] = None) -> dict:
        rank = (self.person or {}).get("rank", "GUEST")
        name = (self.person or {}).get("name", "")
        base = state if state != "ANSWER" else "CONVERSATION"
        line = _CANNED.get((base, rank)) or _CANNED.get((base, "*")) or _CANNED[("CONVERSATION", "*")]
        speech = line.format(name=name, alt=(alternatives or ["recepción"])[0].replace("_", " "))
        typ = contract.allowed_types(state, rank, direction)[0] if not contract.none_allowed(state) else "NONE"
        if typ == "NONE" and state == "ACCESS_DENIAL":
            typ = "NEGATION_NORMAL"
        trig = "" if typ == "NONE" else speech.split(",")[0]
        obj = {"type": typ, "speech": speech, "trigger_text": trig}
        if state == "CONVERSATION":
            obj.update(intent="OTRO", area=None)
        return obj

    def _llm_call(self, state: str, *, utterance: Optional[str] = None, direction: Optional[str] = None,
                  alternatives: Optional[list[str]] = None, pending_area: Optional[str] = None) -> tuple[dict, list[str]]:
        """→ (obj, errors). Con LLM: respond + validación (retry interno). Sin LLM o con
        errores persistentes: frase determinista de respaldo."""
        rank = self.person["rank"]
        if self.llm is not None:
            try:
                r = self.llm.respond(state, rank, self.person, utterance=utterance,
                                     alternatives=alternatives, pending_area=pending_area, direction=direction)
                obj, errs = r.get("obj"), r.get("errors") or []
                if obj is not None and not errs:
                    return obj, []
                self.log.append({"act": "LLM_INVALID", "state": state, "errors": errs, "t": self._t()})
            except Exception as exc:  # LLM caído → respaldo determinista
                self.log.append({"act": "LLM_ERROR", "state": state, "error": str(exc), "t": self._t()})
        return self._canned(state, direction, alternatives), []

    # ------------------------------------------------------------ flujo principal
    def arrival(self, face_result: Optional[dict] = None, qr_image=None, cedula=None) -> dict:
        """LLEGADA → CARA → CONFIRMACION → GREETING (+DESTINO_DE_ROL). Devuelve person."""
        self.state = State.LLEGADA
        self.log.append({"act": "ARRIVAL", "t": self._t()})
        self.state = State.CARA
        fr = face_result if face_result is not None else {"name": None}
        self.state = State.CONFIRMACION
        person = self.confirm_fn(fr, qr_image, cedula) or {}
        rank, st = access.normalize_rank(person.get("rank", "WORKER"), person.get("worker_subtype"))
        person["rank"], person["worker_subtype"] = rank, st
        self.person = person
        self.log.append({"act": "CONFIRMED", "person": person, "t": self._t()})
        self._greeting()
        if rank in ("STAFF", "WORKER"):
            self._destino_de_rol()
        self._invite_reply(rank)
        self.state = State.CONVERSATION
        self._last_interaction = self._t()
        return person

    def _greeting(self) -> None:
        self.state = State.GREETING
        obj, _ = self._llm_call("GREETING")
        self._emit_gesture(obj.get("type", "NONE"))
        self._say(obj.get("speech", ""))

    def _invite_reply(self, rank: str) -> None:
        """Tras la admisión el robot termina SIEMPRE con una pregunta que invite a hablar: si lo
        último dicho no acaba en «?» (el LLM no la puso, o fue una frase de respaldo), se añade
        una pregunta breve acorde al rango y a su tratamiento."""
        last = (self.spoken[-1] if self.spoken else "").strip()
        if last.endswith("?"):
            return
        self._say(INVITE_QUESTION.get(rank, INVITE_QUESTION["*"]))

    def _destino_de_rol(self) -> None:
        wo = (self.person or {}).get("work_order") or {}
        area = wo.get("area")
        direction = self._direction_of(area) if area else None
        if not direction:
            return
        self.state = State.DESTINO_DE_ROL
        self.person["work_order_direction"] = direction
        obj, _ = self._llm_call("DESTINO_DE_ROL", direction=direction, pending_area=area)
        self._emit_gesture(obj.get("type") or contract.DIRECTION_TO_TYPE[direction])
        self._say(obj.get("speech", ""))

    # ------------------------------------------------------------ bucle de conversación
    def on_utterance(self, text: str) -> list[dict]:
        if self.state in (State.END, State.LLEGADA) or self.person is None:
            return []
        mark = len(self.log)
        self._last_interaction = self._t()
        if self.state == State.GUIDANCE_OFFER:
            self._handle_offer_reply(text)
        elif self.state == State.CONVERSATION:
            self._handle_conversation(text)
        return self.log[mark:]

    def _handle_conversation(self, text: str) -> None:
        r = None
        fb_intent, fb_area = intents.fallback_intent(text)
        # Despedida inequívoca («gracias, adiós»): la reconciliación de abajo forzaría DESPEDIDA de
        # todos modos, así que la llamada CONVERSATION al LLM se descartaba y el turno pagaba dos
        # llamadas (CONVERSATION + FAREWELL, ~5 s extra en el Orin). Se salta la primera.
        skip_llm = fb_intent == "DESPEDIDA" and not intents._GO_VERB_RE.search(intents._norm(text))
        if self.llm is not None and not skip_llm:
            try:
                res = self.llm.respond("CONVERSATION", self.person["rank"], self.person, utterance=text)
                r = res.get("obj") if not (res.get("errors") or []) else None
            except Exception as exc:
                self.log.append({"act": "LLM_ERROR", "state": "CONVERSATION", "error": str(exc), "t": self._t()})
        if r is None:
            r = self._canned("CONVERSATION")
            r = {**r, "intent": fb_intent, "area": fb_area}
        intent_, area = r.get("intent"), r.get("area")
        # reconciliación determinista (T6): el respaldo regex corrige al LLM en los dos
        # errores que rompen la seguridad de accesos: pedir destino explícito leído como
        # pregunta/charla, y despedida leída como charla. PREGUNTA informativas (_INFO_RE)
        # nunca se reconcilian a PIDE_DESTINO.
        if (intent_ in ("PREGUNTA", "OTRO") and fb_intent == "PIDE_DESTINO" and fb_area
                and intents._GO_VERB_RE.search(intents._norm(text))
                and not intents._INFO_RE.search(intents._norm(text))):
            self.log.append({"act": "INTENT_RECONCILED", "llm": intent_, "fallback": fb_intent,
                             "area": fb_area, "t": self._t()})
            intent_, area = fb_intent, fb_area
        if (fb_intent == "DESPEDIDA" and intent_ != "DESPEDIDA"
                and not intents._GO_VERB_RE.search(intents._norm(text))):
            self.log.append({"act": "INTENT_RECONCILED", "llm": intent_, "fallback": fb_intent,
                             "t": self._t()})
            intent_ = fb_intent
        self.log.append({"act": "INTENT", "intent": intent_, "area": area, "t": self._t()})

        if intent_ == "DESPEDIDA":
            self._farewell()
            return
        if intent_ == "PIDE_DESTINO":
            txt_area = intents.match_area(text)   # el área de la decisión de acceso queda
            if txt_area and txt_area != area:     # anclada al texto dicho, no a la fantasía del LLM
                self.log.append({"act": "AREA_GROUNDED", "llm_area": area, "text_area": txt_area,
                                 "t": self._t()})
                area = txt_area
            elif not area:
                area = txt_area                    # robustez: el LLM omitió el área ⇒ keywords
        if intent_ == "PIDE_DESTINO" and area:
            self._pide_destino(area)
            return
        # PREGUNTA / SI / NO / OTRO ⇒ responde con la KB, SIN guiar
        if intent_ == "PREGUNTA":
            r = self._kb_grounded(r, area, text)
        self._emit_gesture(r.get("type", "NONE"))
        self._say(r.get("speech", ""))

    _DIR_ES = {"right": "a mi derecha", "left": "a mi izquierda", "front": "al frente"}

    def _kb_grounded(self, r: dict, area: Optional[str], text: str = "") -> dict:
        """Garantía «responde solo con la KB»: si el speech del LLM no cita ningún dato
        del club (nombre de espacio, piso u horario), se reconstruye determinista desde
        club_kb.json con la forma de tratamiento del rango (T6)."""
        import re as _re
        speech = r.get("speech") or ""
        low = speech.lower()
        hits = 0
        for sp in self.kb.get("espacios", {}).values():
            frag1 = str(sp.get("nombre", "")).lower()
            if any(len(f.strip()) > 4 and f.strip() in low for f in _re.split(r"\s+y\s+|,|/", frag1)):
                hits += 1
        for sv in self.kb.get("servicios", []):
            n = str(sv.get("nombre", "")).lower()
            if n and any(len(w) > 3 and w in low for w in _re.split(r"[\s/]+", n)):
                hits += 1
        if hits >= 1:                         # cita datos de la KB ⇒ se conserva la frase del LLM
            return r
        if area and _re.search(r"(?<!\w)(piso|horario|abre|cierra|\d{1,2}:\d{2})(?!\w)", low):
            return r
        rank = self.person.get("rank", "GUEST")
        name = self.person.get("name", "")
        from .personas import titulo_para
        tit = (titulo_para(self.person) or "").capitalize()
        vip = f"{tit} {name}, si me permite" if tit else f"{name}, si me permite"
        prefix = {"VIP": vip, "STAFF": f"{name}, te paso el dato"}.get(rank, f"{name}, con gusto")
        general = bool(intents._INFO_RE.search(intents._norm(text)))   # «¿qué opciones hay?» ⇒ listar
        if area and not general and area in self.kb.get("espacios", {}):
            sp = self.kb["espacios"][area]
            body = (f"{str(sp['nombre']).lower()} queda {self._DIR_ES.get(sp.get('direccion'), 'en el club')}, "
                    f"en el piso {sp.get('piso', '2')}, con horario {str(sp.get('horario', '')).lower()}.")
        else:
            permitidos = access.allowed_spaces(rank, self.person.get("worker_subtype"))
            nombres = [str(self.kb["espacios"][s]["nombre"]).lower()
                       for s in permitidos[:4] if s in self.kb.get("espacios", {})]
            body = ("puede visitar " + ", ".join(nombres[:-1]) + " o " + nombres[-1]
                    + ", todos dentro del club.") if len(nombres) >= 2 else \
                "el club cuenta con varias zonas autorizadas; con gusto le confirmo en recepción."
        speech = f"{prefix}: {body}"
        self.log.append({"act": "KB_GROUNDED", "area": area, "t": self._t()})
        return {**r, "speech": speech, "type": "NONE", "trigger_text": ""}

    def _pide_destino(self, area: str) -> None:
        rank, st = self.person["rank"], self.person.get("worker_subtype")
        if not access.allowed(area, rank, st):
            self._access_denial(area, rank, st)
            return
        direction = self._direction_of(area)
        if rank in ("STAFF", "WORKER"):
            self._point_guide(area, direction)  # POINT + indicación verbal, sin oferta
        else:
            self.state = State.GUIDANCE_OFFER
            self._pending = {"area": area, "direction": direction,
                             "mode": "walk" if rank == "VIP" else "d1"}
            obj, _ = self._llm_call("GUIDANCE_OFFER", pending_area=area)
            self._emit_gesture(obj.get("type", "NONE"))
            self._say(obj.get("speech", ""))

    def _access_denial(self, area: str, rank: str, st: Optional[str]) -> None:
        alt = access.alternative(area, rank, st)
        alts = [alt] if alt else []
        obj, _ = self._llm_call("ACCESS_DENIAL", alternatives=alts, pending_area=area)
        self._emit_gesture(obj.get("type") or "NEGATION_NORMAL")
        self._say(obj.get("speech", ""))
        self.state = State.CONVERSATION

    def _point_guide(self, area: str, direction: Optional[str]) -> None:
        obj, _ = self._llm_call("POINT_GUIDE", direction=direction, pending_area=area)
        typ = obj.get("type") or (contract.DIRECTION_TO_TYPE.get(direction or "", "NONE"))
        self._emit_gesture(typ)
        self._say(obj.get("speech", ""))
        self.state = State.CONVERSATION

    def _handle_offer_reply(self, text: str) -> None:
        pending, self._pending = self._pending or {}, None
        area, mode, direction = pending.get("area"), pending.get("mode"), pending.get("direction")
        intent_ = None
        if self.llm is not None:
            try:
                res = self.llm.respond("CONVERSATION", self.person["rank"], self.person, utterance=text,
                                       extra_context="PREGUNTA PENDIENTE: acabas de ofrecer acompañar/guiar a un espacio. "
                                                     "Clasifica la respuesta del visitante: SI si acepta, NO si rechaza; "
                                                     "escribe una frase breve acorde (acto RESPOND).")
                obj = res.get("obj") if not (res.get("errors") or []) else None
                if obj:
                    intent_ = obj.get("intent")
                    if obj.get("speech"):
                        self._offer_speech = obj
                    if intent_ in ("SI", "NO"):
                        self._emit_gesture(obj.get("type", "NONE"))
                        self._say(obj.get("speech", ""))
            except Exception:
                intent_ = None
        fb = intents.fallback_intent(text)[0]
        if intent_ is None:
            intent_ = fb
        elif intent_ not in ("SI", "NO") and fb in ("SI", "NO"):
            intent_ = fb  # el LLM no vio el sí/no; el respaldo regex sí
        self.state = State.CONVERSATION
        self._last_interaction = self._t()
        if intent_ == "SI":
            if mode == "walk" and area:
                self.backend.walk_to(area)  # bloquea gestos mientras camina: nunca gestos al caminar
                self.log.append({"act": "WALK_WITH", "area": area, "t": self._t()})
            elif area:
                self.backend.dispatch_d1(area)
                self.log.append({"act": "DISPATCH_D1", "area": area, "t": self._t()})
        elif intent_ == "NO":
            self._point_guide(area or "", direction)
        # otro intent (p. ej. DESPEDIDA) ⇒ se atiende en el siguiente turno

    def _farewell(self) -> None:
        self.state = State.FAREWELL
        rank = self.person["rank"]
        obj, _ = self._llm_call("FAREWELL")
        self._emit_gesture(obj.get("type") or ("SOCIAL_HEART" if rank == "VIP" else "FAREWELL_WAVE"))
        self._say(obj.get("speech", ""))
        self.state = State.END
        self.log.append({"act": "END", "t": self._t()})

    def tick(self, t: Optional[float] = None) -> bool:
        """Timeout de conversación (30 s sin interacción ⇒ FAREWELL). → True si despidió."""
        now = self._t() if t is None else t
        if self.state in (State.CONVERSATION, State.GUIDANCE_OFFER) and now - self._last_interaction >= self.timeout_s:
            self.log.append({"act": "TIMEOUT", "idle_s": round(now - self._last_interaction, 1), "t": now})
            self._pending = None
            self._farewell()
            return True
        return False
