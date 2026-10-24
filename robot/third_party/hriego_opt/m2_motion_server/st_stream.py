"""Lean StreamTalk (fp16 .half() generator + WavLM, mmap retrieval table, no cKDTree) with an INCREMENTAL window generator.

`load()` mirrors m1_memory/st_probe.py (HALF=cast KD=mmap TRIM=1 RNG=load). `stream(audio)` is workers/st_worker.gen turned into a
generator: WavLM is run per ~2 s window on demand and the frames of window n are emitted as soon as window n is generated
(the last 8 frames of a window are held back and blended with the first 8 of the next, exactly as the batch code does).
Concatenating the emitted blocks reproduces gen() output (same RNG consumption order)."""
import importlib.util, os, sys, time, json
import numpy as np
from m2common import *   # noqa
sys.path.insert(0, str(HRIEGO / "workers"))
import st_worker as W    # noqa: E402  (paths/consts + S dict only; its load()/gen() are not used)

S = W.S
SR = 16000; WIN = SR * 2; OVL = 8; STRIDE = WIN - int(SR / 30 * OVL)   # 27734 samples = 1.7334 s
N_SEED = 8


def load():
    import torch
    t0 = time.perf_counter()
    os.chdir(W.ROOT)
    spec = importlib.util.spec_from_file_location("st_entry", W.MOTION / "streamtalk_cached_entry.py"); ce = importlib.util.module_from_spec(spec); spec.loader.exec_module(ce)
    ce.runpy.run_path = lambda *a, **k: None
    sys.argv = ["x", "--cache-dir", str(P.streamtalk_retrieval_cache()), "--csv", W.CSV, "--pid", W.PID]; ce.main()
    spec = importlib.util.spec_from_file_location("st_runner", W.MOTION / "streamtalk_runner.py"); R = importlib.util.module_from_spec(spec); spec.loader.exec_module(R)
    import librosa
    from Common.SMPLXConfig import GetBodyPartIndices
    S.update(R=R, torch=torch, np=np, librosa=librosa)
    torch.set_num_threads(int(os.environ.get("ST_TORCH_THREADS", "2")))
    try: torch.set_num_interop_threads(2)
    except RuntimeError: pass
    torch.zeros(1).cuda()
    R.setup_seed(W.LOAD_SEED)
    S["idx"] = {k: GetBodyPartIndices(k, "rot6d") for k in ("hand", "upper", "lower", "face")}
    S["simple"] = R.SimpleSpeechModel().cuda().requires_grad_(False).eval(); S["simple"].load_state_dict(torch.load(R.BUNDLED_FACE_CHECKPOINT, map_location="cpu"))
    S["net"] = R.MotionModel().cuda().requires_grad_(False).eval(); S["net"].load_state_dict(torch.load(str(W.CKPT), map_location="cpu"), strict=True); S["net"].half()
    trim(); torch.cuda.empty_cache()
    S["smplx"] = R.EvaluateSMPLX(W.SMPLX)

    class MM:   # row fetch from the memory-mapped 6D pose table (page cache, evictable)
        def __init__(s): s.a = np.load(KD_CACHE / "poses6d.npy", mmap_mode="r")
        def __getitem__(s, i): return torch.from_numpy(np.array(s.a[int(i)])).cuda()
    S["kd"] = {"poses": MM(), "joints": torch.from_numpy(np.load(KD_CACHE / "joints_f16.npy")).cuda()}
    S["pid"] = torch.LongTensor([1]).cuda()
    z = np.load(W.READY, allow_pickle=True); S["betas"] = z["betas"].astype(np.float32); S["poses0"] = z["poses"].astype(np.float32)[:60]; S["trans0"] = z["trans"].astype(np.float32)[:60]
    S["afe"] = R.AudioFeatureExtractor(W.WAVLM).requires_grad_(False).cuda().eval(); S["afe"].half()
    S["rng_after_load"] = torch.load(RNG_PT)
    for _ in stream(np.zeros(SR * 2, np.float32), 0, warm=True): pass   # warm-up (cudnn/kernels), does not touch the saved RNG state
    trim(); torch.cuda.empty_cache()
    return time.perf_counter() - t0


def plan_windows(n):
    """Window (start, end) sample ranges and the number of frames each contributes (== AudioFeatureExtractor.extract bookkeeping)."""
    starts = list(range(0, n - WIN + 1, STRIDE))
    if starts:
        clips = [(s, s + WIN) for s in starts]; counts = [60] + [60 - OVL] * (len(starts) - 1)
        last_end = starts[-1] + WIN
        if n > last_end:
            ps = starts[-1] + STRIDE; clips.append((ps, min(ps + WIN, n)))
            nf = int(np.ceil(max(0, n - last_end) / STRIDE * (60 - OVL))); counts.append(min(60 - OVL, nf))
    else:
        clips = [(0, n)]; counts = [max(1, min(60, int(np.ceil(n / WIN * 60))))]
    return clips, counts


def _embed(audio, a, b):
    import torch, torch.nn.functional as F
    afe = S["afe"]; clip = audio[a:b]; rng = torch.get_rng_state()   # WavLM draws 24 CPU randoms per call (layerdrop); keep the noise stream independent of it
    if len(clip) < WIN: clip = np.pad(clip, (0, WIN - len(clip)))
    iv = afe.processor(clip, return_tensors="pt", sampling_rate=16000).input_values.cuda()
    with torch.no_grad(), torch.autocast(device_type="cuda", dtype=torch.float16):
        e = afe.model.forward(iv).last_hidden_state
        e = F.interpolate(e.permute((0, 2, 1)), 60, mode="linear", align_corners=True).permute((0, 2, 1))
    torch.set_rng_state(rng)
    return e


WAVLM_RNG_DRAWS = 24   # measured: one WavLM-large forward advances the CPU generator like torch.rand(24)


def stream(audio, seed=1, warm=False, avail_fn=None, batch_rng=True):
    """Generator of (n, n_windows, poses (m,165) f32 axis-angle, trans (m,3) f32, info). `avail_fn(samples_needed)` may block until the audio exists."""
    R, torch = S["R"], S["torch"]; net, simple = S["net"], S["simple"]; idx = S["idx"]; CFG = W.CFG
    from Common.Util import NormalizeRot6d, rotation_6d_to_matrix, axis_angle_to_rotation_6d, matrix_to_rotation_6d, rotation_matrix_to_axis_angle
    from Common.SMPLX_Util import global_to_local_batch, LocalToGlobalAA
    if seed == W.LOAD_SEED and not warm: R.setup_seed(seed); torch.set_rng_state(S["rng_after_load"])
    else: R.setup_seed(seed)
    B = 1; pid = S["pid"]; kd = S["kd"]; betas = S["betas"]
    pose_prompt = torch.from_numpy(axis_angle_to_rotation_6d(LocalToGlobalAA(S["poses0"].copy()))).cuda().unsqueeze(0).float()
    trans_prompt = torch.from_numpy(S["trans0"].copy()).cuda().unsqueeze(0).float()
    trans_indices = np.array([330, 331, 332])
    clips, counts = plan_windows(len(audio)); nw = len(clips); pending = None
    if batch_rng: torch.rand(WAVLM_RNG_DRAWS * nw)   # reproduce the CPU RNG position of the batch code (it runs WavLM on all windows first) -> bit-identical output
    for n, (a, b) in enumerate(clips):
        if avail_fn: avail_fn(b)
        tw = time.perf_counter(); h = _embed(audio, a, b); torch.cuda.synchronize(); t_wavlm = time.perf_counter() - tw; tg = time.perf_counter()
        prompt_mask = torch.full((B, 60), True).cuda(); prompt_mask[:, :N_SEED] = False
        pe = simple(h, pid); peu = simple(h, torch.zeros_like(pid))
        trans_mask = torch.full((B, 60), True).cuda(); trans_mask[:, :N_SEED] = False
        def kw(pm, pe=pe, peu=peu, h=h):
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
        prompt_mask = torch.full((B, 60), True).cuda(); prompt_mask[:, :N_SEED] = False; prompt_mask[:, 52 + ni] = False
        pose_prompt[:, 52 + ni] = npz; xt[:, 52 + ni, :330] = npz
        with torch.no_grad(), torch.autocast(device_type="cuda", dtype=torch.float16, enabled=True):
            xt = net.sample_with_kp_bk(noise, xt_list[5], 5, normalize_func=NormalizeRot6d, cfg_scale=CFG, **kw(prompt_mask))
        xt[..., :330] = NormalizeRot6d(xt[..., :330]); pose_prompt[:, :N_SEED] = xt[:, -N_SEED:][..., :330]; trans_prompt[:, :N_SEED] = xt[:, -N_SEED:][..., 330:333]
        m = xt[0, :, :333]                       # (60, 333): poses 6D + trans (expressions are not used on the X2)
        last = n == nw - 1
        if n == 0:
            if last: out = m[:counts[0]]
            else: out = m[:60 - OVL]; pending = m[60 - OVL:].clone()
        else:
            blended = (pending + m[:OVL]) / 2.0; body = m[OVL:OVL + counts[n]]
            if last: out = torch.cat([blended, body])
            else: out = torch.cat([blended, body[:-OVL]]); pending = body[-OVL:].clone()
        pp = rotation_matrix_to_axis_angle(global_to_local_batch(rotation_6d_to_matrix(out[:, :330].reshape(-1, 55, 6)))).reshape(-1, 165)
        poses = pp.float().cpu().numpy(); trans = out[:, 330:333].float().cpu().numpy(); torch.cuda.synchronize()
        yield n, nw, poses, trans, dict(wavlm_s=t_wavlm, gen_s=time.perf_counter() - tg, frames=len(poses), audio_end_s=b / SR)
