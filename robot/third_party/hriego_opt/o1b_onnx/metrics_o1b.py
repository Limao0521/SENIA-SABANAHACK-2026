#!/usr/bin/env python3
"""Metrics for StreamTalk precision study. python metrics.py -> metrics.json, metrics_summary.csv, metrics_per_clip.csv. See REPORT.md for definitions."""
import json, glob, os, sys
from pathlib import Path
import numpy as np
from scipy.spatial.transform import Rotation as Rot
from scipy import linalg
import librosa
HERE = Path(__file__).resolve().parent; OUT = HERE / "metrics_out"; CLIPS = json.load(open(HERE.parent / "t2_streamtalk_precision/clips.json")); FPS = 30
import paths as P  # run with PYTHONPATH=HRIEGO_gesture root
SUB = os.environ.get("SUBSET", "").split(",") if os.environ.get("SUBSET") else None; SFX = os.environ.get("O1B_SFX", "")
BASE, SEEDREF = os.environ.get("O1B_BASE", "fp32_s1"), "fp32_s2"
GROUPS = {"all": list(range(55)), "body": list(range(22)), "arms": [13, 14, 16, 17, 18, 19, 20, 21], "spine": [3, 6, 9], "neck": [12, 15], "hands": list(range(25, 55))}
sm = np.load(P.smplx_neutral_npz(), allow_pickle=True)
JT = sm["J_regressor"] @ sm["v_template"]; PAR = sm["kintree_table"][0][:55].astype(int); PAR[0] = -1
OFF = JT[:55] - np.where(PAR[:, None] >= 0, JT[np.clip(PAR, 0, None)], 0)
def load(tag, clip):
    z = np.load(OUT / tag / clip / f"{clip}.npz"); return z["poses"].reshape(-1, 55, 3).astype(np.float64)
def mats(p): return Rot.from_rotvec(p.reshape(-1, 3)).as_matrix().reshape(-1, 55, 3, 3)
def geo(pa, pb):  # (T,55) degrees
    Ra, Rb = mats(pa), mats(pb); R = np.einsum("tjki,tjkl->tjil", Ra, Rb)
    return np.degrees(np.linalg.norm(Rot.from_matrix(R.reshape(-1, 3, 3)).as_rotvec(), axis=1)).reshape(-1, 55)
def fk(p):  # wrist/all joint positions (T,55,3), pelvis orient included, no trans
    R = mats(p); T = len(p); G = np.zeros((T, 55, 3, 3)); X = np.zeros((T, 55, 3))
    for j in range(55):
        if PAR[j] < 0: G[:, j] = R[:, j]; X[:, j] = 0
        else: G[:, j] = G[:, PAR[j]] @ R[:, j]; X[:, j] = X[:, PAR[j]] + G[:, PAR[j]] @ OFF[j]
    return X
def angvel(p):  # local-frame angular velocity vectors (T-1,55,3) rad/s
    R = mats(p); rel = np.einsum("tjki,tjkl->tjil", R[:-1], R[1:])
    return Rot.from_matrix(rel.reshape(-1, 3, 3)).as_rotvec().reshape(-1, 55, 3) * FPS
def motion_stats(p):
    w = angvel(p); sp = np.linalg.norm(w, axis=-1); a = np.diff(w, axis=0) * FPS; jk = np.diff(w, 2, axis=0) * FPS * FPS
    X = fk(p); v = np.diff(X, axis=0) * FPS; wr = np.linalg.norm(v[:, [20, 21]], axis=-1)
    wa = np.diff(v, axis=0) * FPS; wj = np.diff(v, 2, axis=0) * FPS * FPS
    return dict(sp=sp, jerk_ang=np.linalg.norm(jk, axis=-1), wr=wr, wr_jerk=np.linalg.norm(wj[:, [20, 21]], axis=-1))
def audio_env(clip, T):
    y, sr = librosa.load(CLIPS[clip], sr=16000); hop = 533
    rms = librosa.feature.rms(y=y, frame_length=1066, hop_length=hop)[0]; on = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop)
    f = lambda e: np.interp(np.arange(T), np.arange(len(e)) * hop / sr * FPS, e)
    return f(rms), f(on)
def peaks(x, thr_rel=0.3):
    from scipy.signal import find_peaks
    pk, _ = find_peaks(x, height=thr_rel * x.max() if x.max() > 0 else 0, distance=5); return pk
def sync_metrics(p, clip):
    st = motion_stats(p); T = len(p); ws = np.r_[st["wr"].max(1), st["wr"].max(1)[-1:]][:T]
    rms, on = audio_env(clip, T); ws = np.r_[ws, np.full(T - len(ws), ws[-1])] if len(ws) < T else ws
    def corr(a, b):
        return float(np.corrcoef(a, b)[0, 1]) if a.std() > 1e-9 and b.std() > 1e-9 else 0.0
    lags = range(-10, 11); cr = [corr(ws[max(0, l):T + min(0, l)], rms[max(0, -l):T - max(0, l)]) for l in lags]
    co = [corr(ws[max(0, l):T + min(0, l)], on[max(0, -l):T - max(0, l)]) for l in lags]
    # beat alignment: motion beats = local minima of smoothed wrist speed (arms), audio beats = onset peaks; sigma = 3 frames (EMAGE/Bailando style)
    from scipy.ndimage import gaussian_filter1d
    sm_ = gaussian_filter1d(ws, 1.5); mb = peaks(-sm_ + sm_.max(), 0.0); ab = peaks(on)
    bas = float(np.mean(np.exp(-np.min((mb[:, None] - ab[None]) ** 2, axis=1) / (2 * 3 ** 2)))) if len(mb) and len(ab) else float("nan")
    return dict(corr_rms0=cr[10], corr_rms_best=float(max(cr, key=abs)), corr_onset0=co[10], corr_onset_best=float(max(co, key=abs)), bas=bas, n_mb=len(mb), n_ab=len(ab))
def feats(p):  # windowed features for FGD-like distance (2 s windows, 1 s stride), 8-d
    st = motion_stats(p); out = []
    for s in range(0, max(1, len(p) - 1 - 59), 30):
        sl = slice(s, s + 60); sp = st["sp"][sl]
        out.append([sp[:, GROUPS["arms"]].mean(), np.percentile(sp[:, GROUPS["arms"]], 95), sp[:, GROUPS["spine"] + GROUPS["neck"]].mean(), sp[:, GROUPS["hands"]].mean(),
                    st["wr"][sl].mean(), np.percentile(st["wr"][sl], 95), st["wr_jerk"][sl].mean(), st["jerk_ang"][sl][:, GROUPS["arms"]].mean()])
    return np.array(out)
def frechet(A, B):
    if len(A) < 3 or len(B) < 3: return float("nan")
    mu = np.r_[A, B].mean(0); sd = np.r_[A, B].std(0) + 1e-9; A = (A - mu) / sd; B = (B - mu) / sd
    m1, m2 = A.mean(0), B.mean(0); c1, c2 = np.cov(A, rowvar=False), np.cov(B, rowvar=False)
    cs, _ = linalg.sqrtm(c1 @ c2, disp=False); cs = cs.real
    return float(((m1 - m2) ** 2).sum() + np.trace(c1) + np.trace(c2) - 2 * np.trace(cs))
def stat(x, f=np.mean): return float(f(x))
def tags_present(): return sorted(d.name for d in OUT.iterdir() if (d / "run.json").exists())
def main():
    tags = [t for t in tags_present() if not SUB or t.startswith(("i8dyn","fp32","fp16","bf16","i8sim"))]; res = {}; perclip = []; allp = {}
    # absolute stats per variant
    for tag in tags:
        clips = [c for c in CLIPS if (OUT / tag / c).exists() and (not SUB or c in SUB)]; allp[tag] = {c: load(tag, c) for c in clips}
    def compare(tag, ref):
        cl = [c for c in allp[tag] if c in allp[ref]]; r = {"clips": cl, "n_clips": len(cl)}; E = {g: [] for g in GROUPS}; win = {}; cm = {}
        for c in cl:
            e = geo(allp[tag][c], allp[ref][c]); T = len(e)
            for g, ix in GROUPS.items(): E[g].append(e[:, ix].reshape(-1))
            cm[c] = {g: float(e[:, ix].mean()) for g, ix in GROUPS.items()}
            for s in range(0, T, 60): win.setdefault(s // 60, []).append(e[s:s + 60][:, GROUPS["body"]].mean())
            tr = [np.abs(np.load(OUT / t / c / f"{c}.npz")["trans"]) for t in (tag,)]
        for g in GROUPS: v = np.concatenate(E[g]); r[f"err_{g}_mean"] = float(v.mean()); r[f"err_{g}_p95"] = float(np.percentile(v, 95))
        r["drift_body_per2s"] = [float(np.mean(win[k])) for k in sorted(win)]; r["per_clip_err_body"] = {c: cm[c]["body"] for c in cl}; r["per_clip_err_all"] = {c: cm[c]["all"] for c in cl}
        r["trans_mae_m"] = float(np.mean([np.abs(np.load(OUT / tag / c / f"{c}.npz")["trans"] - np.load(OUT / ref / c / f"{c}.npz")["trans"]).mean() for c in cl]))
        return r
    for tag in tags:
        cl = list(allp[tag]); ms = {c: motion_stats(allp[tag][c]) for c in cl}; sy = {c: sync_metrics(allp[tag][c], c) for c in cl}
        pool = lambda k, ix=None: np.concatenate([(ms[c][k] if ix is None else ms[c][k][:, ix]).reshape(-1) for c in cl])
        d = dict(n_clips=len(cl))
        for g in ("arms", "spine", "neck", "hands"):
            s_ = pool("sp", GROUPS[g]); j_ = pool("jerk_ang", GROUPS[g]); d[f"vel_{g}_mean"] = float(s_.mean()); d[f"vel_{g}_p95"] = float(np.percentile(s_, 95)); d[f"jerk_{g}_mean"] = float(j_.mean()); d[f"jerk_{g}_p95"] = float(np.percentile(j_, 95))
        w = pool("wr"); wj = pool("wr_jerk"); d.update(wrist_speed_mean=float(w.mean()), wrist_speed_p95=float(np.percentile(w, 95)), wrist_jerk_mean=float(wj.mean()), wrist_jerk_p95=float(np.percentile(wj, 95)))
        for k in ("corr_rms0", "corr_rms_best", "corr_onset0", "corr_onset_best", "bas"): d[k] = float(np.nanmean([sy[c][k] for c in cl]))
        res[tag] = dict(abs=d, sync_per_clip=sy)
    F = {t: np.concatenate([feats(allp[t][c]) for c in allp[t]]) for t in tags}
    for tag in tags:
        res[tag]["vs_" + BASE] = compare(tag, BASE) if tag != BASE and BASE in allp else None
        res[tag]["fgd_vs_" + BASE] = frechet(F[tag], F[BASE]) if tag != BASE else 0.0
    # extra pairs
    if "fp16_s2" in allp and "fp16_s1" in allp: res["fp16_s2"]["vs_fp16_s1"] = compare("fp16_s2", "fp16_s1")
    # stored outputs vs regenerated (default pipeline = fp16)
    stored = {}
    for c, wav in CLIPS.items():
        if c.startswith("v_"):
            n = Path(wav).parent.parent.name; sp = Path(wav).parent.parent / "smplx" / f"{n}.npz"
            if sp.exists() and "fp16_s1" in allp and c in allp["fp16_s1"]:
                z = np.load(sp); pst = z["poses"].reshape(-1, 55, 3).astype(float); pn = allp["fp16_s1"][c]
                k = min(len(pst), len(pn)); stored[c] = dict(frames_stored=len(pst), frames_new=len(pn), geo_mean_deg=float(geo(pst[:k], pn[:k]).mean()) if pst.shape == pn.shape or k else None, keys=list(z.keys()))
    res["_stored_vs_fp16_s1"] = stored; res["_fgd_note"] = "Frechet on 8-d standardised windowed features; n windows=" + str({t: len(F[t]) for t in tags})
    json.dump(res, open(HERE / f"metrics{SFX}.json", "w"), indent=1)
    import csv
    rows = []
    for tag in tags:
        a = res[tag]["abs"]; v = res[tag].get("vs_" + BASE) or {}
        rows.append(dict(variant=tag, n_clips=a["n_clips"], err_all_mean=v.get("err_all_mean"), err_all_p95=v.get("err_all_p95"), err_arms_mean=v.get("err_arms_mean"), err_arms_p95=v.get("err_arms_p95"), err_spine_mean=v.get("err_spine_mean"), err_neck_mean=v.get("err_neck_mean"),
                         err_body_mean=v.get("err_body_mean"), drift_first2s=(v.get("drift_body_per2s") or [None])[0], drift_last2s=(v.get("drift_body_per2s") or [None])[-1], trans_mae_m=v.get("trans_mae_m"), fgd=res[tag]["fgd_vs_" + BASE], **{k: a[k] for k in a if k != "n_clips"}))
    with open(HERE / f"metrics_summary{SFX}.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    print(open(HERE / f"metrics_summary{SFX}.csv").read()[:3000])
main()
