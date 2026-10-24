#!/usr/bin/env python3
"""4d traceability check: LLM raw JSON -> validator -> effective gesture -> WAV sha -> CTC anchor -> plan -> executed trace.
Run: cd $X2_ULTRA_ROOT; PYTHONPATH=$PWD Face/.venv-face/bin/python HRIEGO_gesture/validation/trace_check.py
Read-only on all artifacts. Writes validation/trace_check.json and trace_check_table.csv."""
import csv, hashlib, json, math, wave
from pathlib import Path
from HRIEGO_gesture.paths import DEFAULT_LATENCY, resolve
from HRIEGO_gesture.contract.schema import (EMOTES, VisitGestureManager, normalize, validate, find_span, intent_type, allowed_types)

H = Path(__file__).resolve().parent.parent
V = H / "visits_v2"; R = H / "twin/runs/4c_visits"
LAT = json.load(open(DEFAULT_LATENCY))["bindings"]  # config/preset_latency_c08.json (byte-identical to the c08 table)
SCEN_DIR = {"VIP": "right", "REGULAR": "left", "GUEST": "front", "STAFF": "left", "EXTERNAL": "front"}
RANKS = ["vip", "regular", "guest", "staff", "external"]
mism = []  # (turn, rep, check, detail)
checks = {}  # check name -> [n_ok, n_total]


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def chk(tid, rep, name, ok, detail=""):
    c = checks.setdefault(name, [0, 0]); c[1] += 1; c[0] += bool(ok)
    if not ok:
        mism.append({"turn": tid, "rep": rep, "check": name, "detail": str(detail)})
    return ok


def near(a, b, tol): return a is not None and b is not None and abs(a - b) <= tol


def csvrows(p): return list(csv.DictReader(open(p)))


rows_out = []; dur_delta = []
turns_csv = {1: {r["turn_id"]: r for r in csvrows(R / "metrics/turns.csv")}, 2: {r["turn_id"]: r for r in csvrows(R / "metrics/turns_r2.csv")}}
for rk in RANKS:
    log = json.load(open(V / rk / "visit_log.json")); rank = log["rank"]
    manifest = {}
    for l in open(V / rk / "tts_manifest.jsonl"):
        m = json.loads(l); manifest[m["turn_id"]] = m
    summ = {r["case_id"]: r for r in json.load(open(V / rk / "anchor_plan/summary.json"))["rows_detail"]}
    mgr = VisitGestureManager()
    scen_dir = SCEN_DIR[rank]
    prev_guid = None
    for t in log["turns"]:
        tid, state = t["turn_id"], t["state"]; llm = t["llm"]; dec = t["decision"]
        row = {"visit": rk, "turn": tid, "state": state}
        # --- 1. LLM raw -> parsed -> validator verdict
        att = llm["attempts"][-1]
        chk(tid, 0, "raw_is_final_attempt", llm["raw"] == att["raw"])
        try:
            raw = json.loads(llm["raw"]); parsed = normalize(raw)
        except Exception as e:
            chk(tid, 0, "raw_json_parses", False, e); continue
        chk(tid, 0, "raw_json_parses", True)
        chk(tid, 0, "normalize(raw)==stored_parsed", parsed == llm["parsed"], "")
        errs = validate(parsed, state=state, turn_id=tid, scene_id="E3", rank=rank, scenario_direction=scen_dir if state == "GUIDANCE" else None,
                        prev_guidance_speech=prev_guid)
        chk(tid, 0, "validator_replay_no_errors", errs == [] and llm["errors"] == [], errs)
        if state == "GUIDANCE" and not errs: prev_guid = parsed["speech"]
        # --- 2. effective gesture via replayed manager (cooldown/FSM mandated)
        d2 = mgr.decide(parsed, errs, state)
        for k in ("requested", "effective", "suppressed", "trigger_text", "trigger_char_start", "trigger_char_end"):
            chk(tid, 0, f"decision_replay.{k}", d2.get(k) == dec.get(k), f"replay={d2.get(k)} stored={dec.get(k)}")
        eff = dec["effective"]
        allowed = allowed_types(state, rank) + ["NONE"]
        chk(tid, 0, "effective_in_allowed_enum", eff in allowed, eff)
        if state == "GREETING": chk(tid, 0, "greeting_is_rank_emote", eff == f"GREETING_{rank}", eff)
        if state == "FAREWELL": chk(tid, 0, "farewell_is_mandated_emote", eff == ("SOCIAL_HEART" if rank == "VIP" else "FAREWELL_WAVE"), eff)
        if state == "GUIDANCE": chk(tid, 0, "point_matches_scenario", eff == {"right": "POINT_RIGHT", "left": "POINT_LEFT", "front": "POINT_FRONT"}[scen_dir], eff)
        row.update(speech=parsed["speech"], requested=dec["requested"], effective=eff, suppressed=dec["suppressed"], fsm_mandated=dec.get("fsm_mandated", False))
        # --- 3. manifest / WAV
        m = manifest.get(tid)
        if not chk(tid, 0, "manifest_row_exists", m is not None): continue
        chk(tid, 0, "manifest.emote==effective", m["emote"] == eff, f"{m['emote']} vs {eff}")
        meta = EMOTES.get(eff) if eff != "NONE" else None
        chk(tid, 0, "manifest.motion_area==catalog", (m["motion_id"], m["area"]) == ((meta["motion_id"], meta["area"]) if meta else (None, None)), (m["motion_id"], m["area"]))
        chk(tid, 0, "manifest.speech==parsed.speech", m["speech"] == parsed["speech"])
        if eff != "NONE":
            sp = m["speech"]
            chk(tid, 0, "manifest.trigger==speech[span]", sp[m["trigger_char_start"]:m["trigger_char_end"]] == m["trigger_text"] == dec["trigger_text"], m["trigger_text"])
            chk(tid, 0, "trigger_on_word_boundaries", find_span(sp, m["trigger_text"]) == (m["trigger_char_start"], m["trigger_char_end"]))
        else:
            chk(tid, 0, "NONE_has_no_trigger", m["trigger_text"] is None and dec["trigger_text"] is None)
        wp = resolve(m["wav_path"]); ok_file = wp.is_file()
        chk(tid, 0, "wav_file_exists", ok_file, wp)
        if ok_file:
            s = sha(wp)
            chk(tid, 0, "wav_sha256 manifest==disk", s == m["wav_sha256"], f"disk={s[:12]}")
            chk(tid, 0, "wav_sha256 visit_log.tts==manifest", t["tts"]["wav_sha256"] == m["wav_sha256"])
            with wave.open(str(wp)) as w:
                chk(tid, 0, "wav_sample_rate==manifest", w.getframerate() == m["sample_rate"])
                dd = w.getnframes() / w.getframerate() - m["duration_s"]; dur_delta.append(dd)
                chk(tid, 0, "wav_file_duration within 0.1 s of manifest.duration_s (informational metadata)", abs(dd) <= 0.1, dd)
                nframes, fs = w.getnframes(), w.getframerate()
        row.update(wav_sha=m["wav_sha256"][:12], audio_s=m["duration_s"])
        sr = summ[tid]
        # --- 4. CTC anchor
        anc_p = V / rk / f"anchor_plan/events/{tid}.anchor.json"
        plan_p = V / rk / f"anchor_plan/plans/{tid}.plan.json"
        if eff == "NONE":
            chk(tid, 0, "NONE: summary status none_sonic", sr["status"] == "none_sonic", sr["status"])
            chk(tid, 0, "NONE: no anchor/event/plan files (zero dispatch)", not anc_p.exists() and not plan_p.exists() and not (V / rk / f"anchor_plan/events/{tid}.event.json").exists())
            row.update(anchor="-", plan="none_sonic")
        else:
            an = json.load(open(anc_p))
            chk(tid, 0, "anchor.status aligned", an["status"] == "aligned_ctc_not_human_validated", an["status"])
            chk(tid, 0, "anchor.text/span==trigger", an["text"] == m["trigger_text"] and (an["char_start"], an["char_end"]) == (m["trigger_char_start"], m["trigger_char_end"]))
            chk(tid, 0, "anchor.sha==wav sha", an["sha256"] == m["wav_sha256"])
            chk(tid, 0, "anchor.samples==wav", an["sample_count"] == nframes and an["sample_rate_hz"] == fs)
            chk(tid, 0, "anchor start<end<=duration", 0 <= an["start_s"] < an["end_s"] <= m["duration_s"] + 0.02, (an["start_s"], an["end_s"]))
            chk(tid, 0, "anchor.start_sample==round(start_s*fs)", abs(an["start_sample"] - an["start_s"] * fs) <= 1, "")
            chk(tid, 0, "summary.csv/json anchor==anchor.json", near(sr["trigger_start_s"], an["start_s"], 1e-9) and near(sr["trigger_end_s"], an["end_s"], 1e-9))
            chk(tid, 0, "visit_log.plan.start/end==anchor.json", near(t["plan"]["trigger_start_s"], an["start_s"], 1e-9) and near(t["plan"]["trigger_end_s"], an["end_s"], 1e-9))
            row.update(anchor=f"{an['status'].split('_')[0]} {an['start_s']:.2f}-{an['end_s']:.2f}")
            # --- 5. plan
            if eff.startswith("POINT"):
                chk(tid, 0, "POINT: status anchor_only_no_preset, no plan file", sr["status"] == "anchor_only_no_preset" and not plan_p.exists(), sr["status"])
                row.update(plan="anchor_only")
            else:
                pl = json.load(open(plan_p)); d0 = pl["dispatches"][0]
                pol = pl["policy"].get("align_policy_used") or pl["policy"]["align"]  # stored visits_v2 plans predate "auto": onset
                b_ = LAT[f"{meta['motion_id']}/{meta['area']}"]
                lead = {"onset": b_["lead_onset_s"]["median"], "stroke": b_["lead_stroke_s"]["median"],
                        "midpoint": (b_["lead_onset_s"]["median"] + b_["lead_stroke_s"]["median"]) / 2}[pol]
                chk(tid, 0, "plan.status pending & reason ok", pl["status"] == "pending" and pl["reason"] == "ok" and sr["status"] == "planned" and t["plan"]["status"] == "planned", (pl["status"], pl["reason"]))
                chk(tid, 0, "plan.audio.sha==wav sha", pl["audio"]["sha256"] == m["wav_sha256"] and pl["audio"]["sample_count"] == nframes)
                chk(tid, 0, "plan.binding==manifest motion/area", pl["binding_key"] == f"{m['motion_id']}/{m['area']}" and (d0["motion_id"], d0["area"]) == (m["motion_id"], m["area"]))
                chk(tid, 0, "plan.latency_sha==c08 table", pl["policy"]["latency_sha256"] == "d35c250a0e021d29e3eff90e927360b50e95185953933798ca883294d4f412f3" and not pl["policy"]["latency_placeholder"])
                exp_disp = an["start_sample"] - round(lead * fs)
                chk(tid, 0, f"plan.dispatch==anchor_start - c08 {pol} lead", abs(pl["dispatch_sample"] - exp_disp) <= 2, (pl["dispatch_sample"], exp_disp))
                chk(tid, 0, "plan.start_delay==max(0,-dispatch)", pl["required_audio_start_delay_samples"] == max(0, -pl["dispatch_sample"]))
                chk(tid, 0, "plan.start_delay<=1.5s cap", pl["required_audio_start_delay_samples"] / fs <= 1.5 + 1e-9, pl["required_audio_start_delay_samples"] / fs)
                chk(tid, 0, "plan.unmodified_audio", pl["modified_audio"] is False)
                row.update(plan=f"pending disp={pl['dispatch_sample']} delay={pl['required_audio_start_delay_samples']/fs:.2f}s")
        # --- 6. executed trace per rep
        for rep in (1, 2):
            name = f"{tid}_r{rep}"; tr = turns_csv[rep][tid]
            csv_p = R / f"traces/{name}.csv"; js_p = R / f"traces/{name}.json"
            chk(tid, rep, "trace csv exists", csv_p.exists(), csv_p.name)
            chk(tid, rep, "turns.csv completed/contract_ok", tr["completed_turn"] in ("True", "") and tr["contract_ok"] == "True" or tr["completed_turn"] == "True", (tr["completed_turn"], tr["contract_ok"]))
            tl = csvrows(csv_p)
            if eff == "NONE":
                chk(tid, rep, "NONE exec: SONIC streamed (kind NONE, no preset JSON)", tr["type"] == "NONE" and not js_p.exists(), tr["type"])
                chk(tid, rep, "NONE exec: streamed (no MC preset columns), mode URS", "motion" not in tl[0] and "UPPERBODY_REMOTE_SPLIT" in {r["mode"] for r in tl}, {r["mode"] for r in tl})
                chk(tid, rep, "NONE exec: arms npz base exists", (V / rk / f"sonic/{tid}/{tid}_arms.npz").exists())
                chk(tid, rep, "NONE exec: arm tracking err <=0.015 rad", float(tr["arm_track_err_rad"]) <= 0.015, tr["arm_track_err_rad"])
                chk(tid, rep, "NONE exec: no fall (pelvis>=0.5)", float(tr["pelvis_z_min"]) >= 0.5)
                rows_out.append({**row, "rep": rep, "executed": f"SONIC streamed, arm_err={float(tr['arm_track_err_rad']):.4f} rad"})
            elif eff.startswith("POINT"):
                js = json.load(open(js_p)); D = eff.split("_")[1]
                chk(tid, rep, "POINT exec: direction==effective", js["direction"] == D and tr["direction"] == D, (js["direction"], tr["direction"]))
                chk(tid, rep, "POINT exec: anchor_start==CTC anchor", near(js["anchor_start_s"], an["start_s"], 1e-6), (js["anchor_start_s"], an["start_s"]))
                chk(tid, rep, "POINT exec: base==turn SONIC npz", js["base"] == f"{tid}_arms.npz")
                chk(tid, rep, "POINT exec: FK err to commanded dir (mean hold <=15 deg, source measured joints)", float(tr["err_deg_mean_hold"]) <= 15 and tr["err_source"] == "measured_joints", tr["err_deg_mean_hold"])
                chk(tid, rep, "POINT exec: streamed (no MC preset columns), mode URS", "motion" not in tl[0] and {r["mode"] for r in tl} <= {"UPPERBODY_REMOTE_SPLIT", "STAND_DEFAULT"}, {r["mode"] for r in tl})
                chk(tid, rep, "POINT exec: URS mode all ticks", tr["mode_all_urs"] == "True")
                rows_out.append({**row, "rep": rep, "executed": f"POINT {js['direction']} FK err hold={float(tr['err_deg_mean_hold']):.1f} deg, anchor={float(tr['err_deg_at_anchor']):.1f}"})
            else:
                js = json.load(open(js_p))
                chk(tid, rep, "PRESET exec: wav sha in trace==manifest", js["wav_sha256"] == m["wav_sha256"])
                chk(tid, rep, "PRESET exec: motion/area dispatched==manifest", (js["motion_id"], js["area"]) == (m["motion_id"], m["area"]), (js["motion_id"], js["area"]))
                chk(tid, rep, "PRESET exec: binding_key==plan", js["binding_key"] == pl["binding_key"])
                chk(tid, rep, "PRESET exec: start_delay/dispatch_sample==plan", near(js["start_delay_s"], pl["required_audio_start_delay_samples"] / fs, 1e-6) and js["dispatch_sample"] == pl["dispatch_sample"], (js["start_delay_s"], js["dispatch_sample"]))
                chk(tid, rep, "PRESET exec: plan_status pending & dispatched & completed", js["plan_status"] == "pending" and js["dispatched"] and js["completed"] and js["fail_reason"] is None)
                seen = {r["motion"] for r in tl if r["motion"] != ""}
                chk(tid, rep, "PRESET exec: MC motion seen == manifest motion", seen == {str(m["motion_id"])}, seen)
                states = {(r["mode"], r["fsm"]) for r in tl}
                chk(tid, rep, "PRESET exec: STAND_DEFAULT/fsm4 throughout, playing state reached", states == {("STAND_DEFAULT", "4")} and any(r["player_state"] == "2" for r in tl), states)
                chk(tid, rep, "PRESET exec: returned STAND_DEFAULT stable", js["stable_after"] and js["mode_after"] == "STAND_DEFAULT")
                chk(tid, rep, "PRESET exec: dispatch jitter <=5 ms", abs(js["jitter_ms"]) <= 5, js["jitter_ms"])
                rows_out.append({**row, "rep": rep, "executed": f"MC {js['motion_id']}/{js['area']} played (state2), onset_err={float(tr['onset_minus_anchor_ms']):+.0f} ms"})
res = {"checks": {k: {"ok": v[0], "total": v[1]} for k, v in checks.items()}, "n_checks": sum(v[1] for v in checks.values()),
       "n_ok": sum(v[0] for v in checks.values()), "n_mismatches": len(mism), "mismatches": mism, "executed_turn_rows": len(rows_out), "manifest_duration_vs_file_s": {"n": len(dur_delta), "min": min(dur_delta), "max": max(dur_delta), "mean": sum(dur_delta)/len(dur_delta), "n_exact_6e-4": sum(abs(x) <= 6e-4 for x in dur_delta)}}
json.dump(res, open(H / "validation/trace_check.json", "w"), indent=1, ensure_ascii=False)
with open(H / "validation/trace_check_table.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["visit", "turn", "rep", "state", "speech", "requested", "effective", "suppressed", "fsm_mandated", "wav_sha", "audio_s", "anchor", "plan", "executed"], extrasaction="ignore")
    w.writeheader(); w.writerows(rows_out)
print(json.dumps({k: res[k] for k in ("n_checks", "n_ok", "n_mismatches", "executed_turn_rows", "manifest_duration_vs_file_s")}), "\n", json.dumps(mism, indent=1, ensure_ascii=False)[:4000])
