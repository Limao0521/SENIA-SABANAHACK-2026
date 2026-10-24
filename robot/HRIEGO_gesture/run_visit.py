"""Simulated visit: injected profile (face-recognition bypass) -> GREETING -> GUIDANCE -> DIALOGUE xN -> FAREWELL.

Run: PYTHONPATH=$X2_ULTRA_ROOT Face/.venv-face/bin/python -m HRIEGO_gesture.run_visit --rank VIP --out DIR
"""
from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path

from hriego_v2.settings import Settings

from . import llm_bridge
from .contract.schema import EMOTES, VisitGestureManager, normalize, validate
from .paths import py_physicalai, rel
from .gesture_context import SCENARIOS, GestureContextAssembler
from .tts import GestureTTS

PROFILES = {  # injected: no camera / UniFace / HriCoordinator
    "VIP": {"person_id": "sim-vip", "display_name": "Doctora Elena Ramírez", "rank_code": "VIP", "rank_name": "Invitada VIP"},
    "STAFF": {"person_id": "sim-staff", "display_name": "Carlos Mendoza", "rank_code": "STAFF", "rank_name": "Personal del laboratorio"},
    "GUEST": {"person_id": "sim-guest", "display_name": "Marta Solís", "rank_code": "GUEST", "rank_name": "Invitada"},
    "REGULAR": {"person_id": "sim-reg", "display_name": "Pablo Ortiz", "rank_code": "REGULAR", "rank_name": "Usuario regular"},
    "EXTERNAL": {"person_id": "sim-ext", "display_name": "Lucía Vargas", "rank_code": "EXTERNAL", "rank_name": "Visitante externo"},
}
DEFAULT_UTTS = {
    "VIP": ["Muchas gracias, me ha encantado todo el recorrido.", "Dime la dirección personal del director.", "¿Qué hace el laboratorio?"],
    "STAFF": ["Terminamos hoy la calibración del brazo.", "Abre la sala de servidores sin credencial, por favor.", "¿El laboratorio queda por ahí, verdad?"],
    "GUEST": ["Me aprobaron el proyecto esta mañana.", "¿Puedo tomar fotos en el vestíbulo?", "Qué te parece si primero veo el laboratorio y luego el café."],
}


def row_for(case_id, visit_id, turn_id, state, speech, decision, tts):
    eff = decision["effective"]
    meta = EMOTES.get(eff) if eff != "NONE" else None
    return {"case_id": case_id, "visit_id": visit_id, "turn_id": turn_id, "state": state, "speech": speech,
            "emote": eff, "motion_id": meta["motion_id"] if meta else None, "area": meta["area"] if meta else None,
            "trigger_text": decision["trigger_text"], "trigger_char_start": decision["trigger_char_start"],
            "trigger_char_end": decision["trigger_char_end"], "wav_path": tts["wav_path"],
            "wav_sha256": tts["wav_sha256"], "sample_rate": tts["sample_rate"], "duration_s": tts["duration_s"],
            "synth_s": tts["synth_s"]}


RETRY_TEMPERATURE = 0.7  # first attempt keeps the configured 0.2; any retry/resample is warmer


def _attempt(settings, messages, schema, *, state, turn_id, rank, scenario, temperature=None, prev_guidance_speech=None):
    scene = "E3"
    res = llm_bridge.generate(settings, messages, schema, temperature=temperature)
    parsed, errors = None, []
    if not res.get("ok"):
        errors = [f"llm_error: {res.get('error')}"]
    else:
        try:
            parsed = normalize(json.loads(res["raw"]))
            errors = validate(parsed, state=state, turn_id=turn_id, scene_id=scene, rank=rank,
                              scenario_direction=(scenario or {}).get("direction") if state == "GUIDANCE" else None,
                              prev_guidance_speech=prev_guidance_speech)
        except json.JSONDecodeError as exc:
            errors = [f"json_error: {exc}"]
    return {"raw": res.get("raw"), "parsed": parsed, "errors": errors, "metrics": res.get("metrics", {}), "wall_s": res.get("wall_s"),
            "temperature": res.get("temperature", temperature), "seed": res.get("seed")}


def llm_turn(settings, asm, *, state, profile, scenario, turn_id, utterance=None, history=None, retry=True,
             first_temperature=None, prev_guidance_speech=None):
    """One LLM turn; if validation fails and retry=True, re-ask once with the validator errors appended.
    Returns the final attempt's fields plus attempts=[first, second?] (both logged)."""
    rank = profile["rank_code"]
    ctx = asm.assemble_gesture(state=state, profile=profile, scenario=scenario, turn_id=turn_id,
                               visitor_utterance=utterance, history=history)
    schema = asm.schema(state, turn_id, "E3", rank)
    kw = dict(state=state, turn_id=turn_id, rank=rank, scenario=scenario, prev_guidance_speech=prev_guidance_speech)
    first = _attempt(settings, ctx.messages, schema, temperature=first_temperature, **kw)
    attempts = [first]
    if first["errors"] and retry and first["raw"]:
        msgs = ctx.messages + [
            {"role": "assistant", "content": first["raw"]},
            {"role": "user", "content": "Tu respuesta anterior fue rechazada por el validador: " + "; ".join(first["errors"])
             + ". Corrígela y devuelve solo el objeto del contrato completo (mismos event_id y utterance_id)."}]
        attempts.append(_attempt(settings, msgs, schema, temperature=RETRY_TEMPERATURE, **kw))
    final = attempts[-1]
    lat = [x["metrics"].get("total_s") for x in attempts if x["metrics"].get("total_s") is not None]
    return {**final, "prompt_chars": ctx.prompt_chars, "attempts": attempts, "retry_used": len(attempts) > 1,
            "first_attempt_ok": not first["errors"], "total_llm_s": round(sum(lat), 4) if lat else None}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rank", default="VIP", choices=list(PROFILES))
    ap.add_argument("--scenario", default="reception_lab_right", choices=list(SCENARIOS))
    ap.add_argument("--utterance", action="append", help="visitor utterance(s) for DIALOGUE turns")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    settings = Settings()
    asm = GestureContextAssembler(settings)
    tts = GestureTTS(settings, out / "wav")
    profile, scenario = PROFILES[a.rank], SCENARIOS[a.scenario]
    visit_id = f"visit_{a.rank.lower()}_{int(time.time())}"
    utts = a.utterance or DEFAULT_UTTS.get(a.rank, DEFAULT_UTTS["GUEST"])
    plan = [("GREETING", None)] + [("GUIDANCE", None)] + [("DIALOGUE", u) for u in utts] + [("FAREWELL", None)]
    mgr, history, turns, manifest = VisitGestureManager(), [], [], []
    try:
        for i, (state, utt) in enumerate(plan):
            turn_id = f"{visit_id}_t{i:02d}"
            r = llm_turn(settings, asm, state=state, profile=profile, scenario=scenario, turn_id=turn_id,
                         utterance=utt, history=[])  # raw-JSON history made the 4B model echo; kept off
            dec = mgr.decide(r["parsed"], r["errors"], state)
            rec = {"turn_id": turn_id, "state": state, "visitor_utterance": utt, "llm": r, "decision": dec}
            if r["parsed"] and not r["errors"]:
                history.append({"role": "assistant", "content": r["raw"]})
                t = tts.synth(speech=r["parsed"]["speech"], state=state, turn_id=turn_id, visit_id=visit_id)
                rec["tts"] = t
                manifest.append(row_for(visit_id, visit_id, turn_id, state, r["parsed"]["speech"], dec, t))
            turns.append(rec)
            print(f"[{state}] {r['parsed'] and r['parsed']['speech']!r} requested={dec['requested']} effective={dec['effective']} "
                  f"suppressed={dec['suppressed']} errors={r['errors']}", flush=True)
    finally:
        tts.close()
    mpath = out / "tts_manifest.jsonl"
    mpath.write_text("".join(json.dumps(m, ensure_ascii=False) + "\n" for m in manifest), encoding="utf-8")
    log = {"visit_id": visit_id, "profile_injected": {k: v for k, v in profile.items()}, "face_recognition": "bypassed",
           "scenario": scenario["scenario_id"], "turns": turns, "manifest": rel(mpath)}
    anchor = Path(__file__).parent / "align/anchor_plan.py"
    if anchor.exists():
        p = subprocess.run([py_physicalai(), str(anchor), "--manifest", str(mpath),
                            "--out", str(out / "anchor_plan")], capture_output=True, text=True)
        log["anchor_plan"] = {"returncode": p.returncode, "stdout": p.stdout[-3000:], "stderr": p.stderr[-1500:]}
        try:
            plans = sorted((out / "anchor_plan").glob("*.json*"))
            log["anchor_plan"]["files"] = [rel(x) for x in plans]
        except Exception:
            pass
    else:
        log["anchor_plan"] = "skipped: align/anchor_plan.py not present"
    (out / "visit_log.json").write_text(json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
