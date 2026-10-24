#!/usr/bin/env python3
"""O3 BRAIN driver (host, physicalai): M2 server with O1a V2 models + first-block split; streams tick chunks to the motor over the AF_UNIX socket.
usage: brain_run.py --tag B1 [--split 12] [--models v2] [--turns c001,PRESET:vip:v2visit_vip_t00_greeting,...] [--guard 0.15] [--gap 1.0] [--lat-bench]"""
import argparse, json, socket, threading, time
import numpy as np
from brain_server import *
from o3common import *
HG = HRIEGO
ap = argparse.ArgumentParser(); ap.add_argument("--tag", required=True); ap.add_argument("--split", type=int, default=12); ap.add_argument("--models", default="v2")
ap.add_argument("--guard", type=float, default=0.15); ap.add_argument("--gap", type=float, default=1.0); ap.add_argument("--lat-bench", action="store_true")
ap.add_argument("--turns", default="c001,c004,PRESET:vip:v2visit_vip_t00_greeting,c005,c010,PRESET:vip:v2visit_vip_t03_dialogue,c016,c018,PRESET:vip:v2visit_vip_t02_dialogue,v_guest_t00_greeting_EMOTE,v_guest_t01_guidance_POINT,v_regular_t02_dialogue_NONE,v_staff_t05_farewell_NONE")
ap.add_argument("--no-motor", action="store_true")
a = ap.parse_args()
out = Path(__file__).resolve().parent / "out" / a.tag; out.mkdir(parents=True, exist_ok=True)
import librosa
clips = json.load(open(CLIPS_JSON)); LAT = json.load(open(HG / "twin/replay/presets_c08/preset_latency.json"))["bindings"]


def preset_turn(rk, tid):
    t = [json.loads(l) for l in open(HG / "visits_v2" / rk / "tts_manifest.jsonl") if json.loads(l)["turn_id"] == tid][0]
    ev = json.load(open(HG / f"visits_v2/{rk}/anchor_plan/events/{tid}.event.json")); b = LAT[f"{t['motion_id']}/{t['area']}"]
    anc = ev["anchor"]["start_sample"] / ev["audio"]["sample_rate_hz"]; lo, ls = b["lead_onset_s"]["median"], b["lead_stroke_s"]["median"]
    T = dict(turn_id=tid, motion=t["motion_id"], area=t["area"], emote=t["emote"], anchor_s=anc, max_initial_delay_s=1.5, preset_dur_s=b["duration_s"]["median"],
             policy_dispatch_s={"stroke": anc - ls, "mid": anc - (lo + ls) / 2, "onset": anc - lo})
    return T, str(HG / f"visits_v2/{rk}/sonic/{tid}/wav16/{tid}.wav")


b = Brain(models=a.models, armed=True); ld = b.load(); print("LOAD", ld, flush=True)
b.arm(); w, _ = b.run(librosa.load(clips["c010"], sr=16000)[0][:48000], split=a.split); print("WARM", w["first_cmd_s"], flush=True)
res_all = {"load": ld, "args": vars(a)}
# ---- first-block split micro-benchmark (no sink; interleaved; 4 s audio, armed)
if a.lat_bench:
    lb = {0: [], 13: [], 14: [], 16: []}; au = [librosa.load(clips[c], sr=16000)[0][:64000] for c in ("c001", "c010", "c018")]
    for rep in range(5):
        for sp in (0, 13, 14, 16):
            for x in au:
                b.arm(); r, _ = b.run(x, split=sp); lb[sp].append(r["first_cmd_s"]); trim()
    res_all["split_bench"] = {str(k): dict(mean=float(np.mean(v)), std=float(np.std(v)), min=float(np.min(v)), n=len(v)) for k, v in lb.items()}; print("SPLIT", res_all["split_bench"], flush=True)
sock = None; events = []
if not a.no_motor:
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); sock.connect(SOCK)
    def rd():
        try:
            while True:
                t, p = recv_frame(sock)
                if t == 1: p["t_host"] = time.monotonic(); events.append(p)
        except Exception: pass
    threading.Thread(target=rd, daemon=True).start()
    t_w = time.time()
    while not any(e.get("op") == "ready" for e in events) and time.time() - t_w < 30: time.sleep(0.05)
    print("motor ready", flush=True)
rows = []
for idx, spec in enumerate(a.turns.split(",")):
    preset = None
    if spec.startswith("PRESET:"):
        _, rk, tid = spec.split(":"); preset, wav = preset_turn(rk, tid); name = tid; kind = "preset"
    else: name = spec; wav = clips[spec]; kind = "sonic"
    audio = librosa.load(wav, sr=16000)[0]; dur = len(audio) / 16000
    b.arm(); st = {}; done_ev = len(events)
    def on_first(rel):
        tm = time.monotonic(); prefix_n = PREFIX[b.prefix][0] + int(round(PREFIX[b.prefix][1] * 50))
        lead = -start_offset(prefix_n, preset); T_a0 = tm + lead + a.guard
        st.update(t_first_mono=tm, T_a0=T_a0, prefix_n=prefix_n, lead=lead)
        if sock: send_json(sock, dict(op="turn", turn=idx, kind=kind, name=name, prefix_n=prefix_n, T_a0=T_a0, dur_s=dur, t_req_mono=st["t_req"], t_first_mono=tm, preset=preset))
    sink = (lambda k0, arr, tm: send_chunk(sock, idx, k0, arr, tm)) if sock else None
    st["t_req"] = time.monotonic()
    r, d = b.run(audio, sink=sink, split=a.split, on_first=on_first)
    if sock: send_json(sock, dict(op="turn_end", turn=idx, n_ticks=int(r["ticks"])))
    r.pop("windows"); r.update(idx=idx, name=name, kind=kind, T_a0=st["T_a0"], t_req_mono=st["t_req"], t_first_mono=st["t_first_mono"], t_done_mono=time.monotonic(), dur_audio=dur, guard=a.guard, lead_s=st["lead"],
                              time_to_audio_start_s=st["T_a0"] - st["t_req"], preset=preset)
    np.savez(out / f"{idx:02d}_{name}.npz", **d); rows.append(r)
    print(idx, name, kind, f"first_cmd {r['first_cmd_s']:.3f} audio_start+{r['time_to_audio_start_s']:.2f}s rtf {r['rtf']:.2f} fell {r['fell']}", flush=True)
    if sock:
        t_w = time.time()
        while not any(e.get("op") == "turn_done" and e.get("turn") == idx for e in events) and time.time() - t_w < 40: time.sleep(0.05)
        time.sleep(a.gap)
    else: time.sleep(0.1)
    trim()
if sock:
    send_json(sock, dict(op="quit")); t_w = time.time()
    while not any(e.get("op") == "bye" for e in events) and time.time() - t_w < 40: time.sleep(0.1)
res_all.update(turns=rows, motor_events=events)
json.dump(res_all, open(out / "brain.json", "w"), indent=1, default=float)
print("DONE", flush=True); os._exit(0)
