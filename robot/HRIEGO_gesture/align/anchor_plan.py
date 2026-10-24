#!/usr/bin/env python3
"""Standalone CLI: QuartzNet CTC anchors on real TTS WAVs + c09 preset dispatch plans.

  python anchor_plan.py --manifest M.jsonl --out DIR [--latency L] [--align auto|stroke|midpoint|onset]

--align (default auto) is the dispatch-alignment policy; dispatch = anchor_start - lead (audio sample clock):
  stroke    lead = lead_stroke_s   (the gesture's visible stroke lands on the trigger word)
  midpoint  lead = mean(lead_onset_s, lead_stroke_s)
  onset     lead = lead_onset_s    (the gesture's visible onset lands on the trigger word)
  auto      try stroke; if it needs a start-of-audio delay above --max-initial-delay-s (or has no usable lead) fall back
            to midpoint, then onset. The policy actually used is recorded per row/plan as `align_policy_used`.
Manifest paths (wav_path) are relative to the HRIEGO_gesture root ($HRIEGO_GESTURE_ROOT) or absolute.
Fail closed: wav hash/sample-rate mismatch, speech[start:end] != trigger_text (G1), missing latency, alignment failure.
Needs $QUARTZNET_ONNX (sha256-pinned) for the CTC aligner; the c09 scheduler code is vendored in align/vendor/.
Default --latency: config/preset_latency_c08.json (twin-measured c08 table).
"""
import argparse, copy, csv, json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))      # align/  -> vendor
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # HRIEGO_gesture root -> paths
from paths import DEFAULT_LATENCY, rel, resolve  # noqa: E402
from vendor.align_anchor import Aligner, METHOD  # noqa: E402
from vendor.common import read_jsonl, sha256_file, write_json  # noqa: E402
from vendor.event_builder import build_event  # noqa: E402
from vendor.preset_scheduler import load_latency, plan_event, plan_event_auto  # noqa: E402

ALIGNED = "aligned_ctc_not_human_validated"
ALIGN_CHOICES = ["auto", "stroke", "midpoint", "onset"]


def plan_row(event, table, wav, align, stat, max_delay):
    """One plan under the requested policy; auto records the policy it ended up using in policy.align_policy_used."""
    if align == "auto":
        return plan_event_auto(event, table, wav, stat, max_delay)
    p = plan_event(event, table, wav, align, stat, max_delay)
    p["policy"]["align_policy_used"] = align
    return p


def preroll0_table(table):
    """Hypothetical zero MC pre-roll: every lead/duration reduced by the binding's lead_play_start_s (median)."""
    t = copy.deepcopy(table)
    for b in t["bindings"].values():
        ps = (b.get("lead_play_start_s") or {}).get("median")
        if not isinstance(ps, (int, float)):
            for k in ("lead_onset_s", "lead_stroke_s", "duration_s"):
                b[k] = None  # unknown pre-roll -> missing latency (fail closed)
            continue
        for k in ("lead_onset_s", "lead_stroke_s", "lead_onset_002_s", "duration_s"):
            if isinstance(b.get(k), dict):
                for s, v in list(b[k].items()):
                    if isinstance(v, (int, float)) and s in ("median", "min", "max", "p95"):
                        b[k][s] = max(0.0, v - ps)
    return t


def norm_report(al, cs, ce):
    """How the trigger text was normalized for CTC (from the text view)."""
    try:
        v = al["view"]
        return {"original": v.original, "normalized": getattr(v, "normalized", None) or getattr(v, "text", None)}
    except Exception:
        return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--latency", default=str(DEFAULT_LATENCY))
    ap.add_argument("--align", choices=ALIGN_CHOICES, default="auto")
    ap.add_argument("--stat", default="median")
    ap.add_argument("--max-initial-delay-s", type=float, default=1.5)
    a = ap.parse_args()
    out = Path(a.out).resolve()
    for d in ("events", "plans"):
        (out / d).mkdir(parents=True, exist_ok=True)
    rows = read_jsonl(a.manifest)
    lat_path = resolve(a.latency)
    try:
        table = load_latency(lat_path)
        table["_path"] = rel(lat_path)  # portable: relative to the HRIEGO_gesture root
        table_err = None
    except Exception as exc:  # fail closed: every preset row becomes rejected_resources
        table, table_err = {"bindings": {}, "_path": rel(lat_path), "_sha256": None}, f"{type(exc).__name__}: {exc}"
    table0 = preroll0_table(table)
    aligner = None
    res = []
    try:
        for r in rows:
            cid = r["case_id"]
            rec = {"case_id": cid, "visit_id": r.get("visit_id"), "turn_id": r.get("turn_id"), "emote": r.get("emote"),
                   "motion_id": r.get("motion_id"), "area": r.get("area"), "trigger_text": r.get("trigger_text"),
                   "duration_s": r.get("duration_s"), "sample_rate": r.get("sample_rate"),
                   "trigger_start_s": None, "trigger_end_s": None, "trigger_start_sample": None,
                   "align_status": None, "align_policy_used": None, "align_policy_used_preroll0": None,
                   "delay_s": None, "delay_s_preroll0": None, "dispatch_sample": None, "dispatch_sample_preroll0": None,
                   "status": None, "status_preroll0": None, "reason": None}
            res.append(rec)
            emote = r.get("emote")
            if emote in (None, "NONE"):
                rec["status"] = rec["status_preroll0"] = "none_sonic"
                continue
            wav = r.get("wav_path")
            wav = str(resolve(wav)) if wav else None  # manifest paths are relative to the HRIEGO_gesture root
            if not wav or not Path(wav).is_file():
                rec["status"] = rec["status_preroll0"] = "rejected_hash"
                rec["reason"] = "wav missing"
                continue
            if sha256_file(wav) != r.get("wav_sha256"):
                rec["status"] = rec["status_preroll0"] = "rejected_hash"
                rec["reason"] = "manifest wav_sha256 != file sha256"
                continue
            cs, ce = r.get("trigger_char_start"), r.get("trigger_char_end")
            if cs is None or ce is None or ce <= cs:
                rec["status"] = rec["status_preroll0"] = "alignment_error"
                rec["reason"] = "missing trigger span"
                continue
            if r["speech"][cs:ce] != r.get("trigger_text"):  # G1: the span must be the literal trigger text
                rec["status"] = rec["status_preroll0"] = "rejected_trigger_span"
                rec["reason"] = "speech[start:end] != trigger_text"
                continue
            if aligner is None:
                aligner = Aligner()
            span = aligner.anchor_for_wav(wav, r["speech"], cs, ce)
            rec["align_status"] = span["status"]
            rec["reason"] = span.get("error")
            if span.get("sample_rate_hz") and r.get("sample_rate") and span["sample_rate_hz"] != r["sample_rate"]:
                rec["status"] = rec["status_preroll0"] = "rejected_hash"
                rec["reason"] = "manifest sample_rate != file sample rate"
                continue
            if span["status"] != ALIGNED:
                rec["status"] = rec["status_preroll0"] = f"alignment_{span['status']}"
                continue
            rec.update(trigger_start_s=span["start_s"], trigger_end_s=span["end_s"], trigger_start_sample=span["start_sample"])
            rec["ctc_frame_step_s"] = span.get("frame_step_s")
            write_json(out / f"events/{cid}.anchor.json", {**span, "case_id": cid, "method": METHOD,
                                                           "model": aligner.model_info})
            if r.get("motion_id") is None:
                rec["status"] = rec["status_preroll0"] = "anchor_only_no_preset"
                continue
            if table_err:
                rec["status"] = rec["status_preroll0"] = "rejected_resources"
                rec["reason"] = f"latency table: {table_err}"
                continue
            key = f"{r['motion_id']}/{r['area']}"
            b = (table["bindings"].get(key) or {})
            dec = {"emit_event": True, "status": "ok", "effective": {
                "communicative_act": r.get("emote"), "motor_intent": r.get("emote"), "decision_source": "hriego_manifest",
                "anchor": {"text": span["text"]},
                "binding": {"motion_id": r["motion_id"], "area": r["area"], "asset_sha256": b.get("csv_sha256"),
                            "animation_player_sha256": b.get("yaml_sha256")}}}
            event, why = build_event(cid, dec, span, r["wav_sha256"], span["sample_rate_hz"], span["sample_count"],
                                     aligner.model_info["model_revision"])
            if event is None:
                rec["status"] = rec["status_preroll0"] = why
                continue
            write_json(out / f"events/{cid}.event.json", event)
            for tag, tb in (("", table), ("_preroll0", table0)):
                p = plan_row(event, tb, wav, a.align, a.stat, a.max_initial_delay_s)
                p["policy"]["latency_file"] = rel(lat_path)
                write_json(out / f"plans/{cid}.plan{tag}.json", p)
                rec["align_policy_used" + tag] = p["policy"]["align_policy_used"]
                fs = span["sample_rate_hz"]
                rec["status" if not tag else "status_preroll0"] = "planned" if p["status"] == "pending" else p["status"]
                rec["delay_s" + tag] = p["required_audio_start_delay_samples"] / fs
                rec["dispatch_sample" + tag] = p["dispatch_sample"]
                if p["status"] != "pending" and not rec["reason"]:
                    rec["reason"] = p["reason"]
    finally:
        if aligner is not None:
            aligner.close()

    def cnt(k):
        c = {}
        for x in res:
            c[x[k]] = c.get(x[k], 0) + 1
        return c
    pl = [x for x in res if x["status"] == "planned"]
    summary = {"schema": "hriego_gesture_anchor_plan.v1", "rows": len(res), "policy": {
        "align": a.align, "stat": a.stat, "max_initial_delay_s": a.max_initial_delay_s,
        "latency_file": table.get("_path"), "latency_sha256": table.get("_sha256"), "latency_error": table_err},
        "model": aligner.model_info if aligner else None,
        "align_policy_used_counts": cnt("align_policy_used"), "align_policy_used_preroll0_counts": cnt("align_policy_used_preroll0"),
        "status_counts": cnt("status"), "status_preroll0_counts": cnt("status_preroll0"),
        "align_status_counts": cnt("align_status"),
        "max_delay_s_planned": max((x["delay_s"] for x in pl), default=None),
        "max_delay_s_preroll0_planned": max((x["delay_s_preroll0"] for x in res if x["status_preroll0"] == "planned"), default=None),
        "rows_detail": res}
    write_json(out / "summary.json", summary)
    with (out / "summary.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(res[0]) if res else [], extrasaction="ignore")
        w.writeheader()
        w.writerows(res)
    print(json.dumps({k: v for k, v in summary.items() if k != "rows_detail"}, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
