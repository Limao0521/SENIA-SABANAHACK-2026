#!/usr/bin/env python3
"""4d: time CTC alignment (QuartzNet CPU, 2 threads) and plan_event per stored visit row; read-only. physicalai python.
Writes validation/latency_ctc.json"""
import json, resource, sys, time
from pathlib import Path
H = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(H)); sys.path.insert(0, str(H / "align"))
from paths import DEFAULT_LATENCY, resolve  # noqa
from vendor.align_anchor import Aligner  # noqa  (needs $QUARTZNET_ONNX)
from vendor.event_builder import build_event  # noqa
from vendor.preset_scheduler import load_latency, plan_event_auto  # noqa
table = load_latency(DEFAULT_LATENCY)
t0 = time.perf_counter(); al = Aligner(); load_s = time.perf_counter() - t0
rows = []
for rk in ["vip", "regular", "guest", "staff", "external"]:
    for l in open(H / f"visits_v2/{rk}/tts_manifest.jsonl"):
        r = json.loads(l)
        if r["emote"] == "NONE": continue
        for rep in range(2):  # 2 passes: first includes ORT warm-up effects
            t1 = time.perf_counter(); sp = al.anchor_for_wav(str(resolve(r["wav_path"])), r["speech"], r["trigger_char_start"], r["trigger_char_end"]); ctc = time.perf_counter() - t1
            plan_s = None
            if r["motion_id"] is not None and sp["status"].startswith("aligned"):
                b = table["bindings"][f"{r['motion_id']}/{r['area']}"]
                dec = {"emit_event": True, "status": "ok", "effective": {"communicative_act": r["emote"], "motor_intent": r["emote"], "decision_source": "hriego_manifest", "anchor": {"text": sp["text"]},
                       "binding": {"motion_id": r["motion_id"], "area": r["area"], "asset_sha256": b.get("csv_sha256"), "animation_player_sha256": b.get("yaml_sha256")}}}
                t2 = time.perf_counter(); ev, _ = build_event(r["case_id"], dec, sp, r["wav_sha256"], sp["sample_rate_hz"], sp["sample_count"], al.model_info["model_revision"])
                plan_event_auto(ev, table, str(resolve(r["wav_path"])), "median", 1.5); plan_s = time.perf_counter() - t2
            rows.append({"case": r["case_id"], "pass": rep, "audio_s": r["duration_s"], "ctc_s": ctc, "plan_s": plan_s, "status": sp["status"]})
al.close()
json.dump({"model_load_s": load_s, "maxrss_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, "rows": rows}, open(H / "validation/latency_ctc.json", "w"), indent=1)
print(load_s, resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, len(rows))
