#!/usr/bin/env python3
"""StreamTalk memory probe (copy of workers/st_worker.load with per-step snapshots + variants). Reuses st_worker.gen unchanged.
env: VAR=<name>  HALF=none|cast|direct  KD=full|lean  KD_VERIFY=1|0  TRIM=0|1  TM=0|1 (tracemalloc)  CLIPS=c010,c018,...  RNG=save|load
"""
import importlib.util, os, sys, time, json, gc
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); import memlib
from memlib import snap, trim
VAR = os.environ.get("VAR", "prod"); HALF = os.environ.get("HALF", "none"); KD = os.environ.get("KD", "full"); VERIFY = os.environ.get("KD_VERIFY", "1") == "1"
TRIM = os.environ.get("TRIM", "0") == "1"; TM = os.environ.get("TM", "0") == "1"; RNG = os.environ.get("RNG", "")
LOG = HERE / "logs" / f"st_{VAR}.jsonl"; LOG.unlink(missing_ok=True)
OUT = HERE / "out" / f"st_{VAR}"; OUT.mkdir(parents=True, exist_ok=True)
S0 = lambda l, **k: snap(l, LOG, extra=k or None)
if TM:
    import tracemalloc; tracemalloc.start(1)
S0("00_python_start")
sys.path.insert(0, str(HERE.parent.parent / "workers"))
import st_worker as W
import paths as P
S = W.S
t_load0 = time.time()
os.chdir(W.ROOT)
spec = importlib.util.spec_from_file_location("st_entry", W.MOTION / "streamtalk_cached_entry.py"); ce = importlib.util.module_from_spec(spec); spec.loader.exec_module(ce)
S0("01_import_torch_numpy_scipy_pandas(entry)")
ce.runpy.run_path = lambda *a, **k: None
sys.argv = ["x", "--cache-dir", str(P.streamtalk_retrieval_cache()), "--csv", W.CSV, "--pid", W.PID]; ce.main()
spec = importlib.util.spec_from_file_location("st_runner", W.MOTION / "streamtalk_runner.py"); R = importlib.util.module_from_spec(spec); spec.loader.exec_module(R)
import torch, numpy as np, librosa
S0("02_import_transformers_librosa_runner")
from Common.SMPLXConfig import GetBodyPartIndices
from Common.SMPLX_Util import LocalToGlobalAA
from Common.Util import axis_angle_to_rotation_6d
from FindNearest import MakeKDTree
S.update(R=R, torch=torch, np=np, librosa=librosa)
torch.set_num_threads(2)
try: torch.set_num_interop_threads(2)
except RuntimeError: pass
torch.zeros(1).cuda(); S0("03_cuda_context_init")
R.setup_seed(W.LOAD_SEED)
S["idx"] = {k: GetBodyPartIndices(k, "rot6d") for k in ("hand", "upper", "lower", "face")}
S["simple"] = R.SimpleSpeechModel().cuda().requires_grad_(False).eval(); S["simple"].load_state_dict(torch.load(R.BUNDLED_FACE_CHECKPOINT, map_location="cpu")); S0("04_simple_speech_model")
if HALF == "direct":
    net = R.MotionModel().requires_grad_(False).eval().half(); sd = torch.load(str(W.CKPT), map_location="cpu"); net.load_state_dict(sd, strict=True); del sd; S["net"] = net.cuda()
else:
    S["net"] = R.MotionModel().cuda().requires_grad_(False).eval(); S["net"].load_state_dict(torch.load(str(W.CKPT), map_location="cpu"), strict=True)
    if HALF == "cast": S["net"].half()
S0("05_generator_weights", params_M=round(sum(p.numel() for p in S["net"].parameters()) / 1e6, 1), dtype=str(next(S["net"].parameters()).dtype))
if TRIM: trim(); torch.cuda.empty_cache(); S0("05b_trim")
S["smplx"] = R.EvaluateSMPLX(W.SMPLX); S0("06_smplx_model")

def lean_kd():
    """cached_make_kdtree without cKDTree / CPU copies kept: load cache npz -> concat -> GPU, free CPU."""
    import pandas as pd
    cache = P.streamtalk_retrieval_cache(); man = json.loads((cache / "cache_manifest.json").read_text())
    table = pd.read_csv(W.CSV); ids = [str(x) for x in table.loc[table["type"] == "train", "id"] if str(x).startswith(W.PID)]
    rec = {r["id"]: r for r in man["files"]}; assert set(ids) == set(rec)
    jp, pp = [], []
    for i in ids:
        if VERIFY: assert ce.sha256(Path(W.DATA) / f"{i}.npz") == rec[i]["source_sha256"]
        with np.load(cache / rec[i]["cache"], allow_pickle=False) as z: jp.append(np.asarray(z["joints"], np.float32).reshape(len(z["joints"]), -1)); pp.append(np.asarray(z["poses"], np.float32))
    j = torch.from_numpy(np.concatenate(jp)); p = np.concatenate(pp); del jp, pp
    return j, p
if KD == "full":
    kd = MakeKDTree(pid=W.PID, data_folder=W.DATA, csv_file=W.CSV, smplx_model_path=W.SMPLX); S0("07a_retrieval_load_cpu(kdtree+arrays)", frames=int(len(kd["poses"])))
    kd["poses"] = axis_angle_to_rotation_6d(torch.from_numpy(LocalToGlobalAA(kd["poses"])).float()).cuda()
    kd["joints"] = torch.from_numpy(kd["joints"]).float().cuda().half(); S["kd"] = kd
else:
    j, p = lean_kd(); S0("07a_retrieval_load_cpu(lean)", frames=int(len(p)))
    def conv(pp, step=8192):   # per-frame independent -> chunked (bounded float64 temporaries)
        return torch.cat([axis_angle_to_rotation_6d(torch.from_numpy(LocalToGlobalAA(pp[i:i + step])).float()) for i in range(0, len(pp), step)])
    if KD == "mmap":
        kc = HERE / "out" / "kd_cache"; kc.mkdir(exist_ok=True)
        if not (kc / "poses6d.npy").exists():
            np.save(kc / "poses6d.npy", conv(p).numpy()); np.save(kc / "joints_f16.npy", j.float().half().numpy())
        del j, p; trim()
        class MM:   # row fetch from the memory-mapped 6D pose table (page cache, evictable)
            def __init__(s): s.a = np.load(kc / "poses6d.npy", mmap_mode="r")
            def __getitem__(s, i): return torch.from_numpy(np.array(s.a[int(i)])).cuda()
        S["kd"] = {"poses": MM(), "joints": torch.from_numpy(np.load(kc / "joints_f16.npy")).cuda()}
    else:
        S["kd"] = {"poses": conv(p).cuda(), "joints": j.float().cuda().half()}; del j, p
S0("07b_retrieval_on_gpu")
if TRIM or KD == "lean": trim(); S0("07c_trim_after_retrieval")
S["pid"] = torch.LongTensor([1]).cuda()
z = np.load(W.READY, allow_pickle=True); S["betas"] = z["betas"].astype(np.float32); S["poses0"] = z["poses"].astype(np.float32)[:60]; S["trans0"] = z["trans"].astype(np.float32)[:60]; S0("08_ready_prompt")
if HALF != "none":
    import functools; _f = R.WavLMModel.from_pretrained; R.WavLMModel.from_pretrained = classmethod(lambda cls, *a, **k: _f(*a, torch_dtype=torch.float16, **k)) if HALF == "direct" else _f
S["afe"] = R.AudioFeatureExtractor(W.WAVLM).requires_grad_(False).cuda().eval()
if HALF == "cast": S["afe"].half()
S0("09_wavlm", dtype=str(next(S["afe"].model.parameters()).dtype))
S["rng_after_load"] = torch.get_rng_state()
if RNG == "save": torch.save(S["rng_after_load"], HERE / "logs" / "rng_prod.pt")
elif RNG == "load": S["rng_after_load"] = torch.load(HERE / "logs" / "rng_prod.pt")
W.gen(np.zeros(16000 * 2, np.float32), 0, warm=True); S0("10_warmup_gen")
if TRIM: trim(); torch.cuda.empty_cache(); S0("10b_trim_empty_cache")
load_s = time.time() - t_load0
clips = json.load(open(HERE.parent / "t2_streamtalk_precision/clips.json"))
res = {"var": VAR, "load_s": round(load_s, 2), "clips": {}}
for name in os.environ.get("CLIPS", "c010,c018,v_guest_t01_guidance_POINT").split(","):
    audio = librosa.load(clips[name], sr=16000)[0]; torch.cuda.synchronize(); t = time.time()
    r, tm = W.gen(audio, 1); wall = time.time() - t
    np.savez(OUT / f"{name}.npz", **r); res["clips"][name] = dict(audio_s=round(len(audio) / 16000, 2), gen_s=round(wall, 3), **{k: round(v, 3) for k, v in tm.items() if isinstance(v, float)})
S0("11_after_clips", peak_alloc_mb=round(torch.cuda.max_memory_allocated() / 2**20))
res["final"] = snap("final", None); res["hwm_mb"] = round(memlib.smaps()["Rss"]); 
res["vmhwm_mb"] = round(int([l for l in open("/proc/self/status") if l.startswith("VmHWM")][0].split()[1]) / 1024)
json.dump(res, open(HERE / "logs" / f"st_{VAR}_run.json", "w"), indent=1)
if TM:
    snapt = tracemalloc.take_snapshot(); print("TRACEMALLOC cur/peak MB", [x / 2**20 for x in tracemalloc.get_traced_memory()], file=sys.stderr)
    with open(HERE / "logs" / f"st_{VAR}_tracemalloc.txt", "w") as f:
        f.write(f"current/peak MB {[round(x/2**20,1) for x in tracemalloc.get_traced_memory()]}\n\nby lineno:\n")
        for s in snapt.statistics("lineno")[:25]: f.write(f"{s.size/2**20:9.1f} MB {s.count:8d} {s.traceback[0]}\n")
        f.write("\nby file:\n")
        for s in snapt.statistics("filename")[:15]: f.write(f"{s.size/2**20:9.1f} MB {s}\n")
print("DONE", VAR)
