"""LLM-only 2 reps per case + TTS on rep-1 valid rows. PYTHONPATH=<repo> Face/.venv-face/bin/python -m HRIEGO_gesture.eval.run_eval"""
from __future__ import annotations

import argparse
import json
import statistics as st
from collections import Counter, defaultdict
from pathlib import Path

from hriego_v2.settings import Settings

from ..contract.schema import VisitGestureManager, intent_type
from ..gesture_context import SCENARIOS, GestureContextAssembler
from ..run_visit import PROFILES, llm_turn, row_for
from ..tts import GestureTTS
from .metrics import extra_metrics

HERE = Path(__file__).parent
LABELS = ["NEGATION_NORMAL", "NEGATION_STRONG", "CONFIRMATION", "THUMBS_UP", "SOCIAL_HEART", "APPLAUSE", "NONE"]


def pct(xs, q):
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(round(q * (len(xs) - 1))))], 3) if xs else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=2)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--no-tts", action="store_true")
    ap.add_argument("--out", default="results_v2")
    ap.add_argument("--cases", default="cases_v2.json")
    ap.add_argument("--no-retry", action="store_true")
    a = ap.parse_args()
    res = HERE / a.out; res.mkdir(exist_ok=True)
    cases = json.loads((HERE / a.cases).read_text())[: a.limit]
    settings = Settings(); asm = GestureContextAssembler(settings)
    tts = None if a.no_tts else GestureTTS(settings, res / "wav")
    runs, manifest = [], []
    try:
        for c in cases:
            for rep in range(1, a.reps + 1):
                turn_id = f"{c['case_id']}_r{rep}"
                profile = PROFILES[c["rank"]]
                scenario = SCENARIOS.get(c.get("scenario"))
                r = llm_turn(settings, asm, state=c["state"], profile=profile, scenario=scenario, turn_id=turn_id,
                             utterance=c.get("visitor_utterance"), retry=not a.no_retry)
                dec = VisitGestureManager().decide(r["parsed"], r["errors"])
                row = {"case": c, "rep": rep, "turn_id": turn_id, "llm": r, "decision": dec,
                       "predicted": dec["requested"] if r["parsed"] and not r["errors"] else ("INVALID" if r["errors"] else "NONE")}
                if rep == 1 and tts and r["parsed"] and not r["errors"]:
                    try:
                        t = tts.synth(speech=r["parsed"]["speech"], state=c["state"], turn_id=turn_id, visit_id=c["case_id"])
                        row["tts"] = t
                        manifest.append(row_for(c["case_id"], c["case_id"], turn_id, c["state"], r["parsed"]["speech"], dec, t))
                    except Exception as exc:
                        row["tts_error"] = repr(exc)
                runs.append(row)
                print(c["case_id"], rep, "exp", c["expected"], "got", row["predicted"], r["errors"][:1], flush=True)
    finally:
        if tts: tts.close()
    (res / "llm_runs.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in runs), encoding="utf-8")
    (res / "tts_manifest.jsonl").write_text("".join(json.dumps(m, ensure_ascii=False) + "\n" for m in manifest), encoding="utf-8")

    n = len(runs)
    llm_ok = [r for r in runs if r["llm"]["raw"] is not None]
    json_ok = 0
    for r in runs:
        json_ok += r["llm"]["parsed"] is not None
    valid = [r for r in runs if not r["llm"]["errors"]]
    S = {"n_runs": n, "n_cases": len(cases), "json_valid_rate": round(json_ok / n, 4), "contract_valid_rate": round(len(valid) / n, 4)}
    # dialogue confusion
    dl = [r for r in runs if r["case"]["state"] == "DIALOGUE"]
    conf = defaultdict(Counter)
    for r in dl:
        conf[r["case"]["expected"]][r["predicted"]] += 1
    S["dialogue_confusion"] = {k: dict(v) for k, v in conf.items()}
    pr = {}
    for lab in LABELS:
        tp = conf[lab][lab]; fp = sum(conf[o][lab] for o in LABELS if o != lab); fn = sum(v for k, v in conf[lab].items() if k != lab)
        pr[lab] = {"precision": round(tp / (tp + fp), 3) if tp + fp else None, "recall": round(tp / (tp + fn), 3) if tp + fn else None,
                   "tp": tp, "fp": fp, "fn": fn}
    S["dialogue_per_emote"] = pr
    nn = [r for r in dl if r["case"]["expected"] == "NONE"]
    S["none_false_positive_rate"] = round(sum(r["predicted"] not in ("NONE",) for r in nn) / len(nn), 3) if nn else None
    S["dialogue_exact_accuracy"] = round(sum(r["predicted"] == r["case"]["expected"] for r in dl) / len(dl), 3) if dl else None
    # trigger literal
    wi = [r for r in runs if r["llm"]["parsed"] and r["llm"]["parsed"].get("motor_intents")]
    lit = sum(1 for r in wi if all(i.get("trigger_text", "\0") in r["llm"]["parsed"]["speech"] for i in r["llm"]["parsed"]["motor_intents"]))
    S["trigger_literal_rate"] = round(lit / len(wi), 4) if wi else None
    S["n_with_intent"] = len(wi)
    # greeting / farewell
    g = [r for r in runs if r["case"]["state"] == "GREETING"]
    S["greeting_emote_use_rate"] = round(sum(r["predicted"] == r["case"]["expected"] for r in g) / len(g), 3) if g else None
    stf = [r for r in g if r["case"]["rank"] == "STAFF"]
    S["staff_phrase_compliance"] = round(sum(r["predicted"] == "GREETING_STAFF" and "¡dame esos cinco!" in r["llm"]["parsed"]["speech"].casefold()
                                              and not r["llm"]["errors"] for r in stf) / len(stf), 3) if stf else None
    f = [r for r in runs if r["case"]["state"] == "FAREWELL"]
    S["farewell"] = {r["turn_id"]: r["predicted"] for r in f}
    S["farewell_vip_heart_rate"] = (lambda v: round(sum(r["predicted"] == "SOCIAL_HEART" for r in v) / len(v), 3) if v else None)([r for r in f if r["case"]["rank"] == "VIP"])
    # pointing
    gd = [r for r in runs if r["case"]["state"] == "GUIDANCE"]
    S["point_direction_accuracy"] = round(sum(r["predicted"] == r["case"]["expected"] for r in gd) / len(gd), 3) if gd else None
    S["point_by_expected"] = {e: [r["predicted"] for r in gd if r["case"]["expected"] == e] for e in ("POINT_RIGHT", "POINT_LEFT", "POINT_FRONT")}
    S["extra"] = extra_metrics(runs)
    lat = [r["llm"]["metrics"].get("total_s") for r in runs if r["llm"]["metrics"].get("total_s") is not None]
    S["llm_latency_s"] = {"p50": pct(lat, .5), "p95": pct(lat, .95), "mean": round(st.mean(lat), 3) if lat else None}
    syn = [m["synth_s"] for m in manifest]
    S["tts"] = {"n": len(syn), "synth_s_p50": pct(syn, .5), "synth_s_p95": pct(syn, .95),
                "rtf_mean": round(st.mean(m["synth_s"] / m["duration_s"] for m in manifest), 3) if manifest else None}
    S["error_breakdown"] = dict(Counter(e.split(":")[0][:80] for r in runs for e in r["llm"]["errors"]))
    S["failures"] = [{"turn": r["turn_id"], "expected": r["case"]["expected"], "got": r["predicted"],
                      "speech": (r["llm"]["parsed"] or {}).get("speech"), "utt": r["case"].get("visitor_utterance"),
                      "errors": r["llm"]["errors"]} for r in runs if r["predicted"] != r["case"]["expected"]
                     and not (r["case"]["expected"] == "FAREWELL_WAVE|NONE" and r["predicted"] in ("FAREWELL_WAVE", "NONE"))]
    (res / "summary.json").write_text(json.dumps(S, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in S.items() if k != "failures"}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
