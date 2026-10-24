"""Torch-free StreamTalk generator: ONNX Runtime (CUDA EP) for WavLM / SimpleSpeechModel / CFG velocity net / L1 retrieval; numpy for the ODE loop, rotation maths,
SMPL-X FK (rest joints precomputed for the READY betas) and the poses table (mmap). Mirrors opt/m2_motion_server/st_stream.stream().
Imports: numpy + onnxruntime only (no torch / transformers / scipy / librosa). Noise: pass `noise` (n_windows,60,333) to replay torch's stream, else numpy RNG."""
import os, time
from pathlib import Path
import numpy as np
HERE = Path(__file__).resolve().parent
KD_CACHE = Path(os.environ.get("O1B_KD_CACHE", HERE.parents[0] / "m1_memory/out/kd_cache"))
SR = 16000; WIN = SR * 2; OVL = 8; STRIDE = WIN - int(SR / 30 * OVL); N_SEED = 8; CFG = 3.0
PARENTS = None

def plan_windows(n):
    starts = list(range(0, n - WIN + 1, STRIDE))
    if starts:
        clips = [(s, s + WIN) for s in starts]; counts = [60] + [60 - OVL] * (len(starts) - 1); le = starts[-1] + WIN
        if n > le:
            ps = starts[-1] + STRIDE; clips.append((ps, min(ps + WIN, n))); counts.append(min(60 - OVL, int(np.ceil(max(0, n - le) / STRIDE * (60 - OVL)))))
    else: clips = [(0, n)]; counts = [max(1, min(60, int(np.ceil(n / WIN * 60))))]
    return clips, counts

# ---------------- numpy rotation maths (ports of Common/Util.py + SMPLX_Util.py torch paths) ----------------
def _norm(x, eps=1e-12): return x / np.maximum(np.linalg.norm(x, axis=-1, keepdims=True), eps)
def normalize_rot6d(m):
    sh = m.shape; d = m.reshape(-1, 6); b1 = _norm(d[:, :3]); b2 = _norm(d[:, 3:] - (b1 * d[:, 3:]).sum(-1, keepdims=True) * b1)
    return np.concatenate([b1, b2], -1).reshape(sh)
def rot6d_to_matrix(d6):
    b1 = _norm(d6[..., :3]); b2 = _norm(d6[..., 3:] - (b1 * d6[..., 3:]).sum(-1, keepdims=True) * b1)
    return np.stack((b1, b2, np.cross(b1, b2, axis=-1)), axis=-2)
def matrix_to_rot6d(m): return m[..., :2, :].reshape(m.shape[:-2] + (6,))
def global_to_local(g):
    l = np.empty_like(g)
    for i in range(g.shape[1]):
        l[:, i] = g[:, i] if PARENTS[i] < 0 else np.swapaxes(g[:, PARENTS[i]], -1, -2) @ g[:, i]
    return l
def matrix_to_axis_angle(m, eps=1e-4):
    """Exact port of rotation_matrix_to_axis_angle_torch (acos with clamp, near-pi branch)."""
    sh = m.shape; m = m.reshape(-1, 3, 3)
    tr = m[:, 0, 0] + m[:, 1, 1] + m[:, 2, 2]; th = np.arccos(np.clip((tr - 1) / 2, -1 + eps, 1 - eps))
    axis = np.empty((len(m), 3), m.dtype); zero = th < eps; axis[zero] = (1, 0, 0)
    pi = th > np.arccos(-1 + 2 * eps)
    if pi.any():
        mp = m[pi]; ax = np.sqrt(np.clip((np.stack([mp[:, 0, 0], mp[:, 1, 1], mp[:, 2, 2]], -1) + 1) / 2, 0, None))
        sx = ((mp[:, 2, 1] - mp[:, 1, 2]) > 0) * 2 - 1; sy = ((mp[:, 0, 2] - mp[:, 2, 0]) > 0) * 2 - 1; sz = ((mp[:, 1, 0] - mp[:, 0, 1]) > 0) * 2 - 1
        axis[pi] = ax * np.stack([sx, sy, sz], -1)
    nm = ~(zero | pi)
    if nm.any():
        mn = m[nm]; axis[nm] = np.stack([mn[:, 2, 1] - mn[:, 1, 2], mn[:, 0, 2] - mn[:, 2, 0], mn[:, 1, 0] - mn[:, 0, 1]], -1) / (2 * np.sin(th[nm])[:, None])
    return (axis * th[:, None]).astype(m.dtype).reshape(sh[:-2] + (3,))
def rodrigues(aa):
    ang = np.linalg.norm(aa + 1e-8, axis=-1, keepdims=True); r = aa / ang; c = np.cos(ang)[..., None]; s = np.sin(ang)[..., None]
    rx, ry, rz = r[..., 0], r[..., 1], r[..., 2]; z = np.zeros_like(rx)
    K = np.stack([z, -rz, ry, rz, z, -rx, -ry, rx, z], -1).reshape(aa.shape[:-1] + (3, 3)); I = np.eye(3, dtype=aa.dtype)
    return I + s * K + (1 - c) * (K @ K)
def fk_joints(J_rest, parents, lp):
    """SMPL-X posed joints (no global orient, no trans): lp (T,165) local axis-angle -> (T,165). Equals smplx batch_rigid_transform joints."""
    T = len(lp); aa = lp.reshape(T, 55, 3).astype(np.float32).copy(); aa[:, 0] = 0; R = rodrigues(aa)
    G = np.empty_like(R); X = np.empty((T, 55, 3), np.float32)
    for j in range(55):
        if parents[j] < 0: G[:, j] = R[:, j]; X[:, j] = J_rest[j]
        else: p = parents[j]; G[:, j] = G[:, p] @ R[:, j]; X[:, j] = X[:, p] + (G[:, p] @ (J_rest[j] - J_rest[p]))
    return X.reshape(T, -1)

def _interp_matrix(n_in=99, n_out=60):
    src = np.arange(n_out) * (n_in - 1) / (n_out - 1); i0 = np.minimum(np.floor(src).astype(int), n_in - 2); w = (src - i0).astype(np.float32)
    M = np.zeros((n_out, n_in), np.float32); M[np.arange(n_out), i0] = 1 - w; M[np.arange(n_out), i0 + 1] = w
    return M

class OnnxST:
    def __init__(self, variant="fp16", models_dir=None, device_id=0, cudnn_search="DEFAULT", mem_limit_mb=0, arena_exact=False, wavlm_variant=None, retrieval_provider="CUDAExecutionProvider"):
        import onnxruntime as ort
        self.ort = ort
        if hasattr(ort, "preload_dlls"):
            try: ort.preload_dlls()
            except Exception: pass
        md = Path(models_dir or HERE / "models"); a = np.load(HERE / "assets/st_assets.npz")
        global PARENTS; PARENTS = a["parents"]; self.A = a
        so = ort.SessionOptions(); so.log_severity_level = 3
        cuda = {"device_id": device_id, "cudnn_conv_algo_search": cudnn_search}
        if mem_limit_mb: cuda["gpu_mem_limit"] = int(mem_limit_mb) * 2**20; cuda["arena_extend_strategy"] = "kSameAsRequested"
        if arena_exact: cuda["arena_extend_strategy"] = "kSameAsRequested"
        prov = [("CUDAExecutionProvider", cuda), "CPUExecutionProvider"]
        mk = lambda n, p=prov: ort.InferenceSession(str(md / n), so, providers=p)
        t = time.perf_counter()
        self.wavlm = mk(f"wavlm_{wavlm_variant or variant}.onnx"); self.t_wavlm_load = time.perf_counter() - t
        self.simple = mk(f"simple_{variant}.onnx"); self.step = mk(f"step_{variant}.onnx")
        self.retr = mk("retrieval.onnx", prov if retrieval_provider == "CUDAExecutionProvider" else ["CPUExecutionProvider"])
        self.active = self.step.get_providers()
        self.M = _interp_matrix(); self.poses_tab = np.load(KD_CACHE / "poses6d.npy", mmap_mode="r")
        self.pp0 = a["pose_prompt6d"].astype(np.float32); self.tp0 = a["trans0"].astype(np.float32)
        self.bidx = np.concatenate([a["upper"], a["lower"], a["face"]])
        ts = np.array([i / 10 for i in range(11)], np.float32); self.ts = ts
        kp = np.linspace(0, 1, 11, dtype=np.float64).astype(np.float32); self.ts = kp
    def warm(self):
        for _ in self.stream(np.zeros(WIN, np.float32), noise=np.zeros((1, 60, 333), np.float32), warm=True): pass

    def _embed(self, clip):
        if len(clip) < WIN: clip = np.pad(clip, (0, WIN - len(clip)))
        x = clip.astype(np.float32); x = ((x - x.mean()) / np.sqrt(x.var() + 1e-7))[None]
        e = self.wavlm.run(None, {"audio": x})[0][0]            # (99,1024)
        return (self.M @ e).astype(np.float16).astype(np.float32)[None]   # (1,60,1024), fp16-rounded like production

    def _step(self, xt, t, ctx):
        return self.step.run(None, {"x": xt.astype(np.float32), "t": np.array([t], np.float32), **ctx})[0]

    def _ode(self, x0, xt, i0, ctx, keep=False):
        lst = [xt] if keep else None
        for i in range(i0, 10):
            te = self.ts[i + 1]; p = self._step(xt, self.ts[i], ctx).copy(); p[..., :330] = normalize_rot6d(p[..., :330])
            xt = (x0 * (1 - te) + p * te).astype(np.float32)
            if keep: lst.append(xt)
        return lst if keep else xt

    def stream(self, audio, seed=1, noise=None, warm=False, avail_fn=None):
        A = self.A; rs = np.random.RandomState(seed)
        pose_prompt = self.pp0[None].copy(); trans_prompt = self.tp0[None].copy()
        clips, counts = plan_windows(len(audio)); nw = len(clips); pending = None
        for n, (a, b) in enumerate(clips):
            if avail_fn: avail_fn(b)
            tw = time.perf_counter(); h = self._embed(audio[a:b]); t_wavlm = time.perf_counter() - tw; tg = time.perf_counter()
            pe, peu = self.simple.run(None, {"hubert": h})
            mask = np.ones((1, 60), bool); mask[:, :N_SEED] = False
            x0 = (noise[n][None] if noise is not None else rs.randn(1, 60, 333)).astype(np.float32)
            ctx = dict(poses_prompt=pose_prompt, prompt_mask=mask, trans_prompt=trans_prompt, trans_mask=mask.copy(), hubert=h, expressions=pe, expressions_uncond=peu)
            xl = self._ode(x0, x0, 0, ctx, keep=True); xt = xl[-1].copy()
            xt[..., :330] = normalize_rot6d(xt[..., :330])
            lp = matrix_to_axis_angle(global_to_local(rot6d_to_matrix(xt[0, :, :330].reshape(-1, 55, 6)))).reshape(60, 165)
            q = fk_joints(A["J_rest"], A["parents"], lp)[-8:].astype(np.float16)
            bd, bi = self.retr.run(None, {"q": q})
            ni = int(np.argmin(bd)); row = np.array(self.poses_tab[int(bi[ni])]).reshape(1, 55, 6).astype(np.float32)
            go = rot6d_to_matrix(xt[0, 52 + ni, :6].reshape(1, 6)); nm = np.einsum("bij,bnjk->bnik", go, rot6d_to_matrix(row))
            npz = matrix_to_rot6d(nm).reshape(330)
            mask = np.ones((1, 60), bool); mask[:, :N_SEED] = False; mask[:, 52 + ni] = False
            pose_prompt[:, 52 + ni] = npz
            ctx.update(poses_prompt=pose_prompt, prompt_mask=mask)
            xt = self._ode(x0, xl[5], 5, ctx)
            xt[..., :330] = normalize_rot6d(xt[..., :330]); pose_prompt[:, :N_SEED] = xt[:, -N_SEED:, :330]; trans_prompt[:, :N_SEED] = xt[:, -N_SEED:, 330:333]
            m = xt[0, :, :333]; last = n == nw - 1
            if n == 0:
                if last: out = m[:counts[0]]
                else: out = m[:60 - OVL]; pending = m[60 - OVL:].copy()
            else:
                blended = (pending + m[:OVL]) / 2.0; body = m[OVL:OVL + counts[n]]
                if last: out = np.concatenate([blended, body])
                else: out = np.concatenate([blended, body[:-OVL]]); pending = body[-OVL:].copy()
            poses = matrix_to_axis_angle(global_to_local(rot6d_to_matrix(out[:, :330].reshape(-1, 55, 6)))).reshape(-1, 165).astype(np.float32)
            yield n, nw, poses, out[:, 330:333].astype(np.float32).copy(), dict(wavlm_s=t_wavlm, gen_s=time.perf_counter() - tg, frames=len(poses), audio_end_s=b / SR, ni=ni)
