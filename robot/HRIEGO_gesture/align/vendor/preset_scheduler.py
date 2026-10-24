#!/usr/bin/env python3
"""[VENDORED, HRIEGO modifications marked "HRIEGO:" - see PROVENANCE.md]
Event + measured preset latency -> dispatch plan on the immutable audio sample clock.

dispatch = anchor_start - round(lead*Fs); lead is lead_onset_s or lead_stroke_s (--align).
Never edits the WAV; start-of-audio delay is bounded (c05/c06 rule). Offline: audio_start_monotonic = null.
"""
import json
from pathlib import Path

from .common import sha256_file, write_json

SCHEMA = "x2_preset_latency.v1"
LEAD_KEY = {"onset": "lead_onset_s", "stroke": "lead_stroke_s"}
# HRIEGO: explicit policies + "auto" (stroke -> midpoint -> onset, first one within the start-of-audio cap)
POLICIES = ("onset", "stroke", "midpoint")
AUTO_ORDER = ("stroke", "midpoint", "onset")


def load_latency(path):
    t = json.loads(Path(path).read_text(encoding="utf-8"))
    if t.get("schema") != SCHEMA or not isinstance(t.get("bindings"), dict):
        raise ValueError(f"not a {SCHEMA} file: {path}")
    t["_sha256"], t["_path"] = sha256_file(path), str(path)
    return t


def _stat(entry, field, stat):
    v = (entry or {}).get(field)
    v = v.get(stat) if isinstance(v, dict) else None
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and v >= 0 else None


def _lead(entry, align, stat):
    """HRIEGO: lead for a policy. midpoint = mean(lead_onset_s, lead_stroke_s); None if either is missing."""
    if align == "midpoint":
        o, s = _stat(entry, "lead_onset_s", stat), _stat(entry, "lead_stroke_s", stat)
        return None if o is None or s is None else (o + s) / 2.0
    return _stat(entry, LEAD_KEY[align], stat)


def plan_event(event, table, wav_path=None, align="onset", stat="median", max_initial_delay_s=1.5, busy_until_sample=None):
    """-> plan dict; status in pending|rejected_hash|rejected_resources|rejected_horizon|rejected_conflict."""
    fs, n = event["audio"]["sample_rate_hz"], event["audio"]["sample_count"]
    key = f"{event['binding']['motion_id']}/{event['binding']['area']}"
    plan = {"schema": "x2_gesture_plan.v1", "utterance_id": event["utterance_id"], "binding_key": key,
            "policy": {"align": align, "stat": stat, "max_initial_delay_s": max_initial_delay_s,
                       "latency_file": table.get("_path"), "latency_sha256": table.get("_sha256"),
                       "latency_placeholder": bool(table.get("PLACEHOLDER"))},
            "audio": event["audio"], "audio_start_monotonic": None, "modified_audio": False,
            "status": None, "reason": None, "required_audio_start_delay_samples": 0,
            "dispatch_sample": None, "phases": None, "dispatches": []}

    def rej(status, reason):
        plan["status"], plan["reason"] = status, reason
        return plan

    if wav_path is not None and sha256_file(wav_path) != event["audio"]["sha256"]:
        return rej("rejected_hash", "WAV sha256 differs from event.audio.sha256")
    entry = table["bindings"].get(key)
    lead = _lead(entry, align, stat)  # HRIEGO: midpoint support
    dur = _stat(entry, "duration_s", stat)
    if lead is None or dur is None:
        return rej("rejected_resources", f"no usable {LEAD_KEY.get(align, align)}/duration_s {stat} for {key}")
    anchor = event["anchor"]["start_sample"]
    onset_lead = _stat(entry, "lead_onset_s", stat)
    stroke_lead = _stat(entry, "lead_stroke_s", stat)
    dispatch = anchor - int(round(lead * fs))
    delay = max(0, -dispatch)
    if delay > int(round(max_initial_delay_s * fs)):
        plan["required_audio_start_delay_samples"] = delay
        return rej("rejected_horizon", f"dispatch {dispatch} needs {delay / fs:.3f}s start delay > {max_initial_delay_s}s")
    plan["required_audio_start_delay_samples"] = delay
    end = dispatch + int(round(dur * fs))
    if busy_until_sample is not None and dispatch < busy_until_sample:
        return rej("rejected_conflict", f"dispatch {dispatch} < previous preset end {busy_until_sample}")
    plan["phases"] = {
        "dispatch_sample": dispatch,
        "expected_onset_sample": dispatch + int(round((onset_lead if onset_lead is not None else lead) * fs)),
        "expected_stroke_sample": dispatch + int(round((stroke_lead if stroke_lead is not None else lead) * fs)),
        "expected_end_sample": end, "end_after_audio_end": end > n}
    plan["dispatch_sample"] = dispatch
    plan["dispatches"] = [{"motion_id": event["binding"]["motion_id"], "area": event["binding"]["area"],
                           "dispatch_sample": dispatch, "dispatch_sample_with_start_delay": dispatch + delay,
                           "repeat_count": event["schedule"]["repeat_count"]}]
    plan["status"], plan["reason"] = "pending", "ok"
    return plan


def plan_event_auto(event, table, wav_path=None, stat="median", max_initial_delay_s=1.5, busy_until_sample=None):
    """HRIEGO: policy "auto": try stroke (dispatch = anchor - lead_stroke); if that needs more than the start-of-audio
    cap (rejected_horizon) or has no usable lead, fall back to midpoint, then onset. Returns the first plan that is not
    rejected for horizon/resources (or the last attempt). plan["policy"]["align_policy_used"] says which one produced it."""
    tried, plan = [], None
    for pol in AUTO_ORDER:
        plan = plan_event(event, table, wav_path, pol, stat, max_initial_delay_s, busy_until_sample)
        tried.append(pol)
        if plan["status"] not in ("rejected_horizon", "rejected_resources"):
            break
    plan["policy"].update(align="auto", align_policy_used=tried[-1], align_policy_tried=tried)
    return plan


def plan_sequence(events, table, **kw):
    """Plan several events of one timeline in dispatch order; later overlapping ones are rejected."""
    busy, plans = None, []
    for ev in sorted(events, key=lambda e: e["anchor"]["start_sample"]):
        p = plan_event(ev, table, busy_until_sample=busy, **kw)
        if p["status"] == "pending":
            busy = p["phases"]["expected_end_sample"]
        plans.append(p)
    return plans


def apply_to_event(event, plan):
    ev = json.loads(json.dumps(event))
    ev["schedule"].update(dispatch_sample=plan["dispatch_sample"], status=plan["status"])
    return ev
