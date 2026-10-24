"""4aa S0-S2: Supertonic WAV (44.1 kHz) -> 16 kHz (scipy resample_poly 160/441) -> StreamTalk (c07 command, READY prompt, native retrieval via the
c02 verified cache) -> GMR stage-II npz. Env: physicalai. Output under runs/4aa_pointing/motion/. Reads the c07/c02 assets read-only."""
import sys as _sys, pathlib as _pl; _sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
from paths import laplace_root as _lr, semantic_corporea_root as _sc, py_physicalai as _pyp, conda_exe as _conda; _LR = _lr(); _SC = _sc()
import hashlib, json, subprocess, sys, time
from pathlib import Path
import numpy as np, soundfile as sf
from scipy.signal import resample_poly
H = Path(__file__).resolve().parent; R = H / "runs/4aa_pointing"; M = R / "motion"
C7 = (_LR / "experiments/RCP-01/runs/20261021_rcp01c07_ready_pose"); ROOT = _LR
C2 = C7.parent / "20261019_rcp01c02_fixedcalm"
SCRIPT = C2 / "scripts/streamtalk_cached_entry.py"; CACHE = C2 / "cache/streamtalk_retrieval"
CHECKPOINT = ROOT / "Gesture/third_party/StreamTalk/checkpoints/pretrained/streamtalk_speaker_all_e0940_cfg3.pt"
READY = C7 / "configs/streamtalk_ready_prompt.npz"
WAVLM = ROOT / "Gesture/third_party/StreamTalk/assets/wavlm-libri-clean-100h-large"
SMPLX = (_SC / "third_party/GMR/assets/body_models/smplx/SMPLX_NEUTRAL.npz")
DATA = (_SC / "data/raw/BEAT2/beat_english_v2.0.0/smplxflame_30")
CSV = ROOT / "Gesture/experiments/wayne069_audio_only_20261014/configs/streamtalk_train_excluding_wayne069.csv"
for d in ("wav16", "smplx", "x2/inputs", "logs"): (M / d).mkdir(parents=True, exist_ok=True)
recs = []
for D in sys.argv[1:] or ("RIGHT", "LEFT", "FRONT"):
    name = f"P_{D}"; src = R / f"wav/{name}.wav"; x, sr = sf.read(src, dtype="float32"); assert sr == 44100
    x16 = resample_poly(x, 160, 441).astype(np.float32); w16 = M / f"wav16/{name}.wav"; sf.write(w16, x16, 16000, subtype="PCM_16")
    out = M / f"smplx/{name}.npz"; t0 = time.monotonic()
    cmd = [sys.executable, str(SCRIPT), str(w16), str(CHECKPOINT), "--example_motion_npz", str(READY), "--pid", "1_wayne_0", "--seed", "1",
           "--output_file", str(out), "--wavlm_model", str(WAVLM), "--smplx_model", str(SMPLX), "--retrieval_data_folder", str(DATA),
           "--retrieval_csv_file", str(CSV), "--cfg_scale", "3", "--retrieval_mode", "native", "--cache-dir", str(CACHE), "--csv", str(CSV)]
    with (M / f"logs/streamtalk_{name}.log").open("w") as fh:
        rc = subprocess.run(cmd, cwd=ROOT, stdout=fh, stderr=subprocess.STDOUT).returncode
    assert rc == 0 and out.is_file(), f"StreamTalk failed {name}"
    z = np.load(out, allow_pickle=False); pose, trans = np.asarray(z["poses"], np.float32), np.asarray(z["trans"], np.float32)
    betas = np.zeros(16, np.float32); betas[:10] = np.asarray(z["betas"], np.float32).reshape(-1)[:10]
    assert pose.shape[1] == 165
    np.savez_compressed(M / f"x2/inputs/{name}_gmr_stageii.npz", gender=np.asarray("neutral"), root_orient=pose[:, :3].copy(), pose_body=pose[:, 3:66].copy(),
                        betas=betas, trans=trans.copy(), mocap_frame_rate=np.asarray(30.0, np.float32), source_model=np.asarray(name),
                        source_frame_count=np.asarray(len(pose), np.int32), common_frame_count=np.asarray(len(pose), np.int32),
                        adapter_schema=np.asarray("RCP01C_SMPLX_TO_X2_GMR_STAGEII_V1"))
    recs.append({"clip": name, "frames": len(pose), "wav_s": len(x) / sr, "seconds": round(time.monotonic() - t0, 1)}); print(recs[-1], flush=True)
json.dump(recs, open(M / "streamtalk_records.json", "w"), indent=1)
