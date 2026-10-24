"""P4 timeline builder (host). Conversation clock c (s): line 1 starts at S1=3.5 s (pre-roll so the "Hola" bow stroke can land on the anchor), line k starts at
t_k = end(k-1) + 0.30 s, end = start + WAV duration (full WAV, incl. its own edge silence). Per robot: timeline_<robot>.json (lines, presets, head plan) and
plans/ref_<robot>.npz = 50 Hz reference on the clock: arms (14) = SONIC co-speech while speaking (entry min-jerk fade READY->SONIC(0) 0.6 s before the line, exit fade 0.6 s to READY,
direct min-jerk crossfade when the gap < 1.2 s), READY otherwise; wh (4) = SONIC waist yaw/pitch/roll + head yaw (Original head = 0; Ultra head = SONIC + look-at-Original plan)."""
import sys as _sys, pathlib as _pl; _sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
from paths import laplace_root as _lr, semantic_corporea_root as _sc, py_physicalai as _pyp, conda_exe as _conda, commercial_show_dir; _LR = _lr(); _SC = _sc()
import csv, json, wave
from pathlib import Path
import numpy as np
S = commercial_show_dir(); H = Path(__file__).resolve().parent; O = H / "runs/p4_commercial"
C8 = str(_LR / "experiments/RCP-01/runs/20261021_rcp01c08_preset_latency_twin/metrics/preset_latency.json")
TICK, S1, GAP, E_IN, E_OUT, L_OUT, LOOK, LOOK_T = 0.02, 3.5, 0.30, 0.6, 0.9, 0.6, 0.35, 0.5
READY = np.array([0.2, 0.2, 0.0, -0.6, 0.0, 0.0, 0.0, 0.2, -0.2, 0.0, -0.6, 0.0, 0.0, 0.0])
mj = lambda s: (lambda s: s ** 3 * (10 - 15 * s + 6 * s * s))(np.clip(s, 0, 1))
rows = list(csv.DictReader(open(S / "orden_de_pistas.csv", encoding="utf-8-sig"))); lat = json.load(open(C8))["bindings"]
lines = []; t = S1
for r in rows:
    w = wave.open(str(S / "audio" / r["archivo"])); d = w.getnframes() / w.getframerate(); clip = r["archivo"][:-4]
    lines.append(dict(n=int(r["orden"]), clip=clip, robot="ultra" if r["reproducir_en"] == "X2 Ultra" else "original", start=t, dur=d, end=t + d, text=r["parlamento"], scene=r["escena"])); t += d + GAP
C_END = lines[-1]["end"] + 2.0
anc = {n: json.load(open(O / f"plans/anchor/events/p4_{n:03d}.anchor.json")) for n in (1, 5, 19)}
EM = {  # line -> {robot: (motion, area, label)}
    1: {"ultra": (3001, 11, "BOW (VIP bow 3001/11)"), "original": (3001, 11, "BOW (VIP bow 3001/11)")},
    5: {"ultra": (3004, 11, "HEART (3004/11)"), "original": (3017, 11, "APPLAUSE (3017/11)")},
    19: {"ultra": (3031, 11, "FAREWELL (3031/11)"), "original": (3031, 11, "FAREWELL (3031/11)")}}
def sonic_of(l):
    z = np.load(S / "motion" / l["clip"] / f"{l['clip']}_arms.npz"); a = z["cmd"][100:]; w = np.asarray(z["waist_head"])[100:, :4].copy()
    n = int(round(l["dur"] / TICK)) + 1; idx = np.minimum(np.arange(n), len(a) - 1); return a[idx], w[idx]
def val(l, tau, cache={}):
    if l["n"] not in cache: cache[l["n"]] = sonic_of(l)
    a, w = cache[l["n"]]; f = np.clip(tau / TICK, 0, len(a) - 1); i = int(f); j = min(i + 1, len(a) - 1); r = f - i
    return np.concatenate([a[i] * (1 - r) + a[j] * r, w[i] * (1 - r) + w[j] * r])
Z18 = np.concatenate([READY, np.zeros(4)])
def build(robot):
    L = [l for l in lines if l["robot"] == robot]; N = int(C_END / TICK) + 1; ref = np.tile(Z18, (N, 1)); who = np.zeros(N, int)
    for k in range(N):
        c = k * TICK; pv = [l for l in L if l["end"] <= c]; nx = [l for l in L if l["start"] > c]; cur = [l for l in L if l["start"] <= c < l["end"]]
        XW = 1.0   # same-robot lines with gap < 1.2 s: crossfade window XW centred on the gap, mixing the two RUNNING clips (continuous, no endpoint jump)
        xf = [(p_, q_) for p_, q_ in zip(L[:-1], L[1:]) if q_["start"] - p_["end"] < E_OUT + E_IN and (p_["end"] + q_["start"]) / 2 - XW / 2 <= c <= (p_["end"] + q_["start"]) / 2 + XW / 2]
        if xf:
            p_, q_ = xf[0]; mid = (p_["end"] + q_["start"]) / 2; w_ = mj((c - (mid - XW / 2)) / XW)
            ref[k] = (1 - w_) * val(p_, c - p_["start"]) + w_ * val(q_, c - q_["start"]); who[k] = p_["n"] if w_ < 0.5 else q_["n"]; continue
        if cur: ref[k] = val(cur[0], c - cur[0]["start"]); who[k] = cur[0]["n"]; continue
        p = pv[-1] if pv else None; q = nx[0] if nx else None
        A = val(p, p["dur"]) if p else None; B = val(q, 0.0) if q else None
        if True:
            v = Z18.copy()
            if p and c < p["end"] + E_OUT: v = A + mj((c - p["end"]) / E_OUT) * (Z18 - A)
            if q and c > q["start"] - E_IN: v = Z18 + mj((c - (q["start"] - E_IN)) / E_IN) * (B - Z18)
            ref[k] = v
    # presets
    P = []
    for n, d in EM.items():
        m, a, lab = d[robot]; l = lines[n - 1]; b = lat[f"{m}/{a}"]; ls, lo = b["lead_stroke_s"]["median"], b["lead_onset_s"]["median"]; ab = l["start"] + anc[n]["start_s"]
        pol = "stroke"; disp = ab - ls
        if disp < 1.0: pol = "mid"; disp = ab - (ls + lo) / 2
        P.append(dict(id=f"{robot}_{n:03d}_{m}", line=n, motion=m, area=a, label=lab, anchor_abs=ab, anchor_text=anc[n]["text"], lead_stroke_s=ls, lead_onset_s=lo, policy=pol, dispatch=disp,
                      t_handoff=disp - 0.06, t_fade0=disp - 0.06 - L_OUT, preset_dur_s=b["duration_s"]["median"], motion_dur_s=b["motion_duration_s"]["median"]))
    P.sort(key=lambda x: x["dispatch"])
    wh = ref[:, 14:]; look_iv = []
    if robot == "original": wh[:, 3] = 0.0
    else:
        look = np.zeros(N); ts = np.arange(N) * TICK
        for l in lines:
            if l["robot"] != "original" or l["n"] == 19: continue
            a_, b_ = l["start"], l["end"]
            for p in P:                                  # head must be back at 0 before an Ultra preset starts (head is frozen during presets): finish the return by dispatch - 0.1
                if a_ < p["dispatch"] <= b_ + 3.0: b_ = min(b_, p["dispatch"] - 0.1 - LOOK_T)
            if b_ > a_ + LOOK_T: look += LOOK * (mj((ts - a_) / LOOK_T) - mj((ts - b_) / LOOK_T)); look_iv.append([a_, b_, l["n"]])
        wh[:, 3] += look
    return dict(robot=robot, ref=ref, lines=[dict(l, speaks=(l["robot"] == robot)) for l in lines], presets=P, look_intervals=look_iv)
if __name__ == "__main__":
    json.dump(dict(S1=S1, GAP=GAP, C_END=C_END, lines=lines), open(O / "conversation_clock.json", "w"), indent=1, ensure_ascii=False)
    for rb in ("ultra", "original"):
        T = build(rb); np.savez(O / f"plans/ref_{rb}.npz", arms=T["ref"][:, :14], wh=T["ref"][:, 14:], tick=TICK)
        json.dump({k: v for k, v in T.items() if k != "ref"} | dict(C_END=C_END, tick=TICK, params=dict(S1=S1, GAP=GAP, E_IN=E_IN, E_OUT=E_OUT, L_OUT=L_OUT, LOOK=LOOK, LOOK_T=LOOK_T)), open(O / f"timeline_{rb}.json", "w"), indent=1, ensure_ascii=False)
        r = T["ref"]; v = np.abs(np.diff(r[:, :14], axis=0)).max(1) / TICK
        print(rb, "ticks", len(r), "ref max arm speed", round(float(v.max()), 2), "at c=", round(float(np.argmax(v)) * TICK, 2))
        for p in T["presets"]: print("  ", p["id"], "anchor", round(p["anchor_abs"], 3), "dispatch", round(p["dispatch"], 3), p["policy"], "fade0", round(p["t_fade0"], 2), "end~", round(p["dispatch"] + p["preset_dur_s"], 2))
        print("  look", [[round(x, 2) for x in iv] for iv in T["look_intervals"]])
    for l in lines: print(l["n"], l["robot"], round(l["start"], 2), round(l["end"], 2))
