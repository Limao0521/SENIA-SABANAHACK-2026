"""Torch reference tensors for component checks (physicalai, GPU). Writes scratch/ref_components.npz"""
import numpy as np, torch, torch.nn.functional as F, librosa, json
from common_paths import *
add_st_paths()
import Net
from Common.SMPLXConfig import GetBodyPartIndices
from transformers import WavLMModel, Wav2Vec2Processor
clips = json.load(open(CLIPS_JSON)); wav = clips["c001"]
y = librosa.load(wav, sr=16000)[0]; print("c001 samples", len(y))
proc = Wav2Vec2Processor.from_pretrained(str(WAVLM))
outs = {}
wins = [y[0:32000], y[27734:27734 + 32000]]
iv = [proc(w, return_tensors="pt", sampling_rate=16000).input_values for w in wins]
outs["audio_norm"] = np.concatenate([i.numpy() for i in iv])
# numpy normalisation check
w = wins[0].astype(np.float32); outs["np_norm_maxdiff"] = np.abs((w - w.mean()) / np.sqrt(w.var() + 1e-7) - iv[0].numpy()[0]).max()
m = WavLMModel.from_pretrained(str(WAVLM)).eval().requires_grad_(False).cuda()
with torch.no_grad(): outs["wavlm_fp32"] = np.concatenate([m(i.cuda()).last_hidden_state.float().cpu().numpy() for i in iv])
m.half()
with torch.no_grad(), torch.autocast("cuda", dtype=torch.float16): outs["wavlm_fp16"] = np.concatenate([m(i.cuda()).last_hidden_state.float().cpu().numpy() for i in iv])
del m; torch.cuda.empty_cache()
def to60(e): return F.interpolate(torch.from_numpy(e).permute(0, 2, 1), 60, mode="linear", align_corners=True).permute(0, 2, 1).numpy()
h = to60(outs["wavlm_fp16"][:1]); outs["h60"] = h
simple = Net.SimpleSpeechModel().cuda().eval().requires_grad_(False); simple.load_state_dict(torch.load(FACE_CKPT, map_location="cpu"))
ht = torch.from_numpy(h).cuda(); pid = torch.LongTensor([1]).cuda()
with torch.no_grad(): outs["expr"] = simple(ht, pid).cpu().numpy(); outs["expr_u"] = simple(ht, torch.zeros_like(pid)).cpu().numpy()
with torch.no_grad(), torch.autocast("cuda", dtype=torch.float16): outs["expr_ac"] = simple(ht, pid).float().cpu().numpy()
net = Net.DiffusionDITNetPartsFixedExpressions2PostNormInteraction2().cuda().eval().requires_grad_(False); net.load_state_dict(torch.load(CKPT, map_location="cpu"), strict=True)
z = np.load(P.READY_PROMPT, allow_pickle=True)
from Common.SMPLX_Util import LocalToGlobalAA
from Common.Util import axis_angle_to_rotation_6d
pp = torch.from_numpy(axis_angle_to_rotation_6d(LocalToGlobalAA(z["poses"].astype(np.float32)[:60]))).float().cuda()[None]
tp = torch.from_numpy(z["trans"].astype(np.float32)[:60]).cuda()[None]
mask = torch.full((1, 60), True).cuda(); mask[:, :8] = False
g = torch.Generator().manual_seed(3); noise = torch.randn((1, 60, 333), generator=g).cuda()
kw = dict(pid=pid, poses_prompt=pp, prompt_mask=mask, trans_prompt=tp, trans_mask=mask.clone(), hand_indices=GetBodyPartIndices("hand", "rot6d"), upper_indices=GetBodyPartIndices("upper", "rot6d"),
          lower_indices=GetBodyPartIndices("lower", "rot6d"), face_joint_indices=GetBodyPartIndices("face", "rot6d"), trans_indices=np.array([330, 331, 332]))
ex = torch.from_numpy(outs["expr"]).cuda(); exu = torch.from_numpy(outs["expr_u"]).cuda()
outs.update(step_noise=noise.cpu().numpy(), step_pp=pp.cpu().numpy(), step_tp=tp.cpu().numpy(), step_mask=mask.cpu().numpy())
with torch.no_grad():
    for t in (0.0, 0.5, 0.9):
        ts = torch.tensor([t]).cuda()
        outs[f"step32_t{t}"] = net.forward_with_cfg(noise, ts, cfg_scale=3.0, hubert=ht.float(), expressions=ex, expressions_uncond=exu, **kw).float().cpu().numpy()
    net.half()
    for t in (0.0, 0.5, 0.9):
        ts = torch.tensor([t]).cuda()
        with torch.autocast("cuda", dtype=torch.float16):
            outs[f"step16_t{t}"] = net.forward_with_cfg(noise, ts, cfg_scale=3.0, hubert=ht, expressions=ex, expressions_uncond=exu, **kw).float().cpu().numpy()
np.savez(HERE / "scratch/ref_components.npz", **outs); print({k: v.shape if hasattr(v, "shape") else v for k, v in outs.items()})
