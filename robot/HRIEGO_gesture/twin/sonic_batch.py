"""Profiled SONIC base batch (4aa pointing WAVs + visit NONE/POINT turns). Adapted from sonic_pipeline.py/run_sonic_pipeline.sh (same S0-S5 commands).
Run with the physicalai python. Strictly sequential, one heavy process at a time; each stage wrapped in /usr/bin/time -v + nvidia-smi sampler.
Usage: sonic_batch.py [--only name,...]"""
import sys as _sys, pathlib as _pl; _sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
from paths import laplace_root as _lr, semantic_corporea_root as _sc, py_physicalai as _pyp, conda_exe as _conda; _LR = _lr(); _SC = _sc()
import csv, hashlib, json, re, shutil, subprocess, sys, time, os
from pathlib import Path
import numpy as np, soundfile as sf
from scipy.signal import resample_poly
H = Path(__file__).resolve().parent; B = H / "runs/sonic_batch"; V = H.parent / "visits_v2"
ROOT = _LR; L = ROOT / "experiments/RCP-01/runs"
C3 = L / "20261020_rcp01c03_segment_scheduler/scripts"; C7 = L / "20261021_rcp01c07_ready_pose"; C2 = L / "20261019_rcp01c02_fixedcalm"
SCRIPT = C2 / "scripts/streamtalk_cached_entry.py"; CACHE = C2 / "cache/streamtalk_retrieval"
CKPT = ROOT / "Gesture/third_party/StreamTalk/checkpoints/pretrained/streamtalk_speaker_all_e0940_cfg3.pt"
READY = C7 / "configs/streamtalk_ready_prompt.npz"; WAVLM = ROOT / "Gesture/third_party/StreamTalk/assets/wavlm-libri-clean-100h-large"
SMPLX = (_SC / "third_party/GMR/assets/body_models/smplx/SMPLX_NEUTRAL.npz")
DATA = (_SC / "data/raw/BEAT2/beat_english_v2.0.0/smplxflame_30")
CSV = ROOT / "Gesture/experiments/wayne069_audio_only_20261014/configs/streamtalk_train_excluding_wayne069.csv"
CONDA = _conda()
def clips():
    out = []
    for l in open(H / "runs/4aa_pointing/wav/manifest.jsonl"):
        d = json.loads(l); out.append((d["case_id"], Path(d["wav_path"]), H / "runs/4aa_pointing/motion", d["emote"], "4aa"))
    for rk in "vip regular guest staff external".split():
        for l in open(V / rk / "tts_manifest.jsonl"):
            d = json.loads(l)
            if d["emote"] == "NONE" or d["emote"].startswith("POINT"):
                out.append((d["turn_id"], Path(d["wav_path"]), V / rk / "sonic" / d["turn_id"], d["emote"], rk))
    return out
def smi(): return int(subprocess.check_output(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"]).split()[0])
def stage(clip, name, cmd, audio_s, wd, env=None):
    """Run cmd under /usr/bin/time -v with a GPU sampler; return resource record."""
    wd.mkdir(parents=True, exist_ok=True); tf = wd / f"time_{clip}_{name}.txt"; sf_ = wd / f"gpu_{clip}_{name}.csv"; base = smi()
    samp = subprocess.Popen(["nvidia-smi", "--query-gpu=timestamp,memory.used,utilization.gpu", "--format=csv", "-lms", "500"], stdout=open(sf_, "w"))
    t0 = time.monotonic()
    with (wd / f"log_{clip}_{name}.txt").open("w") as fh:
        rc = subprocess.run(["/usr/bin/time", "-v", "-o", str(tf)] + cmd, stdout=fh, stderr=subprocess.STDOUT, env={**os.environ, **(env or {})}, cwd=ROOT).returncode
    wall = time.monotonic() - t0; samp.terminate(); samp.wait()
    tx = tf.read_text(); g = lambda k: re.search(k + r".*?: (.+)", tx).group(1)
    rss = int(g("Maximum resident set size")) / 1024; cpu = float(g("User time")) + float(g("System time"))
    rows = list(csv.reader(open(sf_)))[1:]; mem = [int(r[1].strip().split()[0]) for r in rows if len(r) > 2]; ut = [int(r[2].strip().split()[0]) for r in rows if len(r) > 2]
    rec = dict(clip=clip, stage=name, rc=rc, wall_s=round(wall, 1), audio_s=round(audio_s, 3), rtf=round(wall / audio_s, 2), cpu_s=round(cpu, 1), peak_rss_mb=round(rss),
               vram_base_mb=base, peak_vram_mb=max(mem) - base if mem else None, peak_vram_abs_mb=max(mem) if mem else None,
               gpu_util_mean=round(float(np.mean(ut)), 1) if ut else None, gpu_util_peak=max(ut) if ut else None)
    print(json.dumps(rec), flush=True); return rec
def run_clip(name, wav, M, emote, grp, profile):
    M.mkdir(parents=True, exist_ok=True); (M / "wav16").mkdir(exist_ok=True); (M / "smplx").mkdir(exist_ok=True); (M / "x2/inputs").mkdir(parents=True, exist_ok=True)
    x, sr = sf.read(wav, dtype="float32"); assert sr == 44100; dur = len(x) / sr
    w16 = M / f"wav16/{name}.wav"; sf.write(w16, resample_poly(x, 160, 441).astype(np.float32), 16000, subtype="PCM_16")
    py = lambda env: [CONDA, "run", "--no-capture-output", "-n", env, "python"]; wd = M / "prof"; rs = []
    def go(sn, cmd, env=None, check=None):
        if (M / "prof" / f"done_{name}_{sn}").exists(): print("skip done", name, sn); return True
        r = stage(name, sn, cmd, dur, wd, env); rs.append(r)
        ok = r["rc"] == 0 and (check is None or check())
        if ok: (wd / f"done_{name}_{sn}").write_text("ok")
        return ok
    out = M / f"smplx/{name}.npz"
    cmd = [str(Path(sys.executable)), str(SCRIPT), str(w16), str(CKPT), "--example_motion_npz", str(READY), "--pid", "1_wayne_0", "--seed", os.environ.get("SONIC_SEED", "1"), "--output_file", str(out),
           "--wavlm_model", str(WAVLM), "--smplx_model", str(SMPLX), "--retrieval_data_folder", str(DATA), "--retrieval_csv_file", str(CSV), "--cfg_scale", "3",
           "--retrieval_mode", "native", "--cache-dir", str(CACHE), "--csv", str(CSV)]
    ok = go("S1_streamtalk", cmd, check=out.is_file)
    if ok:
        z = np.load(out, allow_pickle=False); pose, trans = np.asarray(z["poses"], np.float32), np.asarray(z["trans"], np.float32)
        betas = np.zeros(16, np.float32); betas[:10] = np.asarray(z["betas"], np.float32).reshape(-1)[:10]
        np.savez_compressed(M / f"x2/inputs/{name}_gmr_stageii.npz", gender=np.asarray("neutral"), root_orient=pose[:, :3].copy(), pose_body=pose[:, 3:66].copy(), betas=betas, trans=trans.copy(),
                            mocap_frame_rate=np.asarray(30.0, np.float32), source_model=np.asarray(name), source_frame_count=np.asarray(len(pose), np.int32),
                            common_frame_count=np.asarray(len(pose), np.int32), adapter_schema=np.asarray("RCP01C_SMPLX_TO_X2_GMR_STAGEII_V1"))
    status = {"clip": name, "group": grp, "emote": emote, "audio_s": round(dur, 3), "dir": str(M)}
    if ok: ok = go("S2_gmr", py("gmr") + [str(C3 / "x2_retarget.py"), "--model", name, "--input-dir", str(M / "x2/inputs"), "--output-dir", str(M / "x2/retarget"), "--force"])
    if ok: ok = go("S3_convert", py("physicalai") + [str(C3 / "x2_sonic.py"), "convert", "--model", name, "--retarget-dir", str(M / "x2/retarget"), "--sonic-out-dir", str(M / "sonic"), "--log-dir", str(M / "logs"), "--force"])
    tr = M / f"traces/full_{name}.csv"
    if ok: ok = go("S4_rollout", py("physicalai") + [str(C3 / "x2_sonic.py"), "rollout", "--model", name, "--retarget-dir", str(M / "x2/retarget"), "--sonic-out-dir", str(M / "sonic"), "--log-dir", str(M / "logs"), "--trace-dir", str(M / "traces"), "--no-record", "--force"],
                       env={"X2_TRACE_DIR": str(M / "traces"), "X2_TRACE_TAG": name}, check=tr.is_file)
    arms = M / f"{name}_arms.npz"
    if ok: ok = go("S5_prep_arms", py("laplace") + [str(C7 / "scripts/prep_arms.py"), str(tr), str(arms)], check=arms.is_file)
    status["ok"] = ok; (M / "status.json").write_text(json.dumps(status, indent=1)); return rs, status
if __name__ == "__main__":
    only = sys.argv[2].split(",") if len(sys.argv) > 2 and sys.argv[1] == "--only" else None
    allc = clips(); seen = {}; prof = []; sts = []; B.mkdir(parents=True, exist_ok=True)
    pj = B / "resource_profile.jsonl"
    for name, wav, M, emote, grp in allc:
        if only and name not in only: continue
        sha = hashlib.sha256(Path(wav).read_bytes()).hexdigest()
        if sha in seen:   # identical WAV already processed: reuse
            src = seen[sha]; st = json.loads((src[1] / "status.json").read_text()) if (src[1] / "status.json").exists() else {}
            sts.append({"clip": name, "group": grp, "emote": emote, "dir": str(M), "reused_from": src[0], "ok": st.get("ok"), "audio_s": st.get("audio_s")}); continue
        seen[sha] = (name, M)
        rs, st = run_clip(name, Path(wav), M, emote, grp, prof)
        with pj.open("a") as f:
            for r in rs: f.write(json.dumps(r) + "\n")
        sts.append(st); (B / "status_all.json").write_text(json.dumps(sts, indent=1))
        if not st["ok"]: print("FAILED", name, flush=True)
