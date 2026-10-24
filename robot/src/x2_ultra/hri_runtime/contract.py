"""Contrato B del Club N (T1): el LLM genera solo campos planos por estado; el
código inyecta constantes y valida. Orden de campos: type → speech → trigger_text
(+ intent, area en CONVERSATION). Ideas de HRIEGO_gesture/opt/contract_fix/ y
opt/personas/ (read-only); el orden del schema es respetado por llama.cpp
(verified en opt/t1_schema_stream/REPORT.md).

Estados LLM: GREETING, DESTINO_DE_ROL, CONVERSATION, ANSWER, ACCESS_DENIAL,
GUIDANCE_OFFER, POINT_GUIDE, FAREWELL. La FSM fija el acto; el LLM elige texto y
gesto dentro del enum del estado.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

from .access import SPACES, normalize_rank
from .intents import INTENTS
from .personas import LENGTH_RANGE, card_text, fewshot_text, titulo_para, trato_vip

# ---------------------------------------------------------------- catálogo de gestos
# motion_id/area reutilizados de opt/contract_fix/catalog_fix.json (presets X2).
GESTURES: dict[str, dict[str, Any]] = {
    "GREETING_VIP": {"motion_id": 3001, "area": 11, "kind": "preset"},
    "GREETING_REGULAR": {"motion_id": 1003, "area": 2, "kind": "preset"},
    "GREETING_GUEST": {"motion_id": 1011, "area": 2, "kind": "preset"},
    "GREETING_STAFF": {"motion_id": 1008, "area": 2, "kind": "preset", "note": "mano abierta; invita a chocar esos cinco"},
    "GREETING_WORKER": {"motion_id": 1002, "area": 2, "kind": "preset"},
    "FAREWELL_WAVE": {"motion_id": 3031, "area": 11, "kind": "preset"},
    "SOCIAL_HEART": {"motion_id": 3004, "area": 11, "kind": "preset"},
    "APPLAUSE": {"motion_id": 3017, "area": 11, "kind": "preset"},
    "NEGATION_NORMAL": {"motion_id": 3029, "area": 11, "kind": "preset"},
    "NEGATION_STRONG": {"motion_id": 3009, "area": 11, "kind": "preset"},
    "CONFIRMATION": {"motion_id": 3028, "area": 11, "kind": "preset"},
    "THUMBS_UP": {"motion_id": 1005, "area": 2, "kind": "preset"},
    "POINT_RIGHT": {"motion_id": None, "area": None, "kind": "external_pointing", "target_vector": [0.0, -1.0, 0.0]},
    "POINT_LEFT": {"motion_id": None, "area": None, "kind": "external_pointing", "target_vector": [0.0, 1.0, 0.0]},
    "POINT_FRONT": {"motion_id": None, "area": None, "kind": "external_pointing", "target_vector": [1.0, 0.0, 0.0]},
    "NONE": {"motion_id": None, "area": None, "kind": "none"},
}
DIRECTION_TO_TYPE = {"right": "POINT_RIGHT", "left": "POINT_LEFT", "front": "POINT_FRONT"}
TYPE_TO_DIRECTION = {v: k for k, v in DIRECTION_TO_TYPE.items()}

LLM_STATES = ("GREETING", "DESTINO_DE_ROL", "CONVERSATION", "ANSWER", "ACCESS_DENIAL",
              "GUIDANCE_OFFER", "POINT_GUIDE", "FAREWELL")
DIALOGUE_GESTURES = ["NONE", "CONFIRMATION", "THUMBS_UP", "SOCIAL_HEART"]

SPEECH_MIN_WORDS = 6
SPEECH_MAX_WORDS = 30
TRIGGER_MAX_WORDS = 4
BASE_FIELDS = ("type", "speech", "trigger_text")
CONVERSATION_FIELDS = BASE_FIELDS + ("intent", "area")

DON_DONA_RE = re.compile(r"(?<!\w)(don|doña|dona)\s+(?=[a-záéíóúñ])", re.I)
RANK_LEAK_RE = re.compile(r"(?<!\w)(vip|staff|guest|regular|worker|external|rango|rank)(?!\w)", re.I)
TU_RE = re.compile(r"(?<!\w)(tú|tu|te|ti|contigo|date)\b", re.I)
USTED_RE = re.compile(r"(?<!\w)(usted|ustedes|le|les|su|sus|atenderle|que tenga|cuídese|cuidese)\b", re.I)
STAFF_FAREWELL_ANCHOR_RE = re.compile(r"(?<!\w)(nos vemos|hasta luego|adiós|adios)(?!\w)", re.I)
_STRIP = " \t\n¡!¿?,.;:…"


# ---------------------------------------------------------------- KB
def load_kb(path: Optional[Path] = None) -> dict:
    p = Path(path) if path else Path(__file__).with_name("club_kb.json")
    return json.loads(p.read_text(encoding="utf-8"))


def kb_text(kb: dict, spaces: Optional[list[str]] = None, include_services: bool = True) -> str:
    """Contexto compacto de la KB para el prompt (solo lo relevante)."""
    dir_es = {"right": "a mi derecha", "left": "a mi izquierda", "front": "al frente"}
    lines = [f"Club: {kb['club']} ({kb['ciudad']}). Datos sintéticos fijos; responde SOLO con esto:"]
    for sid, sp in kb["espacios"].items():
        if spaces and sid not in spaces:
            continue
        lines.append(f"- {sp['nombre']} [{sid}]: queda {dir_es.get(sp['direccion'], sp['direccion'])}, piso {sp['piso']}, "
                     f"horario {sp['horario']}. {sp['descripcion']}")
    if include_services:
        for sv in kb.get("servicios", []):
            if spaces and sv.get("ubicacion") not in spaces and sv.get("ubicacion") is not None:
                continue
            lines.append(f"- Servicio {sv['nombre']}: {sv['descripcion']}")
    return "\n".join(lines)


# ---------------------------------------------------------------- schema por estado
def allowed_types(state: str, rank: Optional[str] = None, direction: Optional[str] = None) -> list[str]:
    r, _ = normalize_rank(rank) if rank else (None, None)
    if state == "GREETING":
        return [f"GREETING_{r}"]
    if state in ("DESTINO_DE_ROL", "POINT_GUIDE"):
        if direction:
            return [DIRECTION_TO_TYPE[direction]]
        return ["POINT_RIGHT", "POINT_LEFT", "POINT_FRONT"]
    if state == "CONVERSATION" or state == "ANSWER":
        return list(DIALOGUE_GESTURES)
    if state == "ACCESS_DENIAL":
        return ["NEGATION_NORMAL"]
    if state == "GUIDANCE_OFFER":
        return ["NONE", "CONFIRMATION"]
    if state == "FAREWELL":
        return ["SOCIAL_HEART"] if r == "VIP" else ["FAREWELL_WAVE"]
    raise ValueError(f"estado desconocido: {state!r}")


def none_allowed(state: str) -> bool:
    return state in ("CONVERSATION", "ANSWER", "GUIDANCE_OFFER")


def json_schema(state: str, rank: Optional[str] = None, direction: Optional[str] = None) -> dict[str, Any]:
    """json_schema (strict) para llama-server. Orden de propiedades = orden de generación."""
    types = allowed_types(state, rank, direction)
    if len(types) == 1:
        type_s: dict = {"const": types[0]} if types[0] != "NONE" else {"const": "NONE"}
    else:
        type_s = {"enum": types}
    trig: dict = {"type": "string"} if none_allowed(state) else {"type": "string", "minLength": 3}
    props: dict[str, Any] = {
        "type": type_s,
        "speech": {"type": "string", "minLength": 1},
        "trigger_text": trig,
    }
    required = list(BASE_FIELDS)
    if state == "CONVERSATION":
        # intent y area van DESPUÉS del trío base (el gesto se conoce primero: latencia)
        props["intent"] = {"enum": list(INTENTS)}
        props["area"] = {"enum": list(SPACES) + [None]}
        required = list(CONVERSATION_FIELDS)
    return {"type": "object", "additionalProperties": False, "required": required, "properties": props}


# ---------------------------------------------------------------- validador
def _words(s: str) -> int:
    return len(re.findall(r"\w+", s or ""))


def _literal_trigger(trigger: str, speech: str) -> bool:
    t = trigger.strip(_STRIP).lower()
    s = speech.lower()
    if not t:
        return False
    return re.search(r"(?<!\w)" + re.escape(t) + r"(?!\w)", s) is not None


def repair_trigger(obj: Any) -> Any:
    """Arregla en local solo ``trigger_text`` (el ancla del gesto, no lo que se dice): vacío con
    type=NONE; si no, hasta TRIGGER_MAX_WORDS palabras literales de la frase. En el robot, estos
    dos fallos del validador provocaban un reintento completo del LLM (el doble de latencia)."""
    if not isinstance(obj, dict):
        return obj
    out = dict(obj)
    speech = out.get("speech") if isinstance(out.get("speech"), str) else ""
    trig = out.get("trigger_text") if isinstance(out.get("trigger_text"), str) else ""
    if out.get("type") == "NONE":
        out["trigger_text"] = ""
        return out
    if trig.strip() and _words(trig) <= TRIGGER_MAX_WORDS and _literal_trigger(trig, speech):
        return out
    candidates = []
    toks = trig.split()
    for k in range(min(TRIGGER_MAX_WORDS, len(toks)), 0, -1):
        candidates += [" ".join(toks[:k]), " ".join(toks[-k:])]
    stoks = speech.split()
    for k in range(min(TRIGGER_MAX_WORDS, len(stoks)), 0, -1):
        candidates.append(" ".join(stoks[:k]))
    for c in candidates:
        c = c.strip(_STRIP)
        if c and _words(c) <= TRIGGER_MAX_WORDS and _literal_trigger(c, speech):
            out["trigger_text"] = c
            return out
    return out


def validate(obj: Any, state: str, rank: str, direction: Optional[str] = None,
             worker_subtype: Optional[str] = None) -> list[str]:
    """Lista de errores (vacía = válido). Reglas: orden/keys, enums, trigger literal,
    consistencia de tratamiento por persona, VIP sin exclamaciones, reglas de persona."""
    errs: list[str] = []
    r, _ = normalize_rank(rank)
    if not isinstance(obj, dict):
        return ["obj must be a dict"]
    expected_keys = list(CONVERSATION_FIELDS if state == "CONVERSATION" else BASE_FIELDS)
    keys = list(obj)
    if set(keys) != set(expected_keys):
        errs.append(f"keys must be exactly {expected_keys}, got {keys}")
    elif keys != expected_keys:
        errs.append(f"key order must be {' > '.join(expected_keys)}, got {' > '.join(keys)}")

    typ = obj.get("type")
    allowed = allowed_types(state, r, direction)
    if typ not in allowed:
        errs.append(f"type {typ!r} not allowed in {state} (allowed: {allowed})")
    if state in ("DESTINO_DE_ROL", "POINT_GUIDE") and direction and typ != DIRECTION_TO_TYPE[direction]:
        errs.append(f"type {typ!r} must match direction {direction!r} → {DIRECTION_TO_TYPE[direction]}")

    speech = obj.get("speech")
    if not isinstance(speech, str) or not speech.strip():
        errs.append("speech must be a non-empty string")
        speech = ""
    n = _words(speech)
    lo, hi = LENGTH_RANGE.get(r, (SPEECH_MIN_WORDS, SPEECH_MAX_WORDS))
    if n < SPEECH_MIN_WORDS:
        errs.append(f"speech too short ({n} < {SPEECH_MIN_WORDS} words)")
    if n > max(hi, SPEECH_MAX_WORDS):
        errs.append(f"speech too long ({n} > {max(hi, SPEECH_MAX_WORDS)} words)")

    trig = obj.get("trigger_text")
    if not isinstance(trig, str):
        errs.append("trigger_text must be a string")
        trig = ""
    if typ == "NONE":
        if trig != "":
            errs.append("trigger_text must be empty when type=NONE")
    else:
        if not trig.strip():
            errs.append("trigger_text must be non-empty when type != NONE")
        elif _words(trig) > TRIGGER_MAX_WORDS:
            errs.append(f"trigger_text too long ({_words(trig)} > {TRIGGER_MAX_WORDS} words)")
        elif not _literal_trigger(trig, speech):
            errs.append("trigger_text must be a literal whole-word substring of speech")

    # persona / tratamiento
    if DON_DONA_RE.search(speech):
        errs.append("prohibido usar «don»/«doña»")
    m = RANK_LEAK_RE.search(speech)
    if m:
        errs.append(f"rank leak in speech: {m.group(0)!r}")
    if r == "VIP":
        if "¡" in speech or "!" in speech:
            errs.append("VIP: prohibidos los signos de exclamación")
    if r == "STAFF":
        m = USTED_RE.search(speech)
        if m:
            errs.append(f"STAFF debe tutear; encontrado tratamiento de usted: {m.group(0)!r}")
    else:
        m = TU_RE.search(speech)
        if m:
            errs.append(f"{r} debe tratar de usted; encontrado tuteo: {m.group(0)!r}")
    if r == "STAFF" and state == "FAREWELL" and not STAFF_FAREWELL_ANCHOR_RE.search(speech):
        errs.append("STAFF FAREWELL debe incluir «nos vemos»/«hasta luego»/«adiós» (ancla del gesto)")

    # CONVERSATION: intent/area
    if state == "CONVERSATION":
        intent = obj.get("intent")
        area = obj.get("area")
        if intent not in INTENTS:
            errs.append(f"intent {intent!r} not in {list(INTENTS)}")
        if intent == "PIDE_DESTINO":
            if area not in SPACES:
                errs.append(f"PIDE_DESTINO requiere area válida de la tabla, got {area!r}")
        elif area is not None and area not in SPACES:
            errs.append(f"area must be null or a space id, got {area!r}")
    return errs


# ---------------------------------------------------------------- prompts
def build_messages(state: str, rank: str, person: Optional[dict], kb: dict, *,
                   utterance: Optional[str] = None, alternatives: Optional[list[str]] = None,
                   pending_area: Optional[str] = None, extra_context: Optional[str] = None) -> list[dict[str, str]]:
    """Mensajes chat para el LLM: persona + few-shot + contrato + KB + turno."""
    r, _st = normalize_rank(rank)
    name = (person or {}).get("name")
    sys_parts = [card_text(r, name, titulo_para(person))]
    fs = fewshot_text(r, state)
    if fs:
        sys_parts.append(fs)
    spaces_ctx = None
    if state == "ACCESS_DENIAL" and alternatives:
        spaces_ctx = list(alternatives)
    if state in ("POINT_GUIDE", "DESTINO_DE_ROL", "GUIDANCE_OFFER") and pending_area:
        spaces_ctx = [pending_area]
    sys_parts.append(kb_text(kb, spaces_ctx))
    fields = ", ".join(CONVERSATION_FIELDS if state == "CONVERSATION" else BASE_FIELDS)
    sys_parts.append(
        f"CONTRATO: devuelve SOLO un objeto JSON con los campos en este orden: {fields}. "
        "type = gesto del enum del estado; speech = frase natural completa (>= 6 palabras) en español; "
        "trigger_text = subcadena literal de speech (1-4 palabras, vacío si type=NONE)"
        + ("; intent = intención del visitante; area = espacio de la tabla o null." if state == "CONVERSATION" else "."))
    pd_dir =(kb["espacios"].get(pending_area or "", {}).get("direccion")) if pending_area else \
             ((person or {}).get("work_order_direction") or "")
    pd_dir_es = {"right": "a mi derecha", "left": "a mi izquierda", "front": "al frente"}.get(pd_dir or "", pd_dir or "")
    instr = {
        "GREETING": ("Saluda a la persona que acaba de llegar (acto GREETING) y hazle sentir parte de nuestro club "
                     "(PERTENENCIA de la identidad: «nuestro lugar de encuentro», «su casa», «nuestra comunidad»)."
                     + ("" if r in ("STAFF", "WORKER") else
                        " Termina SIEMPRE con UNA pregunta abierta que le invite a responder o a contarte a qué viene "
                        "(p. ej. «¿qué le trae hoy por el club?», «¿en qué puedo ayudarle?»); la pregunta va al final.")),
        "DESTINO_DE_ROL": (f"Indica proactivamente su destino asignado ({(((person or {}).get('work_order')) or {}).get('nombre_area', '') or ((person or {}).get('work_order') or {}).get('area', '')}), "
                           f"que queda {pd_dir_es} (acto GUIDE, POINT obligatorio; di la dirección en español). "
                           "Termina con UNA pregunta breve que le invite a responder (p. ej. si necesita algo más)."),
        "CONVERSATION": (
            "Clasifica la intención del visitante: PREGUNTA = pregunta por datos, horarios o servicios de la KB; "
            "PIDE_DESTINO = pide EXPLÍCITAMENTE ir/llegar/pasar a un espacio concreto o que le acompañen/guíen "
            "(«quiero ir a X», «¿puedo ir a X?», «llévame a X») (pon su area); "
            "OJO: pedir información («dame información de X», «¿qué opciones hay?», «¿a dónde podría ir?», "
            "expresar un deseo de actividad como «me gustaría hacer ejercicio») es PREGUNTA, nunca PIDE_DESTINO; "
            "SI / NO = respuesta corta afirmativa o negativa; DESPEDIDA = se despide o cierra la visita; OTRO = charla. "
            "Si es PREGUNTA/SI/NO/OTRO responde con la KB (acto RESPOND): la frase DEBE incluir el dato concreto "
            "(nombre del espacio, piso u horario); si pregunta a dónde podría ir o qué opciones hay, cita de 2 a 4 "
            "espacios de la KB por su nombre. Los «cuartos del staff» son las oficinas administrativas "
            "(oficinas_administrativas). Si es PIDE_DESTINO o DESPEDIDA escribe solo "
            "una frase breve de transición (la FSM decidirá el acto siguiente). "
            "Nunca menciones ids internos (p. ej. «gimnasio_deportes», «left»): usa los nombres en español de la KB. "
            f"Visitante dice: «{utterance}»"),
        "ANSWER": f"Responde la pregunta SOLO con la KB, sin ofrecer guía ni señalar (acto RESPOND). Visitante dice: «{utterance}»",
        "ACCESS_DENIAL": ("El espacio pedido no corresponde a esta persona. Gesto NEGATION_NORMAL, pero la frase empieza "
                          "por lo que SÍ puede vivir en nuestro club"
                          + (" (" + ", ".join(kb["espacios"].get(a, {}).get("nombre", a) for a in alternatives) + ")"
                             if alternatives else " (una alternativa de la KB)")
                          + " y solo menciona de pasada que ese espacio está reservado. Nunca «no está autorizado» ni "
                          "«solo para socios»."),
        "GUIDANCE_OFFER": ("Ofrece EXACTAMENTE UNA vez acompañar/guiar al espacio autorizado (acto GUIDE). "
                           + ("Fórmula: «¿Desea que le acompañe?»" if r == "VIP" else "Fórmula: «¿Desea que Draco le guíe?»")),
        "POINT_GUIDE": f"Da la indicación verbal del camino y señala (POINT obligatorio): el espacio queda {pd_dir_es}; di la dirección en español, nunca «left/right/front».",
        "FAREWELL": ("Despídete (acto CLOSING) invitándole a volver a nuestro club; que se sienta parte de nuestra "
                     "comunidad (PERTENENCIA de la identidad)."),
    }[state]
    if extra_context:
        sys_parts.append(extra_context)
    sys_parts.append(instr)
    if prefijo_estatico_activo():
        return _ordenar_por_volatilidad(r, name, state, kb, spaces_ctx, fs, sys_parts[-1], extra_context,
                                        titulo_para(person))
    return [{"role": "system", "content": "\n\n".join(p for p in sys_parts if p)}]


# ---------------------------------------------------------------- prefijo cacheable
CONTRATO_GENERAL = (
    "CONTRATO GENERAL: devuelve SOLO un objeto JSON plano con los campos que pida el turno, en ese orden; "
    "type = gesto del enum del estado; speech = frase natural completa (>= 6 palabras) en español; "
    "trigger_text = subcadena literal de speech (1-4 palabras, vacío si type=NONE).")


def prefijo_estatico_activo() -> bool:
    """HRI_PROMPT_PREFIJO_ESTATICO=1: el system prompt se ordena de lo más estable a lo más
    volátil (KB completa → tarjeta del rango → nombre → ejemplos/instrucción del estado) para
    que la caché de prefijo de llama-server (--cache-reuse) sirva entre estados y visitas.
    Por defecto (0) el prompt es byte a byte el de siempre."""
    import os
    return os.environ.get("HRI_PROMPT_PREFIJO_ESTATICO", "0") == "1"


def _ordenar_por_volatilidad(r: str, name: Optional[str], state: str, kb: dict,
                             spaces_ctx: Optional[list[str]], fs: str, instr: str,
                             extra_context: Optional[str], titulo: Optional[str] = None) -> list[dict[str, str]]:
    """Mismo contenido que el prompt clásico, reordenado; la KB va completa al principio
    (idéntica en todos los turnos) y el subconjunto de espacios del turno se cita al final."""
    card = card_text(r, None)                 # sin nombre: idéntica para todo el rango
    if name and r == "VIP":
        nombre = trato_vip(name, titulo)
    elif name:
        nombre = f"Nombre a usar: «{name}»."
    else:
        nombre = ""
    fields = ", ".join(CONVERSATION_FIELDS if state == "CONVERSATION" else BASE_FIELDS)
    parts = [kb_text(kb, None), CONTRATO_GENERAL,       # 1) estático (todas las visitas)
             card,                                      # 2) por rango
             nombre]                                    # 3) por visita
    if fs:                                              # 4) por estado / turno
        parts.append(fs)
    if spaces_ctx:
        nombres = [kb["espacios"].get(s, {}).get("nombre", s) for s in spaces_ctx]
        parts.append("ESPACIOS RELEVANTES PARA ESTE TURNO: " + ", ".join(nombres) + ".")
    parts.append(f"CAMPOS DE ESTE TURNO, en este orden: {fields}"
                 + ("; intent = intención del visitante; area = espacio de la tabla o null." if state == "CONVERSATION" else "."))
    if extra_context:
        parts.append(extra_context)
    # La instrucción del estado (con lo que dijo el visitante) va como mensaje user.
    return [{"role": "system", "content": "\n\n".join(p for p in parts if p)},
            {"role": "user", "content": instr}]
