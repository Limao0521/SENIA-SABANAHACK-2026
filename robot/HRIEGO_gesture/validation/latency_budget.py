#!/usr/bin/env python3
"""4d latency budget from stored logs (+ latency_ctc.json). Run: python3 validation/latency_budget.py -> validation/latency_budget.json"""
import csv, json, statistics as st
from pathlib import Path
H = Path(__file__).resolve().parent.parent
def pct(x, q):
    x = sorted(x); k = (len(x) - 1) * q; f = int(k); c = min(f + 1, len(x) - 1); return x[f] + (x[c] - x[f]) * (k - f)
def S(x): return {"n": len(x), "p50": round(pct(x, .5), 4), "p95": round(pct(x, .95), 4), "max": round(max(x), 4), "min": round(min(x), 4)} if x else None
llm_first_in, llm_first_wall, llm_final_in, llm_all_in, llm_all_wall, llm_turn_total = [], [], [], [], [], []
synth, delay_pre, delay_pre0, aud, rtt = [], [], [], [], []
kinds = {}
n_retry = n_extra = 0
for rk in ["vip", "regular", "guest", "staff", "external"]:
    lg = json.load(open(H / f"visits_v2/{rk}/visit_log.json"))
    sm = {r["case_id"]: r for r in json.load(open(H / f"visits_v2/{rk}/anchor_plan/summary.json"))["rows_detail"]}
    for t in lg["turns"]:
        L = t["llm"]; at = L["attempts"]
        llm_first_in.append(at[0]["metrics"]["total_s"]); llm_first_wall.append(at[0]["wall_s"])
        llm_final_in.append(at[-1]["metrics"]["total_s"])
        for a in at: llm_all_in.append(a["metrics"]["total_s"]); llm_all_wall.append(a["wall_s"])
        llm_turn_total.append(sum(a["metrics"]["total_s"] for a in at))
        n_retry += bool(L["retry_used"]); n_extra += L["extra_turn_retries"]
        synth.append(t["tts"]["synth_s"]); aud.append(t["tts"]["duration_s"])
        r = sm[t["turn_id"]]
        k = "NONE" if t["decision"]["effective"] == "NONE" else "POINT" if t["decision"]["effective"].startswith("POINT") else "PRESET"
        kinds.setdefault(k, []).append({"turn": t["turn_id"], "synth": t["tts"]["synth_s"], "llm_final": at[-1]["metrics"]["total_s"], "llm_wall_final": at[-1]["wall_s"],
                                         "delay": r["delay_s"], "delay0": r["delay_s_preroll0"]})
        if k == "PRESET": delay_pre.append(r["delay_s"]); delay_pre0.append(r["delay_s_preroll0"])
ctc = json.load(open(H / "validation/latency_ctc.json"))
c1 = [r["ctc_s"] for r in ctc["rows"] if r["pass"] == 0]; c2 = [r["ctc_s"] for r in ctc["rows"] if r["pass"] == 1]
pl = [r["plan_s"] for r in ctc["rows"] if r["plan_s"] is not None and r["pass"] == 0]
rb = list(csv.DictReader(open(H / "twin/runs/4c_visits/metrics/turns.csv")))
rtt = [float(r["service_rtt_ms"]) for r in rb if r["service_rtt_ms"]]
# SONIC offline stages per clip
prof = json.load(open(H / "twin/runs/sonic_batch/resource_profile.json"))
by = {}
for r in prof: by.setdefault(r["clip"], {})[r["stage"]] = r["wall_s"]
stages = {}
for c, d in by.items():
    for s, w in d.items(): stages.setdefault(s, []).append(w)
tot = [sum(d.values()) for d in by.values() if len(d) == 5]
stage_stats = {s: S(v) for s, v in sorted(stages.items())}; tot_stats = S(tot)
audio_by_clip = {r["clip"]: r["audio_s"] for r in prof}
tts_med = pct(synth, .5); ctc_med = pct(c1, .5); plan_med = pct(pl, .5); llm_med = pct(llm_final_in, .5)
def e2e(kind):
    ks = kinds[kind]; d = {}
    d["tts_p50"] = pct([k["synth"] for k in ks], .5); d["tts_p95"] = pct([k["synth"] for k in ks], .95)
    return d
res = {
 "llm_in_container_s": {"first_attempt": S(llm_first_in), "final_attempt": S(llm_final_in), "all_attempts_pooled": S(llm_all_in), "per_turn_total_incl_retries": S(llm_turn_total)},
 "llm_docker_exec_wall_s": {"first_attempt": S(llm_first_wall), "all_attempts_pooled": S(llm_all_wall)},
 "llm_retry": {"turns_with_validator_retry": n_retry, "extra_resample_turns": n_extra, "n_turns": len(llm_first_in)},
 "tts_synth_s": S(synth), "tts_audio_dur_s": S(aud), "tts_rtf_p50": round(pct([s / a for s, a in zip(synth, aud)], .5), 3),
 "ctc_align_s_cold_per_wav": S(c1), "ctc_align_s_second_pass_cached_ignore": S(c2), "ctc_model_load_s": ctc["model_load_s"], "ctc_maxrss_mib": ctc["maxrss_mib"],
 "plan_event_s": S(pl),
 "required_audio_start_delay_s_presets_with_MC_preroll": S(delay_pre), "required_audio_start_delay_s_hypothetical_preroll0": S(delay_pre0),
 "mc_service_rtt_ms_4c": S(rtt),
 "sonic_offline_stage_wall_s": stage_stats, "sonic_offline_total_wall_s": tot_stats, "sonic_n_clips": len(tot),
 "n_by_kind": {k: len(v) for k, v in kinds.items()},
}
# end-to-end "Qwen reply complete -> audio start" (sequential, no overlap)
pre = [k["synth"] for k in kinds["PRESET"]]
e = {}
e["PRESET"] = {"formula": "TTS synth + CTC + plan + required start delay (MC pre-roll lead)",
               "p50_s": round(pct([k["synth"] + ctc_med + plan_med + k["delay"] for k in kinds["PRESET"]], .5), 3),
               "p95_s": round(pct([k["synth"] + pct(c1, .95) + pct(pl, .95) + k["delay"] for k in kinds["PRESET"]], .95), 3),
               "of_which_start_delay_p50": round(pct([k["delay"] for k in kinds["PRESET"]], .5), 3)}
e["PRESET_hypothetical_preroll0"] = {"p50_s": round(pct([k["synth"] + ctc_med + plan_med + k["delay0"] for k in kinds["PRESET"]], .5), 3)}
e["NONE_audio_only_today"] = {"formula": "TTS synth (audio can start right after synth, but no co-speech gesture is ready)", "p50_s": round(tts_med, 3), "p95_s": round(pct(synth, .95), 3)}
off = tot_stats["p50"]; off95 = tot_stats["p95"]
e["NONE_with_SONIC_offline"] = {"formula": "TTS synth + StreamTalk+GMR+convert+rollout+prep (offline, sequential)", "p50_s": round(tts_med + off, 1), "p95_s": round(pct(synth, .95) + off95, 1)}
e["POINT_with_SONIC_offline"] = {"formula": "TTS synth + CTC + StreamTalk+GMR+convert+rollout+prep (anchor-timed overlay is then real-time)", "p50_s": round(tts_med + ctc_med + off, 1), "p95_s": round(pct(synth, .95) + pct(c1, .95) + off95, 1)}
res["qwen_reply_to_audio_start"] = e
json.dump(res, open(H / "validation/latency_budget.json", "w"), indent=1)
print(json.dumps(res, indent=1))
