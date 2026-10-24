#!/usr/bin/env python3
"""Persistent StreamTalk worker (env physicalai). Loads StreamTalk + WavLM + SimpleSpeechModel + SMPL-X FK + retrieval DB + READY prompt once.
Request {"id","wav","out","seed"} -> SMPL-X npz (same keys as the cold streamtalk_runner). Generation loop is the c02 runner loop, copied unchanged
except that per-request state (pose/trans prompt, seed) is rebuilt. For seed == load seed (default 1) the CPU RNG state captured right after the
cold-order model construction is restored, so the output is bit-comparable with a cold run (cold consumes CPU RNG while building the models)."""
import importlib.util, os, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from common import serve
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # HRIEGO_gesture root -> paths.py
import paths as P
ROOT = P.streamtalk_root()  # $STREAMTALK_ROOT (external StreamTalk code + checkpoint)
MOTION = P.MOTION_DIR  # vendored streamtalk_runner.py / streamtalk_cached_entry.py
CKPT = ROOT / "checkpoints/pretrained/streamtalk_speaker_all_e0940_cfg3.pt"
READY = P.READY_PROMPT; WAVLM = str(ROOT / "assets/wavlm-libri-clean-100h-large")
SMPLX = str(P.smplx_neutral_npz())  # $SEMANTIC_CORPOREA_ROOT / $SMPLX_NEUTRAL_NPZ
DATA = str(P.beat2_smplx_dir())  # $BEAT2_SMPLX_DIR
CSV = str(P.streamtalk_train_csv())  # $STREAMTALK_TRAIN_CSV
LOAD_SEED = int(os.environ.get("ST_LOAD_SEED", "1")); PID = "1_wayne_0"; CFG = 3.0
S = {}

def load():
    os.chdir(ROOT)
    # 1. let the cached entry install its verified FindNearest.MakeKDTree patch, but skip running the upstream script
    spec = importlib.util.spec_from_file_location("st_entry", MOTION / "streamtalk_cached_entry.py"); ce = importlib.util.module_from_spec(spec); spec.loader.exec_module(ce)
    ce.runpy.run_path = lambda *a, **k: None
    sys.argv = ["x", "--cache-dir", str(P.streamtalk_retrieval_cache()), "--csv", CSV, "--pid", PID]; ce.main()
    spec = importlib.util.spec_from_file_location("st_runner", MOTION / "streamtalk_runner.py"); R = importlib.util.module_from_spec(spec); spec.loader.exec_module(R)
    import torch, numpy as np, librosa
    from Common.SMPLXConfig import GetBodyPartIndices
    from Common.SMPLX_Util import LocalToGlobalAA
    from Common.Util import axis_angle_to_rotation_6d
    from FindNearest import MakeKDTree
    S.update(R=R, torch=torch, np=np, librosa=librosa)
    torch.set_num_threads(2)
    try: torch.set_num_interop_threads(2)
    except RuntimeError: pass
    R.setup_seed(LOAD_SEED)
    S["idx"] = {k: GetBodyPartIndices(k, "rot6d") for k in ("hand", "upper", "lower", "face")}
    S["simple"] = R.SimpleSpeechModel().cuda().requires_grad_(False).eval(); S["simple"].load_state_dict(torch.load(R.BUNDLED_FACE_CHECKPOINT, map_location="cpu"))
    S["net"] = R.MotionModel().cuda().requires_grad_(False).eval(); S["net"].load_state_dict(torch.load(str(CKPT), map_location="cpu"), strict=True)
    S["smplx"] = R.EvaluateSMPLX(SMPLX)
    kd = MakeKDTree(pid=PID, data_folder=DATA, csv_file=CSV, smplx_model_path=SMPLX)
    kd["poses"] = axis_angle_to_rotation_6d(torch.from_numpy(LocalToGlobalAA(kd["poses"])).float()).cuda()
    kd["joints"] = torch.from_numpy(kd["joints"]).float().cuda().half(); S["kd"] = kd
    S["pid"] = torch.LongTensor([1]).cuda()
    z = np.load(READY, allow_pickle=True); S["betas"] = z["betas"].astype(np.float32); S["poses0"] = z["poses"].astype(np.float32)[:60]; S["trans0"] = z["trans"].astype(np.float32)[:60]
    S["afe"] = R.AudioFeatureExtractor(WAVLM).requires_grad_(False).cuda().eval()
    S["rng_after_load"] = torch.get_rng_state()
    # warm-up on a short synthetic clip so cudnn/kernels are initialised (does not touch the saved RNG state)
    gen(np.zeros(16000 * 2, np.float32), 0, warm=True)
    return {"load_seed": LOAD_SEED}

def gen(audio, seed, warm=False):
    R, torch, np = S["R"], S["torch"], S["np"]; net, simple, afe = S["net"], S["simple"], S["afe"]; idx = S["idx"]
    from Common.Util import NormalizeRot6d, rotation_6d_to_matrix, axis_angle_to_rotation_6d, matrix_to_rotation_6d, rotation_matrix_to_axis_angle
    from Common.SMPLX_Util import global_to_local_batch, LocalToGlobalAA
    saved = torch.get_rng_state()
    if seed == LOAD_SEED and not warm: R.setup_seed(seed); torch.set_rng_state(S["rng_after_load"])
    else: R.setup_seed(seed)
    B, n_seed, has_trans = 1, 8, True; pid = S["pid"]; kd = S["kd"]; betas = S["betas"]
    pose_prompt = torch.from_numpy(axis_angle_to_rotation_6d(LocalToGlobalAA(S["poses0"].copy()))).cuda().unsqueeze(0).float()
    trans_prompt = torch.from_numpy(S["trans0"].copy()).cuda().unsqueeze(0).float()
    trans_indices = np.array([330, 331, 332]); tm = {}
    t0 = time.time(); huberts = afe.extract(audio); torch.cuda.synchronize(); tm["wavlm_s"] = time.time() - t0
    nseg = len(huberts); motions = []; t1 = time.time()
    for n in range(nseg):
        prompt_mask = torch.full((B, 60), True).cuda(); prompt_mask[:, :n_seed] = False
        pe = simple(huberts[n], pid); peu = simple(huberts[n], torch.zeros_like(pid))
        trans_mask = torch.full((B, 60), True).cuda(); trans_mask[:, :n_seed] = False
        def kw(pm, pe=pe, peu=peu, h=huberts[n]):
            return dict(pid=pid, poses_prompt=pose_prompt, prompt_mask=pm, trans_prompt=trans_prompt, trans_mask=trans_mask, hand_indices=idx["hand"], upper_indices=idx["upper"],
                        lower_indices=idx["lower"], face_joint_indices=idx["face"], trans_indices=trans_indices, hubert=h, expressions=pe, expressions_uncond=peu)
        noise = torch.randn((B, 60, 55 * 6 + 3)).cuda()
        with torch.no_grad(), torch.autocast(device_type="cuda", dtype=torch.float16):
            xt_list = net.sample(noise, normalize_func=NormalizeRot6d, cfg_scale=CFG, **kw(prompt_mask)); xt = xt_list[-1]
        xt[..., :330] = NormalizeRot6d(xt[..., :330])
        lp = rotation_matrix_to_axis_angle(global_to_local_batch(rotation_6d_to_matrix(xt[..., :330].reshape(-1, 55, 6)))).reshape(B, -1, 165)
        joints = S["smplx"](betas=torch.from_numpy(betas).float().cuda(), poses=lp.reshape(-1, 165).cuda()).reshape((60, 1, -1))
        db = kd["joints"]; q = joints[-8:].reshape(8, -1).half()
        best_d = torch.full((8,), float("inf"), dtype=q.dtype, device=q.device); best_i = torch.zeros((8,), dtype=torch.long, device=q.device)
        for s in range(0, db.shape[0], 1024):
            e = min(s + 1024, db.shape[0]); cb, ci = torch.abs(db[None, s:e, :] - q[:, None, :]).sum(-1).min(-1)
            r = cb < best_d; best_d[r] = cb[r]; best_i[r] = ci[r] + s
        ni = int(torch.argmin(best_d).item())
        npz = rotation_6d_to_matrix(kd["poses"][best_i[ni]].reshape(1, 55, 6))
        go = rotation_6d_to_matrix(xt[0, 52 + ni, :6].reshape(1, 6))
        npz = matrix_to_rotation_6d(torch.einsum("bij,bnjk->bnik", go, npz)).reshape(1, 330)
        prompt_mask = torch.full((B, 60), True).cuda(); prompt_mask[:, :n_seed] = False; prompt_mask[:, 52 + ni] = False
        pose_prompt[:, 52 + ni] = npz; xt[:, 52 + ni, :330] = npz
        with torch.no_grad(), torch.autocast(device_type="cuda", dtype=torch.float16, enabled=True):
            xt = net.sample_with_kp_bk(noise, xt_list[5], 5, normalize_func=NormalizeRot6d, cfg_scale=CFG, **kw(prompt_mask))
        xt[..., :330] = NormalizeRot6d(xt[..., :330]); pose_prompt[:, :n_seed] = xt[:, -n_seed:][..., :330]; trans_prompt[:, :n_seed] = xt[:, -n_seed:][..., 330:333]
        motions.append(torch.cat([xt, pe], dim=-1))
    for i in range(1, len(motions)):
        motions[i - 1][:, -n_seed:] = (motions[i - 1][:, -n_seed:] + motions[i][:, :n_seed]) / 2.0
        motions[i] = motions[i][:, n_seed:n_seed + afe.output_frame_counts[i]]
    if len(motions) == 1: motions[0] = motions[0][:, :afe.output_frame_counts[0]]
    sr = torch.cat(motions, dim=1); expr = sr[..., -100:]
    pp = rotation_matrix_to_axis_angle(global_to_local_batch(rotation_6d_to_matrix(sr[..., :330].reshape(-1, 55, 6)))).reshape(B, -1, 165)
    torch.cuda.synchronize(); tm["diffusion_s"] = time.time() - t1; tm["segments"] = nseg
    res = dict(betas=betas, poses=pp[0].cpu().numpy(), expressions=expr[0].cpu().numpy(), trans=sr[..., 330:333][0].cpu().numpy(), gender=np.array("neutral"), mocap_frame_rate=np.array(30), model="smplx2020")
    return res, tm

def handle(req):
    np = S["np"]; t = time.time()
    audio = S["librosa"].load(req["wav"], sr=16000)[0]; load_s = time.time() - t
    res, tm = gen(audio, int(req.get("seed", 1)))
    Path(req["out"]).parent.mkdir(parents=True, exist_ok=True); np.savez(req["out"], **res)
    import torch
    return dict(audio_s=round(len(audio) / 16000, 3), frames=int(res["poses"].shape[0]), audio_load_s=round(load_s, 3), vram_alloc_mb=round(torch.cuda.memory_allocated() / 2**20), vram_peak_mb=round(torch.cuda.max_memory_allocated() / 2**20),
                **{k: round(v, 3) if isinstance(v, float) else v for k, v in tm.items()})

if __name__ == "__main__": serve("streamtalk", load, handle)
