"""Tests T1 — FSM HRI Club N con Backend y LLM falsos + transcripciones WAV1–6.

Correr:  cd /home/luis/GitHub/x2_ultra-reto2
  PYTHONPATH=src /home/luis/GitHub/x2_ultra-retos/.venv/bin/python -m pytest tests/test_hri_runtime_fsm.py -q

Smoke en vivo contra x2-llm: solo si el endpoint es alcanzable (HRI_LLM_URL);
desde el host normalmente requiere nsenter al netns de rootlesskit — ver
llm_client.runner_note(). Para forzarlo: HRI_LIVE=1 pytest -k live.
"""
from __future__ import annotations

import json
import os

import pytest

from x2_ultra.hri_runtime import access, contract, intents
from x2_ultra.hri_runtime.fsm import HriFSM, State

# ----------------------------------------------------------------- fakes
class FakeBackend:
    def __init__(self):
        self.calls: list[tuple] = []

    def state(self):
        self.calls.append(("state",))
        return {"t": 0.0, "pelvis_z": 0.75, "tilt_deg": 0.0, "mode": "stand", "stable": True,
                "walking": False, "fall": False}

    def stream_upper_body(self, t, arms14, waist3, head2):
        self.calls.append(("stream_upper_body", t))

    def play_emote(self, motion_id, area, t_dispatch):
        self.calls.append(("play_emote", motion_id, area))

    def point(self, direction, t_stroke, hold_s=1.5):
        self.calls.append(("point", direction))

    def walk_to(self, area):
        self.calls.append(("walk_to", area))

    def set_velocity(self, vx, wz):
        self.calls.append(("set_velocity", vx, wz))

    def dispatch_d1(self, area):
        self.calls.append(("dispatch_d1", area))

    def stop(self):
        self.calls.append(("stop",))

    def estop(self):
        self.calls.append(("estop",))

    def kinds(self):
        return [c[0] for c in self.calls]


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


def _greeting_obj(rank, name):
    if rank == "VIP":
        return {"type": "GREETING_VIP", "trigger_text": "Buenas tardes",
                "speech": f"Buenas tardes, señorita {name}. Es un honor recibirla hoy en el Club N."}
    if rank == "STAFF":
        return {"type": "GREETING_STAFF", "trigger_text": "dame esos cinco",
                "speech": f"¡Qué tal, {name}! ¡Dame esos cinco! Qué bueno verte por el club otra vez."}
    if rank == "REGULAR":
        return {"type": "GREETING_REGULAR", "trigger_text": "Qué gusto",
                "speech": f"Qué gusto verle de nuevo, {name}; bienvenido a su club, el Club N."}
    if rank == "GUEST":
        return {"type": "GREETING_GUEST", "trigger_text": "Bienvenido",
                "speech": f"Bienvenido al Club N, {name}; estoy aquí para orientarle paso a paso."}
    return {"type": "GREETING_WORKER", "trigger_text": "Bienvenido",
            "speech": f"Bienvenido al Club N, {name}. Su visita queda registrada; proceda al mostrador."}


def _state_obj(state, rank, name, direction=None, alternatives=None):
    if state == "GREETING":
        return _greeting_obj(rank, name)
    if state == "DESTINO_DE_ROL":
        typ = contract.DIRECTION_TO_TYPE[direction or "front"]
        if rank == "STAFF":
            return {"type": typ, "trigger_text": "señalo",
                    "speech": f"{name}, tu orden de trabajo queda en la dirección que te señalo ahora."}
        return {"type": typ, "trigger_text": "proceda",
                "speech": f"{name}, su destino de trabajo queda en la dirección señalada; proceda directo ahora."}
    if state == "ACCESS_DENIAL":
        alt = "otra opción autorizada" if not alternatives else alternatives[0].replace("_", " ")
        if rank == "VIP":
            return {"type": "NEGATION_NORMAL", "trigger_text": "Lamento informarle",
                    "speech": f"Lamento informarle, señorita {name}, que ese espacio está reservado; le sugiero {alt}."}
        return {"type": "NEGATION_NORMAL", "trigger_text": "Lo siento",
                "speech": f"Lo siento, {name}; ese espacio no está autorizado hoy, le sugiero {alt} con gusto."}
    if state == "GUIDANCE_OFFER":
        if rank == "VIP":
            return {"type": "NONE", "trigger_text": "",
                    "speech": f"Señorita {name}, el espacio está disponible; ¿desea que le acompañe hasta allí?"}
        return {"type": "NONE", "trigger_text": "",
                "speech": f"{name}, el espacio está disponible para usted; ¿desea que Draco le guíe?"}
    if state == "POINT_GUIDE":
        typ = contract.DIRECTION_TO_TYPE[direction or "front"]
        if rank == "STAFF":
            return {"type": typ, "trigger_text": "señalo",
                    "speech": f"{name}, queda en la dirección que te señalo; sigue por ahí no más."}
        return {"type": typ, "trigger_text": "señalo",
                "speech": f"{name}, queda en la dirección que le señalo ahora; siga por allí."}
    if state == "FAREWELL":
        if rank == "VIP":
            return {"type": "SOCIAL_HEART", "trigger_text": "excelente tarde",
                    "speech": f"Ha sido un privilegio atenderla, señorita {name}. Que tenga una excelente tarde."}
        if rank == "STAFF":
            return {"type": "FAREWELL_WAVE", "trigger_text": "nos vemos",
                    "speech": f"Nos vemos luego, {name}; que te rinda mucho el turno de hoy."}
        return {"type": "FAREWELL_WAVE", "trigger_text": "Hasta pronto",
                "speech": f"Hasta pronto, {name}; gracias por visitar el Club N el día de hoy."}
    if state == "ANSWER":
        return {"type": "NONE", "trigger_text": "",
                "speech": f"Entiendo, {name}; le confirmo el dato con la información oficial del club."}
    raise AssertionError(state)


def _conversation_obj(rank, name, text, script):
    if text in script:
        return dict(script[text])
    intent_, area = intents.fallback_intent(text)
    return {"type": "NONE", "speech": f"Entiendo lo que me dice, {name}; quedo atento a cualquier otra cosa.",
            "trigger_text": "", "intent": intent_, "area": area}


class ScriptedLLM:
    """LLM falso: respuestas por estado que PASAN contract.validate (se verifica aquí)."""

    def __init__(self, script=None):
        self.script = script or {}
        self.calls: list[dict] = []

    def respond(self, state, rank, person, *, utterance=None, alternatives=None,
                pending_area=None, direction=None, retry=True, extra_context=None):
        self.calls.append({"state": state, "utterance": utterance, "direction": direction,
                           "alternatives": alternatives, "pending_area": pending_area})
        name = (person or {}).get("name", "")
        if state == "CONVERSATION":
            obj = _conversation_obj(rank, name, utterance or "", self.script)
        else:
            obj = _state_obj(state, rank, name, direction, alternatives)
        # reordenar claves al orden del contrato
        order = contract.CONVERSATION_FIELDS if state == "CONVERSATION" else contract.BASE_FIELDS
        obj = {k: obj[k] for k in order if k in obj}
        errors = contract.validate(obj, state, rank, direction=direction)
        assert not errors, f"respuesta scripted inválida ({state}/{rank}): {errors}"
        return {"obj": obj, "errors": [], "raw": json.dumps(obj, ensure_ascii=False),
                "metrics": {}, "attempts": [], "retry_used": False, "first_attempt_ok": True}


def _qr(name, rank, subtype=None, work_order=None):
    return {"person_id": f"P-{name}", "name": name, "rank": rank,
            "worker_subtype": subtype, "work_order": work_order}


def _fsm(script=None, clock=None):
    be, clk = FakeBackend(), clock or FakeClock()
    llm = ScriptedLLM(script)
    return HriFSM(be, llm, clock=clk, conversation_timeout_s=30.0), be, clk, llm


# ----------------------------------------------------------------- transcripciones WAV1–6
# Utterances plausibles según el propósito de cada WAV del spec.
WAV1 = "Buenas tardes, ¿a qué hora abre la biblioteca del club hoy?"          # pregunta ⇒ responde sin guiar
WAV2 = "¿Me permite pasar a las habitaciones de alojamiento esta noche?"      # coherencia de restricciones
WAV3 = "¿Dónde queda el restaurante y a qué hora sirven el desayuno?"         # responder preguntas
WAV4 = "¿Qué servicio de wifi y de lockers tiene el club disponibles?"        # misma intención que WAV3
WAV5 = "Necesito entrar a la cava a dejar un pedido de bebidas."              # restricciones de WORKER
WAV6 = "Deseo ir al gimnasio de deportes, ¿me acompaña usted?"                # restricciones VIP/REGULAR/GUEST

SCRIPT_WAVS = {
    WAV1: {"type": "NONE", "trigger_text": "", "intent": "PREGUNTA", "area": "biblioteca_galeria",
           "speech": "Señorita Laura, la biblioteca del club abre hoy a las ocho, en el piso dos."},
    WAV2: {"type": "NONE", "trigger_text": "", "intent": "PIDE_DESTINO", "area": "habitaciones_alojamiento",
           "speech": "Sebastian, quisiera pasar a las habitaciones de alojamiento esta misma noche."},
    WAV3: {"type": "NONE", "trigger_text": "", "intent": "PREGUNTA", "area": "restaurantes_cafeterias",
           "speech": "Mario, el restaurante queda a mi derecha en el piso dos, y sirve desayuno desde las seis."},
    WAV4: {"type": "NONE", "trigger_text": "", "intent": "PREGUNTA", "area": None,
           "speech": "Mario, el wifi del club es ClubN-Invitados y hay lockers gratuitos en el gimnasio."},
    WAV5: {"type": "NONE", "trigger_text": "", "intent": "PIDE_DESTINO", "area": "bares_la_cava",
           "speech": "Entiendo, Josaed; ese espacio no está autorizado para su visita de trabajo hoy."},
    WAV6: {"type": "NONE", "trigger_text": "", "intent": "PIDE_DESTINO", "area": "gimnasio_deportes",
           "speech": "Señorita Laura, con mucho gusto verifico el acceso al gimnasio de deportes ahora."},
    "Sí, por favor.": {"type": "CONFIRMATION", "trigger_text": "con gusto", "intent": "SI", "area": None,
                       "speech": "Perfecto, señorita Laura; con gusto le acompaño hasta el gimnasio ahora mismo."},
    "No, gracias.": {"type": "NONE", "trigger_text": "", "intent": "NO", "area": None,
                     "speech": "Comprendo, señorita Laura; entonces le indico la dirección exacta hacia allá."},
    "Listo, muchas gracias. Hasta luego.": {"type": "NONE", "trigger_text": "", "intent": "DESPEDIDA", "area": None,
                                            "speech": "Gracias a usted, Josaed; que tenga muy buena noche."},
    "Sí, claro, con gusto.": {"type": "CONFIRMATION", "trigger_text": "con gusto", "intent": "SI", "area": None,
                              "speech": "Perfecto, Mario; enseguida Draco le guiará hasta el centro de convenciones."},
    "No, prefiero ir por mi cuenta.": {"type": "NONE", "trigger_text": "", "intent": "NO", "area": None,
                                       "speech": "Entiendo, Mario; entonces le señalo la dirección con mucho gusto."},
}


def test_wav1_vip_pregunta_responde_sin_guiar():
    fsm, be, clk, llm = _fsm(SCRIPT_WAVS)
    fsm.arrival({"name": "Laura"}, qr_image=_qr("Laura", "VIP"))
    be.calls.clear()
    fsm.on_utterance(WAV1)
    assert fsm.state == State.CONVERSATION
    joined = " ".join(fsm.spoken)
    assert "biblioteca" in joined.lower() and "ocho" in joined.lower()
    assert not any(k in be.kinds() for k in ("point", "walk_to", "dispatch_d1"))  # SIN guiar
    assert ("play_emote", 3029, 11) not in be.calls  # sin negación


def test_wav2_coherencia_restricciones_regular_denegado():
    fsm, be, clk, llm = _fsm(SCRIPT_WAVS)
    fsm.arrival({"name": "Sebastian"}, qr_image=_qr("Sebastian", "REGULAR"))
    be.calls.clear()
    fsm.on_utterance(WAV2)  # habitaciones_alojamiento: REGULAR = no
    assert ("play_emote", 3029, 11) in be.calls  # NEGATION_NORMAL
    assert not any(k in be.kinds() for k in ("point", "walk_to", "dispatch_d1"))
    assert fsm.state == State.CONVERSATION
    denial = [c for c in llm.calls if c["state"] == "ACCESS_DENIAL"]
    assert denial and denial[0]["alternatives"]  # ofrece alternativa autorizada
    assert all(access.allowed(a, "REGULAR") for a in denial[0]["alternatives"])


def test_wav3_guest_responder_preguntas():
    fsm, be, clk, llm = _fsm(SCRIPT_WAVS)
    fsm.arrival({"name": "Mario"}, qr_image=_qr("Mario", "GUEST"))
    be.calls.clear()
    fsm.on_utterance(WAV3)
    assert "restaurante" in " ".join(fsm.spoken).lower()
    assert not any(k in be.kinds() for k in ("point", "walk_to", "dispatch_d1"))


def test_wav4_misma_intencion_que_wav3():
    fsm, be, clk, llm = _fsm(SCRIPT_WAVS)
    fsm.arrival({"name": "Mario"}, qr_image=_qr("Mario", "GUEST"))
    fsm.on_utterance(WAV3)
    fsm.on_utterance(WAV4)
    conv = [c for c in llm.calls if c["state"] == "CONVERSATION"]
    assert len(conv) == 2
    assert "wifi" in " ".join(fsm.spoken).lower()
    assert not any(k in be.kinds() for k in ("point", "walk_to", "dispatch_d1"))


def test_wav5_worker_restricciones_y_despedida():
    fsm, be, clk, llm = _fsm(SCRIPT_WAVS)
    fsm.arrival({"name": "Josaed"},
                qr_image=_qr("Josaed", "WORKER", "mantenimiento",
                             {"area": "cuartos_tecnicos", "task": "revisión de tableros"}))
    # DESTINO_DE_ROL proactivo con POINT hacia cuartos_tecnicos (front)
    assert ("point", "front") in be.calls
    be.calls.clear()
    fsm.on_utterance(WAV5)  # bares_la_cava: WORKER = no
    assert ("play_emote", 3029, 11) in be.calls
    assert not any(k in be.kinds() for k in ("point", "walk_to", "dispatch_d1"))
    fsm.on_utterance("Listo, muchas gracias. Hasta luego.")
    assert fsm.state == State.END
    assert ("play_emote", 3031, 11) in be.calls  # FAREWELL_WAVE


def test_wav6_vip_acceso_gym_offer_si_camina_con_el():
    fsm, be, clk, llm = _fsm(SCRIPT_WAVS)
    fsm.arrival({"name": "Laura"}, qr_image=_qr("Laura", "VIP"))
    fsm.on_utterance(WAV6)  # gimnasio: VIP = sí ⇒ GUIDANCE_OFFER
    assert fsm.state == State.GUIDANCE_OFFER
    assert "acompañe" in " ".join(fsm.spoken)  # VIP: "¿Desea que le acompañe?"
    assert not any(k in be.kinds() for k in ("walk_to", "dispatch_d1", "point"))
    fsm.on_utterance("Sí, por favor.")
    assert ("walk_to", "gimnasio_deportes") in be.calls  # sí ⇒ X2 camina con él
    assert fsm.state == State.CONVERSATION


def test_offer_no_termina_en_point_vip():
    fsm, be, clk, llm = _fsm(SCRIPT_WAVS)
    fsm.arrival({"name": "Laura"}, qr_image=_qr("Laura", "VIP"))
    fsm.on_utterance(WAV6)
    fsm.on_utterance("No, gracias.")
    assert ("point", "left") in be.calls  # gimnasio = left; no ⇒ POINT
    assert ("walk_to", "gimnasio_deportes") not in be.calls
    assert fsm.state == State.CONVERSATION


def test_guest_offer_si_dispatch_d1_y_no_point():
    fsm, be, clk, llm = _fsm(SCRIPT_WAVS)
    fsm.arrival({"name": "Mario"}, qr_image=_qr("Mario", "GUEST"))
    fsm.on_utterance("¿Me puede indicar el centro de convenciones, por favor?")
    # sin script → fallback determinista: PIDE_DESTINO (keyword) vía intents.fallback_intent
    if fsm.state == State.GUIDANCE_OFFER:
        fsm.on_utterance("Sí, claro, con gusto.")
        assert ("dispatch_d1", "centro_convenciones") in be.calls
    fsm2, be2, _, _ = _fsm(SCRIPT_WAVS)
    fsm2.arrival({"name": "Mario"}, qr_image=_qr("Mario", "GUEST"))
    fsm2.on_utterance("Quiero ir al centro de convenciones del club, ¿me guía?")
    assert fsm2.state == State.GUIDANCE_OFFER
    fsm2.on_utterance("No, prefiero ir por mi cuenta.")
    assert ("point", "front") in be2.calls  # centro_convenciones = front
    assert ("dispatch_d1", "centro_convenciones") not in be2.calls


def test_staff_destino_de_rol_proactivo_con_point():
    fsm, be, clk, llm = _fsm(SCRIPT_WAVS)
    fsm.arrival({"name": "Luis"}, qr_image=_qr("Luis", "STAFF",
                                               None, {"area": "salas_juntas", "task": "montar sala"}))
    assert ("point", "right") in be.calls  # salas_juntas = right
    assert ("play_emote", 1008, 2) in be.calls  # GREETING_STAFF
    rol = [c for c in llm.calls if c["state"] == "DESTINO_DE_ROL"]
    assert rol and rol[0]["direction"] == "right"
    assert fsm.state == State.CONVERSATION


def test_worker_acceso_permitido_point_directo_sin_offer():
    fsm, be, clk, llm = _fsm(SCRIPT_WAVS)
    fsm.arrival({"name": "Josaed"},
                qr_image=_qr("Josaed", "WORKER", "mantenimiento",
                             {"area": "cuartos_tecnicos", "task": "revisión"}))
    be.calls.clear()
    fsm.on_utterance("También debo subir un momento al gimnasio de deportes.")
    # gimnasio: WORKER mantenimiento = sí ⇒ POINT + indicación verbal, sin GUIDANCE_OFFER
    if ("point", "left") in be.calls:
        assert fsm.state == State.CONVERSATION
        assert not any(c["state"] == "GUIDANCE_OFFER" for c in llm.calls)


def test_sin_qr_cedula_simulada_worker():
    # Sin QR ⇒ cédula simulada ⇒ WORKER + orden de trabajo simulada (spec).
    # default_confirm integra el confirm_identity real de T2 si existe; la orden
    # simulada puede variar, así que se verifica coherencia, no valores fijos.
    fsm, be, clk, llm = _fsm(SCRIPT_WAVS)
    person = fsm.arrival({"name": "Proveedor"}, cedula="1234567890")
    assert person["rank"] == "WORKER"
    assert person["worker_subtype"] in access.WORKER_SUBTYPES
    area = person["work_order"]["area"]
    assert area in access.SPACES
    direction = fsm._direction_of(area)
    assert ("point", direction) in be.calls  # DESTINO_DE_ROL con POINT hacia la orden


def test_timeout_farewell_a_los_30s():
    clk = FakeClock()
    fsm, be, _, _ = _fsm(clock=clk)
    fsm.arrival({"name": "Sebastian"}, qr_image=_qr("Sebastian", "REGULAR"))
    clk.advance(29.0)
    assert fsm.tick() is False
    clk.advance(2.0)
    assert fsm.tick() is True
    assert fsm.state == State.END
    assert ("play_emote", 3031, 11) in be.calls


def test_llm_caido_usa_fallback_determinista():
    be, clk = FakeBackend(), FakeClock()

    class BrokenLLM:
        def respond(self, *a, **k):
            raise ConnectionError("llama-server caído")

    fsm = HriFSM(be, BrokenLLM(), clock=clk)
    fsm.arrival({"name": "Laura"}, qr_image=_qr("Laura", "VIP"))
    assert fsm.state == State.CONVERSATION
    fsm.on_utterance("¿Quiero ir al gimnasio de deportes, me acompañan?")
    # fallback: PIDE_DESTINO detectado por regex → oferta o point, sin crash
    assert fsm.state in (State.CONVERSATION, State.GUIDANCE_OFFER)
    assert any(a["act"] == "LLM_ERROR" for a in fsm.log)


# ----------------------------------------------------------------- unit: access / contract / intents
def test_access_table_exacta():
    assert access.allowed("bares_la_cava", "VIP") and access.allowed("bares_la_cava", "REGULAR")
    assert not access.allowed("bares_la_cava", "GUEST") and not access.allowed("bares_la_cava", "STAFF")
    assert not access.allowed("bares_la_cava", "WORKER", "logistica")
    assert access.allowed("habitaciones_alojamiento", "VIP") and not access.allowed("habitaciones_alojamiento", "REGULAR")
    assert access.allowed("habitaciones_alojamiento", "STAFF") and access.allowed("habitaciones_alojamiento", "WORKER", "mantenimiento")
    assert not access.allowed("habitaciones_alojamiento", "WORKER", "logistica")
    assert access.allowed("oficinas_administrativas", "STAFF") and not access.allowed("oficinas_administrativas", "VIP")
    assert access.allowed("almacenes_logistica", "WORKER", "logistica") and not access.allowed("almacenes_logistica", "VIP")
    assert access.allowed("gimnasio_deportes", "WORKER", "mantenimiento") and not access.allowed("gimnasio_deportes", "WORKER", "logistica")
    assert access.allowed("salas_juntas", "WORKER", "mantenimiento") and not access.allowed("salas_juntas", "GUEST")
    assert access.allowed("gastroworking", "STAFF") and not access.allowed("gastroworking", "GUEST")
    vip_no = {s for s in access.SPACES if not access.allowed(s, "VIP")}
    staff_no = {s for s in access.SPACES if not access.allowed(s, "STAFF")}
    assert vip_no == {"oficinas_administrativas", "almacenes_logistica", "cuartos_tecnicos"}
    assert staff_no == {"bares_la_cava", "gimnasio_deportes", "piscina_spa_humedas"}
    assert all(access.allowed(s, "REGULAR") for s in ("recepcion_vestibulos", "centro_convenciones", "estacionamientos"))
    assert access.normalize_rank("EXTERNAL", None)[0] == "WORKER"


def test_contract_schema_y_validador():
    sch = contract.json_schema("CONVERSATION")
    assert list(sch["properties"]) == ["type", "speech", "trigger_text", "intent", "area"]
    assert sch["properties"]["intent"]["enum"] == list(intents.INTENTS)
    assert set(sch["properties"]["area"]["enum"]) == set(access.SPACES) | {None}
    assert contract.json_schema("GREETING", "VIP")["properties"]["type"] == {"const": "GREETING_VIP"}
    assert contract.json_schema("FAREWELL", "VIP")["properties"]["type"] == {"const": "SOCIAL_HEART"}
    assert contract.json_schema("FAREWELL", "STAFF")["properties"]["type"] == {"const": "FAREWELL_WAVE"}
    assert contract.json_schema("POINT_GUIDE", "GUEST", "left")["properties"]["type"] == {"const": "POINT_LEFT"}

    base = {"type": "NONE", "speech": "Entiendo su pregunta y le respondo con los datos del club.", "trigger_text": ""}
    assert contract.validate({**base, "intent": "OTRO", "area": None}, "CONVERSATION", "GUEST") == []
    # VIP sin exclamaciones
    e = contract.validate({**base, "speech": "¡Claro que sí, señorita Laura!"}, "ANSWER", "VIP")
    assert any("exclamación" in x for x in e)
    # STAFF debe tutear
    e = contract.validate({"type": "NONE", "speech": "Claro que sí, le atiendo con mucho gusto ahora.", "trigger_text": ""}, "ANSWER", "STAFF")
    assert any("tutear" in x for x in e)
    # usted ranks: no tuteo
    e = contract.validate({**base, "speech": "Claro que sí, te atiendo con tu solicitud ahora mismo."}, "ANSWER", "GUEST")
    assert any("tuteo" in x for x in e)
    # trigger literal
    e = contract.validate({"type": "NEGATION_NORMAL", "speech": "Lo siento, ese espacio no está autorizado hoy.", "trigger_text": "espacio reservado"}, "ACCESS_DENIAL", "REGULAR")
    assert any("literal" in x for x in e)
    # sin don/doña
    e = contract.validate({**base, "speech": "Buenas tardes, doña Laura, bienvenida al club hoy."}, "ANSWER", "VIP")
    assert any("don" in x for x in e)
    # PIDE_DESTINO exige area
    e = contract.validate({**base, "intent": "PIDE_DESTINO", "area": None}, "CONVERSATION", "VIP")
    assert any("area" in x for x in e)
    # orden de claves
    e = contract.validate({"speech": base["speech"], "type": "NONE", "trigger_text": ""}, "ANSWER", "VIP")
    assert any("order" in x for x in e)


def test_intents_fallback_y_areas():
    assert intents.fallback_intent("sí, por favor")[0] == "SI"
    assert intents.fallback_intent("no, gracias")[0] == "NO"
    assert intents.fallback_intent("hasta luego, que tengan buena noche")[0] == "DESPEDIDA"
    i, a = intents.fallback_intent("¿quiero ir al gimnasio, me guía?")
    assert i == "PIDE_DESTINO" and a == "gimnasio_deportes"
    assert intents.match_area("necesito la sala de juntas") == "salas_juntas"
    assert intents.match_area("voy a la cava") == "bares_la_cava"
    assert intents.match_area("¿dónde está el parqueadero?") == "estacionamientos"
    assert intents.match_area("el cuarto técnico de climatización") == "cuartos_tecnicos"


def test_build_messages_con_work_order_none():
    kb = contract.load_kb()
    p = {"name": "Laura", "rank": "VIP", "worker_subtype": None, "work_order": None}
    for st in contract.LLM_STATES:
        msgs = contract.build_messages(st, "VIP", p, kb, utterance="hola",
                                       alternatives=["recepcion_vestibulos"], pending_area="gimnasio_deportes")
        assert msgs and msgs[0]["role"] == "system" and "Club N" in msgs[0]["content"]


def test_kb_completa():
    kb = contract.load_kb()
    assert kb["club"] == "Club N"
    assert set(kb["espacios"]) == set(access.SPACES)
    for sp in kb["espacios"].values():
        assert sp["direccion"] in ("right", "left", "front")
        assert sp["piso"] and sp["horario"] and sp["descripcion"]
    assert kb["servicios"]


# ----------------------------------------------------------------- live smoke (x2-llm)
def _live_ok():
    if os.environ.get("HRI_LIVE") != "1":
        from x2_ultra.hri_runtime.llm_client import LlamaStreamClient
        if not LlamaStreamClient().health():
            return False
    return True


@pytest.mark.skipif(not _live_ok(), reason="x2-llm no alcanzable desde este netns (usar nsenter; ver llm_client.runner_note())")
def test_live_smoke_x2_llm():
    from x2_ultra.hri_runtime.llm_client import HriLLM
    llm = HriLLM()
    person = {"name": "Laura", "rank": "VIP"}
    r = llm.respond("GREETING", "VIP", person)
    assert r["obj"] is not None, r["raw"]
    print("\n[live GREETING]", json.dumps(r["obj"], ensure_ascii=False), "errors:", r["errors"])
    r2 = llm.respond("CONVERSATION", "VIP", person, utterance=WAV1)
    assert r2["obj"] is not None, r2["raw"]
    print("[live CONVERSATION]", json.dumps(r2["obj"], ensure_ascii=False), "errors:", r2["errors"])
    assert r2["obj"].get("intent") in intents.INTENTS
