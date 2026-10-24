"""rcp01c.intervention.v2: FSM fixes the communicative act, the LLM picks the gesture inside a per-state enum."""
from __future__ import annotations

import json
import math
import re
import unicodedata
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "rcp01c.intervention.v2"  # v2.1 contract fixes keep the same wire format
CATALOG = json.loads((Path(__file__).with_name("catalog.json")).read_text(encoding="utf-8"))
EMOTES = CATALOG["emotes"]
RANKS = ("VIP", "REGULAR", "GUEST", "STAFF", "EXTERNAL")
ACT_BY_STATE = {"GREETING": "GREETING", "GUIDANCE": "GUIDE", "DIALOGUE": "RESPOND", "FAREWELL": "CLOSING"}
DIALOGUE_ENUM = ["NEGATION_NORMAL", "NEGATION_STRONG", "CONFIRMATION", "THUMBS_UP", "SOCIAL_HEART", "APPLAUSE"]
FRONT_CUES = ("frente a mí", "frente a mi", "delante de mí", "delante de mi", "al frente")
STAFF_PHRASE = "¡dame esos cinco!"
STAFF_TRIGGER = "dame esos cinco"
MAX_PER_VISIT = {"SOCIAL_HEART": 2, "APPLAUSE": 2}
LEXICON = CATALOG["anchor_lexicon"]
DIRECTION_TO_TYPE = {"right": "POINT_RIGHT", "left": "POINT_LEFT", "front": "POINT_FRONT"}
TRIGGER_MAX_WORDS = 4
SPEECH_MIN_WORDS = 6
STRIP_CHARS = " \t\n¡!¿?,.;:"
RANK_LEAK_RE = re.compile(r"(?<!\w)(vip|staff|guest|regular|external|rango|rank|agente)(?!\w)", re.I)
VISITOR_PERSPECTIVE_RE = re.compile(
    r"\b(a|en) (su|tu) (derecha|izquierda)\b|\ba la (derecha|izquierda) (del|de el) agente\b|\ba la (derecha|izquierda) de (usted|ti)\b", re.I)


def _fold(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", t.casefold()) if unicodedata.category(c) != "Mn")


def lexicon_key(kind: str, state: str) -> str | None:
    if kind == "GREETING_STAFF":
        return "GREETING_STAFF"
    if kind.startswith("GREETING_"):
        return "GREETING"
    if kind == "FAREWELL_WAVE" or (kind == "SOCIAL_HEART" and state == "FAREWELL"):
        return "FAREWELL"
    return kind if kind in LEXICON else None


def lexicon_hit(trigger: str, kind: str, state: str) -> bool:
    key = lexicon_key(kind, state)
    if key is None:
        return False
    tl, tf = trigger.casefold(), _fold(trigger)
    for cue in LEXICON[key]:
        if cue == "sí":  # accent matters: 'si' is the conditional
            if re.search(r"(?<!\w)sí(?!\w)", tl):
                return True
        elif re.search(r"(?<!\w)" + re.escape(_fold(cue)) + r"(?!\w)", tf):
            return True
    return False


def strip_trigger(trig: str) -> str:
    return trig.strip(STRIP_CHARS)


def find_span(speech: str, trig: str) -> tuple[int, int] | None:
    """First occurrence of trig in speech that starts and ends on word boundaries."""
    if not trig:
        return None
    i = speech.find(trig)
    while i >= 0:
        j = i + len(trig)
        if (i == 0 or not speech[i - 1].isalnum()) and (j == len(speech) or not speech[j].isalnum()):
            return i, j
        i = speech.find(trig, i + 1)
    return None


def speech_word_count(speech: str) -> int:
    return len(re.findall(r"\w+", speech))


def allowed_types(state: str, rank: str | None) -> list[str]:
    """Allowed non-NONE gesture types for a state (NONE = empty motor_intents)."""
    if state == "GREETING":
        return [f"GREETING_{rank}"]
    if state == "GUIDANCE":
        return ["POINT_RIGHT", "POINT_LEFT", "POINT_FRONT"]
    if state == "DIALOGUE":
        return list(DIALOGUE_ENUM)
    if state == "FAREWELL":
        return ["SOCIAL_HEART"] if rank == "VIP" else ["FAREWELL_WAVE"]
    raise ValueError(state)


def none_allowed(state: str, rank: str | None) -> bool:
    return not (state == "GUIDANCE" or (state == "FAREWELL" and rank == "VIP"))


def build_schema(state: str, turn_id: str, scene_id: str, rank: str | None) -> dict[str, Any]:
    types = allowed_types(state, rank)

    def intent(kind: str) -> dict[str, Any]:
        props = {"event_id": {"const": turn_id}, "type": {"const": kind},
                 "trigger_text": {"type": "string", "minLength": 3}}  # length (1-4 words) enforced in code, not by grammar
        req = ["event_id", "type", "trigger_text"]
        if kind.startswith("POINT_"):
            props["reference_frame"] = {"const": "X2_base_local"}
            props["target_vector"] = {"const": EMOTES[kind]["target_vector"]}
            req += ["reference_frame", "target_vector"]
        return {"type": "object", "additionalProperties": False, "required": req, "properties": props}

    if none_allowed(state, rank):
        # explicit NONE choice (type="NONE", trigger_text=""): an empty-array option was never chosen by the 4B model
        item = intent(types[0])
        item["properties"]["type"] = {"enum": types + ["NONE"]}
        item["properties"]["trigger_text"] = {"type": "string"}
    elif len(types) == 1:
        item = intent(types[0])
    else:
        item = {"anyOf": [intent(t) for t in types]}
    return {"type": "object", "additionalProperties": False,
            "required": ["schema_version", "scene_id", "utterance_id", "speech", "communicative_acts", "motor_intents"],
            "properties": {"schema_version": {"const": SCHEMA_VERSION},
                           "scene_id": {"const": scene_id},
                           "utterance_id": {"const": turn_id},
                           "speech": {"type": "string", "minLength": 1},
                           "communicative_acts": {"type": "array", "minItems": 1, "maxItems": 1,
                                                  "items": {"const": ACT_BY_STATE[state]}},
                           "motor_intents": {"type": "array", "items": item,
                                             "minItems": 1, "maxItems": 1}}}


def _quoted(speech: str, start: int) -> bool:
    before = speech[:start]
    o = max(before.rfind("«"), before.rfind("“"), before.rfind("‘"))
    c = max(before.rfind("»"), before.rfind("”"), before.rfind("’"))
    return o > c or before.count('"') % 2 == 1


def _negated(speech: str, start: int) -> bool:
    left = " " + speech.casefold()[max(0, start - 48):start]
    return any(m in left for m in (" no ", " nunca ", " ni ", " tampoco "))


def _norm_words(t: str) -> list[str]:
    return re.findall(r"\w+", _fold(t))


def repeats_speech(speech: str, prev: str | None, threshold: float = 0.6) -> bool:
    """True if `speech` (near-)duplicates `prev` (SequenceMatcher ratio on folded words, or word-set Jaccard)."""
    if not prev:
        return False
    a, b = _norm_words(speech), _norm_words(prev)
    if not a or not b:
        return False
    import difflib
    jac = len(set(a) & set(b)) / len(set(a) | set(b))
    return difflib.SequenceMatcher(None, a, b).ratio() >= threshold or jac >= threshold


def validate(value: Any, *, state: str, turn_id: str, scene_id: str, rank: str | None,
             scenario_direction: str | None = None, prev_guidance_speech: str | None = None) -> list[str]:
    """Return a list of hard errors ([] = valid)."""
    errs: list[str] = []
    if not isinstance(value, dict):
        return ["not a JSON object"]
    req = {"schema_version", "scene_id", "utterance_id", "speech", "communicative_acts", "motor_intents"}
    if set(value) != req:
        errs.append(f"keys must be exactly {sorted(req)}")
    if value.get("schema_version") != SCHEMA_VERSION:
        errs.append("schema_version")
    if value.get("scene_id") != scene_id:
        errs.append("scene_id mismatch")
    if value.get("utterance_id") != turn_id:
        errs.append("utterance_id mismatch")
    speech = value.get("speech")
    if not isinstance(speech, str) or not speech.strip():
        errs.append("speech empty")
        speech = ""
    else:
        if speech_word_count(speech) < SPEECH_MIN_WORDS:
            errs.append(f"speech must have at least {SPEECH_MIN_WORDS} words")
        m = RANK_LEAK_RE.search(speech)
        if m:
            errs.append(f"speech must not mention rank names/codes or 'agente' (found {m.group(0)!r})")
        if state == "DIALOGUE" and repeats_speech(speech, prev_guidance_speech):
            errs.append("DIALOGUE speech repeats the previous GUIDANCE sentence; answer the visitor_utterance directly instead")
        if VISITOR_PERSPECTIVE_RE.search(speech):
            errs.append("speech uses visitor-relative/third-person directions; use first person ('a mi derecha')")
    if value.get("communicative_acts") != [ACT_BY_STATE[state]]:
        errs.append(f"communicative_acts must be [{ACT_BY_STATE[state]}] (set by the FSM)")
    intents = value.get("motor_intents")
    if not isinstance(intents, list) or len(intents) > 1:
        errs.append("motor_intents must be an array with 0..1 items")
        intents = []
    if not intents and not none_allowed(state, rank):
        errs.append(f"state {state}/{rank} requires one motor intent")
    types = allowed_types(state, rank)
    for it in intents:
        if not isinstance(it, dict):
            errs.append("intent not object"); continue
        kind = it.get("type")
        base = {"event_id", "type", "trigger_text"}
        keys = base | ({"reference_frame", "target_vector"} if str(kind).startswith("POINT_") else set())
        if set(it) != keys:
            errs.append(f"intent keys must be exactly {sorted(keys)}")
        if it.get("event_id") != turn_id:
            errs.append("event_id must equal turn_id")
        if kind not in types:
            errs.append(f"type {kind!r} not allowed in {state} (enum {types})")
            continue
        trig = it.get("trigger_text")
        if not isinstance(trig, str) or not trig.strip():
            errs.append("trigger_text empty")
            continue
        trig = strip_trigger(trig)
        if not trig or trig not in speech:
            errs.append("trigger_text must be a literal substring of speech")
            continue
        if find_span(speech, trig) is None:
            errs.append("trigger_text must start and end on whole words of speech (no cut words)")
            continue
        nw = len(trig.split())
        if not 1 <= nw <= TRIGGER_MAX_WORDS:
            errs.append(f"trigger_text must have 1-{TRIGGER_MAX_WORDS} words (has {nw})")
        if not lexicon_hit(trig, kind, state):
            errs.append(f"trigger_text must contain an anchor cue of {kind}: {LEXICON[lexicon_key(kind, state)]}")
        if kind.startswith("POINT_"):
            if it.get("reference_frame") != "X2_base_local":
                errs.append("reference_frame must be X2_base_local")
            vec = it.get("target_vector")
            if (not isinstance(vec, list) or len(vec) != 3 or any(
                    not isinstance(x, (int, float)) or isinstance(x, bool) or not math.isfinite(x) for x in vec)
                    or any(abs(a - b) > 1e-8 for a, b in zip(vec, EMOTES[kind]["target_vector"]))):
                errs.append("target_vector contradicts the X2_base_local convention")
            tl = trig.casefold()
            if any(x in tl for x in ("tu derecha", "su derecha", "tu izquierda", "su izquierda")):
                errs.append("visitor-relative perspective rejected")
            if scenario_direction and DIRECTION_TO_TYPE[scenario_direction] != kind:
                errs.append(f"type {kind} contradicts the scenario direction {scenario_direction}")
        if state == "GREETING" and rank == "STAFF" and kind == "GREETING_STAFF":
            if trig.casefold() != STAFF_TRIGGER:
                errs.append("STAFF trigger_text must be 'dame esos cinco'")
    if state == "GREETING" and rank == "STAFF":
        if STAFF_PHRASE not in speech.casefold():
            errs.append("STAFF speech must contain '¡dame esos cinco!'")
        if not intents:
            errs.append("STAFF greeting needs GREETING_STAFF")
    if state == "GUIDANCE" and not intents:
        errs.append("GUIDANCE needs a POINT intent")
    return errs


def intent_type(value: dict | None) -> str:
    it = (value or {}).get("motor_intents") or []
    return it[0].get("type", "INVALID") if len(it) == 1 and isinstance(it[0], dict) else "NONE"


class VisitGestureManager:
    """Code-side context manager: gates validated intents (literal/non-negated/non-quoted, cooldown, caps)."""

    def __init__(self) -> None:
        self.last: str | None = None
        self.counts: dict[str, int] = {}

    def decide(self, parsed: dict | None, errors: list[str], state: str | None = None) -> dict[str, Any]:
        """FSM-mandated gestures (rank greeting, VIP farewell SOCIAL_HEART) are exempt from cooldown and caps;
        the cooldown/caps only gate the optional DIALOGUE choice."""
        out = {"requested": intent_type(parsed) if parsed else "INVALID", "effective": "NONE",
               "suppressed": None, "trigger_text": None, "trigger_char_start": None, "trigger_char_end": None}
        if errors:
            out["suppressed"] = "validation_failed"
            self.last = None  # no gesture was played
            return out
        it = (parsed.get("motor_intents") or [None])[0]
        if not it:
            self.last = None
            return out
        speech, trig, kind = parsed["speech"], it["trigger_text"], it["type"]
        trig = strip_trigger(trig)
        span = find_span(speech, trig)
        start = span[0] if span else -1
        out.update(trigger_text=trig, trigger_char_start=start, trigger_char_end=start + len(trig))
        reason = None
        mandated = kind.startswith("GREETING") or (state == "FAREWELL" and kind == "SOCIAL_HEART")
        out["fsm_mandated"] = mandated
        if start < 0:
            reason = "trigger_not_literal"
        elif not kind.startswith("GREETING") and _negated(speech, start) and kind not in ("NEGATION_NORMAL", "NEGATION_STRONG"):
            reason = "trigger_negated"
        elif _quoted(speech, start):
            reason = "trigger_quoted"
        elif mandated:
            pass
        elif kind == self.last and kind not in ("GREETING_STAFF",):
            reason = "cooldown_same_emote_consecutive"
        elif kind in MAX_PER_VISIT and self.counts.get(kind, 0) >= MAX_PER_VISIT[kind]:
            reason = f"cap_{kind}_{MAX_PER_VISIT[kind]}_per_visit"
        if reason:
            out.update(suppressed=reason, trigger_text=None, trigger_char_start=None, trigger_char_end=None)
            self.last = None
            return out
        out["effective"] = kind
        self.last = kind
        self.counts[kind] = self.counts.get(kind, 0) + 1
        return out


def normalize(parsed: Any) -> Any:
    """Encode type=NONE as motor_intents=[] (the contract's canonical NONE); strip edge punctuation of trigger_text."""
    if isinstance(parsed, dict) and isinstance(parsed.get("motor_intents"), list):
        its = parsed["motor_intents"]
        if len(its) == 1 and isinstance(its[0], dict) and isinstance(its[0].get("trigger_text"), str) and its[0].get("type") != "NONE":
            trig = strip_trigger(its[0]["trigger_text"])
            sp = parsed.get("speech")
            if isinstance(sp, str) and trig and trig not in sp:  # 4B model lowercases 'Buenos días': snap to the speech's own casing
                i = sp.casefold().find(trig.casefold())
                if i >= 0 and len(sp.casefold()) == len(sp):
                    trig = sp[i:i + len(trig)]
            its = [dict(its[0], trigger_text=trig)]
            parsed = dict(parsed, motor_intents=its)
        if len(its) == 1 and isinstance(its[0], dict) and its[0].get("type") == "NONE":
            parsed = dict(parsed, motor_intents=[])
    return parsed
