import sys, os, collections; sys.path.insert(0, os.path.dirname(__file__)); from memlib import snap
import torch, torch.nn.functional as F
LOG = None
def top():
    agg = collections.Counter(); cur = None
    for l in open("/proc/self/smaps"):
        p = l.split()
        if len(p) >= 5 and "-" in p[0] and not p[0].endswith(":"): cur = p[5] if len(p) > 5 else "[anon]"
        elif p[0] == "Rss:": agg[cur] += int(p[1]) / 1024
    return [(round(v), k.split("/")[-1]) for k, v in agg.most_common(8)]
snap("a_import_torch"); torch.zeros(1).cuda(); snap("b_ctx")
x = torch.randn(512, 512, device="cuda"); y = x @ x; torch.cuda.synchronize(); snap("c_fp32_matmul(cublas)")
with torch.autocast("cuda", dtype=torch.float16):
    z = x @ x; torch.cuda.synchronize(); snap("d_fp16_matmul_autocast(cublasLt?)")
    l = torch.nn.Linear(384, 384).cuda(); o = l(torch.randn(4, 60, 384, device="cuda")); torch.cuda.synchronize(); snap("e_linear")
    c = torch.nn.Conv1d(512, 512, 3).cuda(); o = c(torch.randn(1, 512, 400, device="cuda")); torch.cuda.synchronize(); snap("f_conv1d(cudnn)")
    q = torch.randn(1, 8, 100, 64, device="cuda", dtype=torch.half); o = F.scaled_dot_product_attention(q, q, q); torch.cuda.synchronize(); snap("g_sdpa")
    o = F.layer_norm(torch.randn(10, 384, device="cuda"), (384,)); o = F.softmax(torch.randn(10, 384, device="cuda"), -1); o = F.gelu(o); snap("h_ln_softmax_gelu")
print(top(), file=sys.stderr)
