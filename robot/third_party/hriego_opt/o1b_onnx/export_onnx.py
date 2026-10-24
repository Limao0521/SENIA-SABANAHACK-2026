"""Export StreamTalk neural parts to ONNX (fp32). Run in physicalai env (CPU export). python export_onnx.py [wavlm|step|simple|all]"""
import sys, time, numpy as np, torch
from common_paths import *
add_st_paths()
import Net
from Common.SMPLXConfig import GetBodyPartIndices
what = sys.argv[1] if len(sys.argv) > 1 else "all"
OPSET = 18

class Step(torch.nn.Module):
    """One velocity-network evaluation with classifier-free guidance (pair batch) for B=1, pid=1. Output = unnormalised pred_x1 (1,60,333)."""
    def __init__(self, net):
        super().__init__(); self.net = net
        self.hi, self.ui, self.li, self.fi = [GetBodyPartIndices(k, "rot6d") for k in ("hand", "upper", "lower", "face")]
        self.ti = np.array([330, 331, 332]); self.register_buffer("pid", torch.LongTensor([1]))
        self.cfg = 3.0
    def forward(self, x, t, poses_prompt, prompt_mask, trans_prompt, trans_mask, hubert, expressions, expressions_uncond):
        net = self.net
        x2 = torch.cat([x, x], 0); t2 = torch.cat([t, t], 0)
        kw = dict(hand_indices=self.hi, upper_indices=self.ui, lower_indices=self.li, face_joint_indices=self.fi, trans_indices=self.ti,
                  pid=torch.cat([self.pid, self.pid]), poses_prompt=torch.cat([poses_prompt, poses_prompt]), prompt_mask=torch.cat([prompt_mask, prompt_mask]),
                  trans_prompt=torch.cat([trans_prompt, trans_prompt]), trans_mask=torch.cat([trans_mask, trans_mask]),
                  hubert=torch.cat([torch.zeros_like(hubert), hubert]), expressions=torch.cat([expressions_uncond, expressions]))
        p = net.forward(x2, t2, **kw); u, c = p.chunk(2, 0)
        return u + self.cfg * (c - u)

class Simple(torch.nn.Module):
    def __init__(self, m):
        super().__init__(); self.m = m; self.register_buffer("p1", torch.LongTensor([1])); self.register_buffer("p0", torch.LongTensor([0]))
    def forward(self, h): return self.m(h, self.p1), self.m(h, self.p0)

def sample_inputs():
    g = torch.Generator().manual_seed(0)
    return (torch.randn(1, 60, 333, generator=g), torch.tensor([0.3]), torch.randn(1, 60, 330, generator=g) * .3, torch.cat([torch.zeros(1, 8, dtype=torch.bool), torch.ones(1, 52, dtype=torch.bool)], 1),
            torch.randn(1, 60, 3, generator=g), torch.cat([torch.zeros(1, 8, dtype=torch.bool), torch.ones(1, 52, dtype=torch.bool)], 1),
            torch.randn(1, 60, 1024, generator=g), torch.randn(1, 60, 100, generator=g) * .1, torch.randn(1, 60, 100, generator=g) * .1)

t0 = time.time()
if what in ("step", "all"):
    net = Net.DiffusionDITNetPartsFixedExpressions2PostNormInteraction2().eval().requires_grad_(False); net.load_state_dict(torch.load(CKPT, map_location="cpu"), strict=True)
    m = Step(net).eval(); ins = sample_inputs()
    with torch.no_grad():
        ref = net.forward_with_cfg(ins[0], ins[1], cfg_scale=3.0, pid=torch.LongTensor([1]), poses_prompt=ins[2], prompt_mask=ins[3], trans_prompt=ins[4], trans_mask=ins[5], hubert=ins[6], expressions=ins[7], expressions_uncond=ins[8],
              hand_indices=m.hi, upper_indices=m.ui, lower_indices=m.li, face_joint_indices=m.fi, trans_indices=m.ti)
        mine = m(*ins); print("wrapper vs forward_with_cfg maxdiff", float((ref - mine).abs().max()))
        names = ["x", "t", "poses_prompt", "prompt_mask", "trans_prompt", "trans_mask", "hubert", "expressions", "expressions_uncond"]
        torch.onnx.export(m, ins, str(MODELS / "step_fp32.onnx"), input_names=names, output_names=["pred"], opset_version=OPSET, dynamo=True)
    np.save(MODELS / "step_sample_ref.npy", mine.numpy()); print("step done", time.time() - t0)
if what in ("simple", "all"):
    s = Net.SimpleSpeechModel().eval().requires_grad_(False); s.load_state_dict(torch.load(FACE_CKPT, map_location="cpu"))
    m = Simple(s).eval(); h = torch.randn(1, 60, 1024)
    with torch.no_grad(): torch.onnx.export(m, (h,), str(MODELS / "simple_fp32.onnx"), input_names=["hubert"], output_names=["expr", "expr_uncond"], opset_version=OPSET, dynamo=True)
    print("simple done", time.time() - t0)
if what in ("wavlm", "all"):
    from transformers import WavLMModel
    w = WavLMModel.from_pretrained(str(WAVLM)).eval().requires_grad_(False)
    class W(torch.nn.Module):
        def __init__(s, m): super().__init__(); s.m = m
        def forward(s, x): return s.m(x).last_hidden_state
    x = torch.randn(1, 32000)
    with torch.no_grad():
        torch.onnx.export(W(w).eval(), (x,), str(MODELS / "wavlm_fp32.onnx"), input_names=["audio"], output_names=["feat"], opset_version=OPSET, dynamic_shapes={"x": {1: torch.export.Dim("n", min=1600)}}, dynamo=True)
    print("wavlm done", time.time() - t0)
