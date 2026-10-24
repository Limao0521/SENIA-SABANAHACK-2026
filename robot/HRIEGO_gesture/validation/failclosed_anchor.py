#!/usr/bin/env python3
"""4d fail-closed injection tests for align/anchor_plan.py (real CLI, real QuartzNet, CPU). Run with the physicalai python.
Mutates copies of stored manifest rows / latency table in a temp dir; stored artifacts are never modified. Prints JSON list of results."""
import copy, json, shutil, subprocess, sys, tempfile
from pathlib import Path
H = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(H))
from paths import DEFAULT_LATENCY, py_physicalai, resolve  # noqa
PY = py_physicalai()
LAT0 = str(DEFAULT_LATENCY)
def row(rk, tid):
    for l in open(H / f"visits_v2/{rk}/tts_manifest.jsonl"):
        r = json.loads(l)
        if r["turn_id"] == tid: return r
BASE = row("vip", "v2visit_vip_t03_dialogue")          # SOCIAL_HEART 3004/11 anchor 0.88 s
EXT_GREET = row("external", "v2visit_external_t00_greeting")  # 1002/2, onset lead 1.20, stroke lead 2.22 (stroke delay would be ~1.56 s)
POINT = row("vip", "v2visit_vip_t01_guidance")
NONE = row("vip", "v2visit_vip_t04_dialogue")
tmp = Path(tempfile.mkdtemp(prefix="failclosed_"))
lat = json.load(open(LAT0))

def latvariant(name, fn):
    t = copy.deepcopy(lat); fn(t); p = tmp / f"lat_{name}.json"; json.dump(t, open(p, "w")); return str(p)

def run(name, rows, expect_status, extra_args=(), latency=None, expect_no_dispatch=True, note=""):
    d = tmp / name; d.mkdir()
    mp = d / "manifest.jsonl"; mp.write_text("".join(json.dumps(r) + "\n" for r in rows))
    cmd = [PY, str(H / "align/anchor_plan.py"), "--manifest", str(mp), "--out", str(d / "out")] + list(extra_args)
    if latency: cmd += ["--latency", latency]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    res = {"name": name, "rc": p.returncode, "expected_status": expect_status, "note": note}
    try:
        s = json.load(open(d / "out/summary.json")); r0 = s["rows_detail"][0]
        res.update(status=r0["status"], reason=r0["reason"])
        cid = r0["case_id"]
        pf = d / f"out/plans/{cid}.plan.json"
        plan = json.load(open(pf)) if pf.exists() else None
        ev = (d / f"out/events/{cid}.event.json").exists()
        res["plan_file"] = bool(plan); res["plan_status"] = plan and plan["status"]
        disp = bool(plan and (plan["dispatches"] or plan["dispatch_sample"] is not None))
        res["dispatch_emitted"] = disp
        ok = res["status"] == expect_status and (not disp if expect_no_dispatch else True)
    except Exception as e:  # noqa
        res.update(error=f"{type(e).__name__}: {e}", stderr=p.stderr[-400:]); ok = False
        res["status"] = None; res["dispatch_emitted"] = None
    res["result"] = "PASS" if ok else "FAIL"
    return res

def mut(r, **kw):
    r = dict(r); r.update(kw); return r
out = []
bad_sha = BASE["wav_sha256"][:-1] + ("0" if BASE["wav_sha256"][-1] != "0" else "1")
out.append(run("wrong_wav_hash", [mut(BASE, wav_sha256=bad_sha)], "rejected_hash"))
tw = tmp / "tampered.wav"; shutil.copy(resolve(BASE["wav_path"]), tw)
with open(tw, "r+b") as f: f.seek(2000); b = f.read(1); f.seek(2000); f.write(bytes([b[0] ^ 0xFF]))
out.append(run("wav_file_tampered_byte", [mut(BASE, wav_path=str(tw))], "rejected_hash", note="one flipped byte in the WAV copy; manifest sha unchanged"))
out.append(run("wav_missing", [mut(BASE, wav_path=str(tmp / "nope.wav"))], "rejected_hash"))
out.append(run("manifest_sample_rate_mismatch", [mut(BASE, sample_rate=16000)], "rejected_hash"))
out.append(run("missing_latency_entry", [BASE], "rejected_resources",
               latency=latvariant("nobinding", lambda t: t["bindings"].pop("3004/11"))))
out.append(run("latency_entry_without_duration", [BASE], "rejected_resources",
               latency=latvariant("nodur", lambda t: t["bindings"]["3004/11"].pop("duration_s"))))
(tmp / "garbage_lat.json").write_text("{not json")
out.append(run("latency_table_unreadable", [BASE], "rejected_resources", latency=str(tmp / "garbage_lat.json")))
out.append(run("start_delay_over_cap_stroke_policy", [EXT_GREET], "rejected_horizon", ["--align", "stroke"],
               note="1002/2 stroke lead 2.22 s - anchor 0.66 s = 1.56 s > 1.5 s cap"))
out.append(run("start_delay_over_cap_inflated_lead", [BASE], "rejected_horizon",
               ["--align", "onset"], latency=latvariant("lead3", lambda t: t["bindings"]["3004/11"]["lead_onset_s"].update(median=3.0)),
               note="onset lead forced to 3.0 s -> delay 2.12 s"))
out.append(run("trigger_span_empty", [mut(BASE, trigger_char_start=5, trigger_char_end=5)], "alignment_error"))
out.append(run("trigger_span_beyond_speech", [mut(BASE, trigger_char_start=200, trigger_char_end=210)], None, note="probe: span outside speech must not plan"))
out.append(run("POINT_no_preset_no_dispatch", [POINT], "anchor_only_no_preset", expect_no_dispatch=True))
out.append(run("NONE_with_stray_trigger_fields", [mut(NONE, trigger_text="Gracias", trigger_char_start=0, trigger_char_end=7, motion_id=3004, area=11)], "none_sonic",
               note="emote NONE with leftover trigger/motion fields: nothing aligned, nothing dispatched"))
# G1 guard: anchor_plan must refuse a manifest whose trigger_text is not the literal speech[start:end]
out.append(run("G1_trigger_text_vs_span_mismatch", [mut(BASE, trigger_text="ayudarte")], "rejected_trigger_span",
               note="trigger_text 'ayudarte' but span [0:7]='Gracias' -> reject (speech[start:end] != trigger_text); no event, no plan."))
out.append(run("G1_trigger_text_missing", [mut(BASE, trigger_text=None)], "rejected_trigger_span", note="trigger_text null with a valid span -> reject."))
out.append(run("auto_policy_falls_back_when_stroke_over_cap", [EXT_GREET], "planned", expect_no_dispatch=False,
               note="default --align auto: stroke needs 1.56 s > 1.5 s cap -> midpoint (or onset) is used; must plan, not reject."))
for r in out:
    if r["name"] == "trigger_span_beyond_speech":
        r["expected_status"] = "!= planned"; r["result"] = "PASS" if (r["status"] not in (None, "planned") and not r.get("dispatch_emitted")) else "FAIL"
json.dump(out, sys.stdout, ensure_ascii=False, indent=1)
