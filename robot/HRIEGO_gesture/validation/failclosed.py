#!/usr/bin/env python3
"""4d fail-closed injection tests (offline: no twin, no LLM, no GPU).
Part A: real contract code (contract/schema.py: normalize, validate, VisitGestureManager) on mutated outputs.
Part B: real align/anchor_plan.py CLI via failclosed_anchor.py (physicalai python) on mutated manifests/latency tables.
Run: cd $X2_ULTRA_ROOT; PYTHONPATH=$PWD Face/.venv-face/bin/python HRIEGO_gesture/validation/failclosed.py"""
import json, subprocess
from pathlib import Path
from HRIEGO_gesture.contract.schema import VisitGestureManager, normalize, validate, EMOTES
from HRIEGO_gesture.paths import py_physicalai

H = Path(__file__).resolve().parent
TID = "t"
results = []

def mk(state, speech, typ, trig, rank="VIP"):
    it = {"event_id": TID, "type": typ, "trigger_text": trig}
    if typ.startswith("POINT_"):
        it.update(reference_frame="X2_base_local", target_vector=EMOTES[typ]["target_vector"])
    act = {"GREETING": "GREETING", "GUIDANCE": "GUIDE", "DIALOGUE": "RESPOND", "FAREWELL": "CLOSING"}[state]
    return {"schema_version": "rcp01c.intervention.v2", "scene_id": "E3", "utterance_id": TID, "speech": speech,
            "communicative_acts": [act], "motor_intents": [it]}

def case(name, state, rank, obj, expect_err=None, expect_effective="NONE", mgr=None, direction=None, expect_suppressed=None, expect_mandated=None, note=""):
    parsed = normalize(obj)
    errs = validate(parsed, state=state, turn_id=TID, scene_id="E3", rank=rank, scenario_direction=direction)
    m = mgr or VisitGestureManager()
    dec = m.decide(parsed, errs, state)
    ok = True
    if expect_err is not None:
        ok &= any(expect_err in e for e in errs)
    else:
        ok &= errs == []
    ok &= dec["effective"] == expect_effective
    if expect_suppressed: ok &= dec["suppressed"] == expect_suppressed
    if expect_mandated is not None: ok &= bool(dec.get("fsm_mandated")) == expect_mandated
    # zero dispatch <=> effective NONE
    results.append({"name": name, "layer": "contract", "state": state, "rank": rank, "expected_error": expect_err, "expected_effective": expect_effective,
                    "errors": errs, "requested": dec["requested"], "effective": dec["effective"], "suppressed": dec["suppressed"],
                    "fsm_mandated": dec.get("fsm_mandated"), "dispatch": dec["effective"] != "NONE", "note": note, "result": "PASS" if ok else "FAIL"})

SP = "Gracias, me alegra haber podido ayudarte"
case("trigger_not_in_speech", "DIALOGUE", "VIP", mk("DIALOGUE", SP, "SOCIAL_HEART", "te quiero mucho"), "literal substring", "NONE", expect_suppressed="validation_failed")
case("trigger_mid_word", "DIALOGUE", "VIP", mk("DIALOGUE", SP, "SOCIAL_HEART", "Graci"), "whole words", "NONE", expect_suppressed="validation_failed")
case("emote_not_allowed_GREETING_THUMBS_UP", "GREETING", "VIP", mk("GREETING", "Buenos días, Doctora. Bienvenida al laboratorio.", "THUMBS_UP", "Buenos días"), "not allowed in GREETING", "NONE")
case("emote_not_allowed_GREETING_other_rank", "GREETING", "REGULAR", mk("GREETING", "Buenos días, Pablo. Bienvenido al laboratorio.", "GREETING_VIP", "Buenos días", "REGULAR"), "not allowed in GREETING", "NONE")
case("emote_not_allowed_GUIDANCE_APPLAUSE", "GUIDANCE", "VIP", mk("GUIDANCE", "Felicidades, el laboratorio está a mi derecha", "APPLAUSE", "Felicidades"), "not allowed in GUIDANCE", "NONE", direction="right")
case("emote_not_allowed_FAREWELL_VIP_wave", "FAREWELL", "VIP", mk("FAREWELL", "Fue un placer atenderle hoy, hasta luego.", "FAREWELL_WAVE", "hasta luego"), "not allowed in FAREWELL", "NONE")
case("emote_not_allowed_FAREWELL_staff_heart", "FAREWELL", "STAFF", mk("FAREWELL", "Fue un placer atenderle hoy, hasta luego.", "SOCIAL_HEART", "hasta luego", "STAFF"), "not allowed in FAREWELL", "NONE")
case("rank_leak_in_speech", "DIALOGUE", "VIP", mk("DIALOGUE", "Gracias, me alegra ayudarte como invitada VIP hoy", "SOCIAL_HEART", "Gracias"), "rank names", "NONE")
case("rank_leak_agente", "DIALOGUE", "VIP", mk("DIALOGUE", "Gracias, el agente se alegra de ayudarte hoy", "SOCIAL_HEART", "Gracias"), "rank names", "NONE")
case("third_person_direction", "GUIDANCE", "VIP", mk("GUIDANCE", "El laboratorio está a su derecha, siga adelante", "POINT_RIGHT", "a su derecha"), "visitor-relative", "NONE", direction="right")
case("third_person_direction_tu_izquierda", "GUIDANCE", "REGULAR", mk("GUIDANCE", "La sala de juntas está en tu izquierda, adelante", "POINT_LEFT", "en tu izquierda", "REGULAR"), "visitor-relative", "NONE", direction="left")
case("NONE_with_stray_trigger_DIALOGUE", "DIALOGUE", "VIP", mk("DIALOGUE", "El laboratorio cierra a las dieciocho horas", "NONE", "Gracias"), None, "NONE",
     note="type NONE + non-empty trigger_text: normalize() encodes motor_intents=[]; trigger dropped, valid, no dispatch")
case("NONE_in_GUIDANCE_rejected", "GUIDANCE", "VIP", mk("GUIDANCE", "El laboratorio queda por allá, sígueme por favor", "NONE", ""), "requires one motor intent", "NONE", direction="right")
case("NONE_in_VIP_FAREWELL_rejected", "FAREWELL", "VIP", mk("FAREWELL", "Fue un placer atenderle hoy, hasta luego.", "NONE", ""), "requires one motor intent", "NONE")
# cooldown in DIALOGUE
m = VisitGestureManager()
d1 = m.decide(normalize(mk("DIALOGUE", SP, "SOCIAL_HEART", "Gracias")), [], "DIALOGUE")
case("cooldown_repeat_in_DIALOGUE", "DIALOGUE", "VIP", mk("DIALOGUE", "Muchas gracias, me alegra mucho tu visita hoy", "SOCIAL_HEART", "Muchas gracias"), None, "NONE", mgr=m, expect_suppressed="cooldown_same_emote_consecutive",
     note=f"first SOCIAL_HEART effective={d1['effective']}")
# cap 2 per visit
m = VisitGestureManager()
for typ_sp in (("APPLAUSE", "Felicidades por el logro de hoy, lo lograste", "Felicidades"), ("NEGATION_NORMAL", "No puedo darte la tarjeta de acceso hoy", "No puedo"),
               ("APPLAUSE", "Felicidades por el premio, es una gran noticia", "Felicidades"), ("NEGATION_NORMAL", "No puedo abrir la sala de servidores hoy", "No puedo")):
    m.decide(normalize(mk("DIALOGUE", typ_sp[1], typ_sp[0], typ_sp[2])), [], "DIALOGUE")
case("cap_APPLAUSE_third_in_visit", "DIALOGUE", "VIP", mk("DIALOGUE", "Felicidades por el ascenso, es una excelente noticia", "APPLAUSE", "Felicidades"), None, "NONE", mgr=m, expect_suppressed="cap_APPLAUSE_2_per_visit")
# VIP farewell exempt from cooldown AND cap
m = VisitGestureManager()
for _ in range(2):
    m.last = None
    m.decide(normalize(mk("DIALOGUE", SP, "SOCIAL_HEART", "Gracias")), [], "DIALOGUE")
m.last = "SOCIAL_HEART"  # last dialogue gesture was the same emote and the cap (2) is already reached
case("VIP_farewell_exempt_from_cooldown_and_cap", "FAREWELL", "VIP", mk("FAREWELL", "Fue un placer atenderle hoy, hasta luego y que tenga un excelente día.", "SOCIAL_HEART", "hasta luego"), None,
     "SOCIAL_HEART", mgr=m, expect_mandated=True, note="counts[SOCIAL_HEART]=2 and last=SOCIAL_HEART before the farewell")
case("POINT_without_direction_cue", "GUIDANCE", "VIP", mk("GUIDANCE", "El laboratorio está por ese pasillo de allá", "POINT_RIGHT", "por ese pasillo"), "anchor cue", "NONE", direction="right")
case("POINT_wrong_direction_vs_scenario", "GUIDANCE", "VIP", mk("GUIDANCE", "El laboratorio está a mi izquierda, adelante", "POINT_LEFT", "a mi izquierda"), "contradicts the scenario", "NONE", direction="right")
bad = mk("GUIDANCE", "El laboratorio está a mi derecha, adelante", "POINT_RIGHT", "a mi derecha"); bad["motor_intents"][0]["target_vector"] = [0.0, 1.0, 0.0]
case("POINT_target_vector_flipped", "GUIDANCE", "VIP", bad, "target_vector", "NONE", direction="right")
case("negated_trigger_suppressed", "DIALOGUE", "VIP", mk("DIALOGUE", "No es una buena idea, perfecto no es la palabra", "THUMBS_UP", "perfecto"), None, "NONE", expect_suppressed="trigger_negated",
     note="manager layer (validator passes lexicon 'perfecto'; negation 'no' within 48 chars)")
case("speech_too_short", "DIALOGUE", "VIP", mk("DIALOGUE", "Gracias, amigo", "SOCIAL_HEART", "Gracias"), "at least 6 words", "NONE")
case("STAFF_greeting_missing_phrase", "GREETING", "STAFF", mk("GREETING", "Buenos días, Carlos. Bienvenido al laboratorio.", "GREETING_STAFF", "Buenos días", "STAFF"), "dame esos cinco", "NONE")
for r in results[:]:
    pass
p = subprocess.run([py_physicalai(), str(H / "failclosed_anchor.py")], capture_output=True, text=True, timeout=1800)
anchor = json.loads(p.stdout) if p.returncode == 0 else [{"name": "anchor_suite", "result": "FAIL", "error": p.stderr[-500:]}]
for r in anchor:
    r["layer"] = "anchor_plan"; r["extra_probe"] = r["name"].startswith("probe_")
allr = results + anchor
req = [r for r in allr if not r.get("extra_probe")]
summary = {"n_required": len(req), "n_pass": sum(r["result"] == "PASS" for r in req), "n_fail": [r["name"] for r in req if r["result"] != "PASS"],
           "extra_probes": [{"name": r["name"], "result": r["result"], "note": r.get("note")} for r in allr if r.get("extra_probe")]}
json.dump({"summary": summary, "cases": allr}, open(H / "failclosed.json", "w"), indent=1, ensure_ascii=False)
print(json.dumps(summary, indent=1, ensure_ascii=False))
for r in allr: print(r["result"], r["layer"], r["name"], "|", r.get("effective", r.get("status")), "|", (r.get("errors") or [r.get("reason")])[:1])
