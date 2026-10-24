"""Metrics computed identically on v1 and v2 llm_runs.jsonl rows (v1 rows have no 'attempts')."""
from __future__ import annotations

import re
import statistics as st

from ..contract.schema import RANK_LEAK_RE, find_span, lexicon_hit, speech_word_count, validate


def _p(xs, q):
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(round(q * (len(xs) - 1))))], 3) if xs else None


def extra_metrics(runs):
    out = {}
    first_ok = [bool(r["llm"].get("first_attempt_ok", not r["llm"]["errors"])) for r in runs]
    out["contract_valid_first_attempt"] = round(sum(first_ok) / len(runs), 4)
    out["contract_valid_after_retry"] = round(sum(not r["llm"]["errors"] for r in runs) / len(runs), 4)
    out["retry_rate"] = round(sum(bool(r["llm"].get("retry_used")) for r in runs) / len(runs), 4)
    out["retry_recovered"] = sum(bool(r["llm"].get("retry_used")) and not r["llm"]["errors"] for r in runs)
    # analyse the first attempt of every run (what a no-retry system would have seen)
    first = []
    for r in runs:
        a0 = (r["llm"].get("attempts") or [r["llm"]])[0]
        first.append((r, a0))
    wi = [(r, a) for r, a in first if a["parsed"] and a["parsed"].get("motor_intents")]
    lit = wb = lx = 0
    for r, a in wi:
        sp, it = a["parsed"]["speech"], a["parsed"]["motor_intents"][0]
        t = it.get("trigger_text", "")
        lit += t in sp and bool(t)
        wb += bool(t) and find_span(sp, t) is not None
        lx += lexicon_hit(t, it.get("type", ""), r["case"]["state"])
    n = len(wi) or 1
    out["n_with_intent_first_attempt"] = len(wi)
    out["trigger_literal_rate"] = round(lit / n, 4)
    out["trigger_whole_word_rate"] = round(wb / n, 4)
    out["lexicon_pass_rate"] = round(lx / n, 4)
    speeches = [a["parsed"]["speech"] for _, a in first if a["parsed"]]
    out["rank_leak_count_first_attempt"] = sum(bool(RANK_LEAK_RE.search(s)) for s in speeches)
    fin = [r["llm"]["parsed"]["speech"] for r in runs if r["llm"]["parsed"] and not r["llm"]["errors"]]
    out["rank_leak_count_final_valid"] = sum(bool(RANK_LEAK_RE.search(s)) for s in fin)
    wc = [speech_word_count(s) for s in speeches]
    out["speech_words_p50"] = st.median(wc) if wc else None
    out["speech_words_min"] = min(wc) if wc else None
    out["speech_lt6_words"] = sum(w < 6 for w in wc)
    lat1 = [a["metrics"].get("total_s") for _, a in first if a["metrics"].get("total_s") is not None]
    out["llm_latency_first_attempt_s"] = {"p50": _p(lat1, .5), "p95": _p(lat1, .95)}
    tot = [r["llm"].get("total_llm_s") or (r["llm"]["metrics"] or {}).get("total_s") for r in runs]
    tot = [x for x in tot if x is not None]
    out["llm_latency_with_retry_s"] = {"p50": _p(tot, .5), "p95": _p(tot, .95)}
    # first attempt vs the v2 validator (for v1 rows this re-judges the old outputs)
    ok = 0
    for r, a in first:
        c = r["case"]
        if a["parsed"] is None:
            continue
        sc = {"reception_lab_right": "right", "reception_desk_front": "front", "reception_meeting_left": "left"}.get(c.get("scenario"))
        errs = validate(a["parsed"], state=c["state"], turn_id=r["turn_id"], scene_id="E3", rank=c["rank"],
                        scenario_direction=sc if c["state"] == "GUIDANCE" else None)
        ok += not errs
    out["first_attempt_passes_v2_validator"] = round(ok / len(runs), 4)
    return out
