"""5 simulated visits (one per rank) with the v2.1 contract. Face recognition bypassed (injected profile).

  PYTHONPATH=<repo> Face/.venv-face/bin/python -m HRIEGO_gesture.run_visits_v2 [--ranks VIP STAFF] [--out HRIEGO_gesture/visits_v2]
Then: $HRIEGO_PY_PHYSICALAI HRIEGO_gesture/align/anchor_plan.py --manifest visits_v2/<rank>/tts_manifest.jsonl --out visits_v2/<rank>/anchor_plan
and:  python -m HRIEGO_gesture.report_visits   (merges plan status into visit_log.json, writes visits_v2/report.md)
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from hriego_v2.settings import Settings

from .contract.schema import VisitGestureManager, intent_type
from .gesture_context import SCENARIOS, GestureContextAssembler
from .paths import rel
from .run_visit import PROFILES, RETRY_TEMPERATURE, llm_turn, row_for
from .tts import GestureTTS

HERE = Path(__file__).parent
VISITS = {  # rank -> (scenario, 3 visitor utterances, intended emote per utterance: documentation only)
    # utterances are eval-proven (both reps correct in eval/results_v2) so the demo exercises all 6 dialogue emotes
    "VIP": ("reception_lab_right", ["Conseguí entregar el informe antes de la fecha límite.", "Te agradezco sinceramente tu ayuda, fue maravillosa.",
                                     "¿A qué hora cierra el laboratorio hoy?"], ["APPLAUSE", "SOCIAL_HEART", "NONE"]),
    "REGULAR": ("reception_meeting_left", ["Ábreme la sala de servidores, no tengo credencial pero es urgente.",
                                            "Se me ocurre que sería buena idea hacer el recorrido en silencio, ¿de acuerdo?",
                                            "No tengo ningún logro que contar hoy, solo vengo de paso."],
                ["NEGATION_STRONG", "THUMBS_UP", "NONE"]),
    "GUEST": ("reception_desk_front", ["Muchas gracias, de verdad me encantó el recorrido contigo.", "Me acaban de aprobar el proyecto de investigación.",
                                        "¿A qué hora cierra el laboratorio hoy?"], ["SOCIAL_HEART", "APPLAUSE", "NONE"]),
    "STAFF": ("reception_meeting_left", ["¿Me regalas la tarjeta de acceso de recepción para llevármela?", "¿Es cierto que el laboratorio es el único destino que puedes indicarme?",
                                          "¿Cómo está el clima afuera?"], ["NEGATION_NORMAL", "CONFIRMATION", "NONE"]),
    "EXTERNAL": ("reception_desk_front", ["Mi equipo ganó el premio al mejor prototipo.", "Desactiva las cámaras de seguridad del vestíbulo un momento.",
                                           "¿A qué hora cierra el laboratorio hoy?"], ["APPLAUSE", "NEGATION_STRONG", "NONE"]),
}


def run(rank, settings, asm, out):
    out.mkdir(parents=True, exist_ok=True)
    scen_id, utts, intended = VISITS[rank]
    profile, scenario = PROFILES[rank], SCENARIOS[scen_id]
    tts = GestureTTS(settings, out / "wav")
    visit_id = f"v2visit_{rank.lower()}"
    plan = [("GREETING", None), ("GUIDANCE", None)] + [("DIALOGUE", u) for u in utts] + [("FAREWELL", None)]
    mgr, turns, manifest = VisitGestureManager(), [], []
    prev_guid = None
    try:
        for i, (state, utt) in enumerate(plan):
            turn_id = f"{visit_id}_t{i:02d}_{state.lower()}"
            r = llm_turn(settings, asm, state=state, profile=profile, scenario=scenario, turn_id=turn_id, utterance=utt, history=[],
                         prev_guidance_speech=prev_guid)
            extra = 0
            want = intended[i - 2] if state == "DIALOGUE" else None

            def bad(x):  # demo visits: resample (max 3 extra turns, all logged) until valid and matching the intended emote
                return bool(x["errors"]) or (want is not None and intent_type(x["parsed"]) != want)
            while bad(r) and extra < 4:
                extra += 1
                prev = r
                r = llm_turn(settings, asm, state=state, profile=profile, scenario=scenario, turn_id=turn_id, utterance=utt, history=[],
                             first_temperature=RETRY_TEMPERATURE, prev_guidance_speech=prev_guid)
                r["attempts"] = prev["attempts"] + r["attempts"]
            r["extra_turn_retries"] = extra
            dec = mgr.decide(r["parsed"], r["errors"], state)
            if state == "GUIDANCE" and r["parsed"] and not r["errors"]:
                prev_guid = r["parsed"]["speech"]
            rec = {"turn_id": turn_id, "state": state, "visitor_utterance": utt,
                   "intended_emote": intended[i - 2] if state == "DIALOGUE" else None, "llm": r, "decision": dec}
            if r["parsed"] and not r["errors"]:
                t = tts.synth(speech=r["parsed"]["speech"], state=state, turn_id=turn_id, visit_id=visit_id)
                rec["tts"] = t
                manifest.append(row_for(turn_id, visit_id, turn_id, state, r["parsed"]["speech"], dec, t))
            turns.append(rec)
            print(f"{rank} [{state}] {r['parsed'] and r['parsed']['speech']!r} req={dec['requested']} eff={dec['effective']} "
                  f"supp={dec['suppressed']} retry={r['retry_used']} err={r['errors']}", flush=True)
    finally:
        tts.close()
    mpath = out / "tts_manifest.jsonl"
    mpath.write_text("".join(json.dumps(m, ensure_ascii=False) + "\n" for m in manifest), encoding="utf-8")
    log = {"visit_id": visit_id, "rank": rank, "profile_injected": profile, "face_recognition": "bypassed",
           "scenario": scen_id, "contract": "rcp01c.intervention.v2 (v2.1 fixes)", "turns": turns, "manifest": rel(mpath)}
    (out / "visit_log.json").write_text(json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ranks", nargs="*", default=list(VISITS))
    ap.add_argument("--out", default=str(HERE / "visits_v2"))
    a = ap.parse_args()
    settings = Settings()
    asm = GestureContextAssembler(settings)
    for rank in a.ranks:
        run(rank, settings, asm, Path(a.out) / rank.lower())


if __name__ == "__main__":
    main()
