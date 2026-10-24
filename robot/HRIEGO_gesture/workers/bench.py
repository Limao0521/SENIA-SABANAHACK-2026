#!/usr/bin/env python3
"""Cold vs warm benchmark of the StreamTalk -> GMR -> SONIC(convert+rollout) pipeline with persistent workers.
Run with the physicalai python:  bench.py warm | pipe | cold | compare
 warm : load 3 workers (MemAvailable guard), run all commercial clips serially (S1-S4, no prep_arms) into motion/<clip>/, GMR-QC seed retries
 pipe : 3 workers pipelined over the first N commercial clips -> workers/bench/pipelined/, burst + staggered arrivals
 cold : the current per-clip cold pipeline (own process per stage) on 2 clips -> workers/bench/cold/
 compare : numeric diff warm vs cold."""
import csv, json, os, queue, re, statistics as st, subprocess, sys, threading, time
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).parent)); from wlib import *
sys.path.insert(0, str(Path(__file__).resolve().parent.parent)); import paths as P
H = Path(__file__).parent; B = H / "bench"; B.mkdir(exist_ok=True)
EXP = P.commercial_show_dir(); AUD = EXP / "audio"; MOT = EXP / "motion"
CLIPS = sorted(p.stem for p in AUD.glob("*.wav"))
def rec(name, root, seed):
    M = root / name; return dict(M=M, wav=str(AUD / f"{name}.wav"), smplx=str(M / f"smplx/{name}.npz"), retarget=str(M / "x2/retarget"), sonic=str(M / "sonic"), logs=str(M / "logs"), traces=str(M / "traces"))
def pct(v, q): v = sorted(v); return round(v[min(len(v) - 1, int(round(q * (len(v) - 1))))], 2) if v else None
def stage_st(w, name, p, seed): return w.call(id=name, wav=p["wav"], out=p["smplx"], seed=seed)
def stage_gmr(w, name, p): return w.call(id=name, smplx=p["smplx"], name=name, out_dir=p["retarget"])
def stage_sonic(w, name, p): return w.call(id=name, name=name, retarget_dir=p["retarget"], sonic_dir=p["sonic"], log_dir=p["logs"], trace_dir=p["traces"])
def slim(r): return {k: v for k, v in r.items() if k not in ("qc", "t_start", "t_end")}
def start_workers(sp, tag):
    ws = {}; load = {}
    for k in ("st", "gmr", "sonic"):
        ws[k] = Worker(k, B, sampler=sp); load[k] = dict(ws[k].ready); print(tag, "ready", load[k], "MemAvail", round(mem_avail_gb(), 1), flush=True)
        load[k]["vram_after_load_mb"] = None
    return ws, load

def warm():
    sp = Sampler(B / "samples_warm.csv"); sp.start(); ws, load = start_workers(sp, "warm"); time.sleep(1.5)
    for k in load: load[k]["vram_after_load_mb"] = sp.rows[-1].get(f"{k}_vram_mb")
    rows = []; status = []
    for name in CLIPS:
        p = rec(name, MOT, 1); t_sub = time.monotonic(); attempts = []; seed = 1
        for att in range(4):
            seed = [1, 2, 3, 4][att]; a = {"seed": seed}
            r1 = stage_st(ws["st"], name, p, seed); a["st"] = slim(r1)
            if not r1["ok"]: attempts.append(a); break
            r2 = stage_gmr(ws["gmr"], name, p); a["gmr"] = slim(r2); a["qc"] = r2.get("qc"); attempts.append(a)
            if r2["ok"] and r2["status"] == "PASS": break
            if not (r2.get("qc") or {}).get("fail", "") or "self_collision" not in str(r2["qc"].get("fail")): break
        last = attempts[-1]
        if last.get("gmr", {}).get("ok"):
            r3 = stage_sonic(ws["sonic"], name, p); last["sonic"] = slim(r3)
        e2e = time.monotonic() - t_sub; ok = bool(last.get("gmr", {}).get("ok") and last.get("sonic", {}).get("ok"))
        stt = {"clip": name, "audio_s": last["st"].get("audio_s"), "seed_final": last["seed"], "attempts": len(attempts), "gmr_status": last.get("gmr", {}).get("status"), "gmr_qc_fail": (last.get("qc") or {}).get("fail"),
               "qc": last.get("qc"), "sonic_status": last.get("sonic", {}).get("sonic_status"), "ok": ok, "e2e_s": round(e2e, 2), "final_attempt": last, "all_attempts": [{"seed": x["seed"], "gmr_status": x.get("gmr", {}).get("status"), "qc_fail": (x.get("qc") or {}).get("fail")} for x in attempts]}
        (p["M"]).mkdir(parents=True, exist_ok=True); (p["M"] / "status.json").write_text(json.dumps(stt, indent=1)); status.append(stt)
        rows.append(dict(clip=name, audio_s=stt["audio_s"], seed=last["seed"], attempts=len(attempts), st_s=sum(x["st"]["wall_s"] for x in attempts), gmr_s=sum(x.get("gmr", {}).get("wall_s", 0) for x in attempts),
                         convert_s=last.get("sonic", {}).get("convert_s"), rollout_s=last.get("sonic", {}).get("rollout_s"), sonic_s=last.get("sonic", {}).get("wall_s"), e2e_s=round(e2e, 2), gmr_status=stt["gmr_status"], ok=ok))
        print("warm", rows[-1], flush=True)
    time.sleep(1.5); sp.stop()
    for w in ws.values(): w.stop()
    json.dump(dict(load=load, rows=rows, status=[{k: v for k, v in s.items() if k != "final_attempt"} for s in status]), open(B / "warm_results.json", "w"), indent=1)

def pipe(n=12):
    sp = Sampler(B / "samples_pipe.csv"); sp.start(); ws, load = start_workers(sp, "pipe"); out = {}
    names = CLIPS[:n]; warmres = json.load(open(B / "warm_results.json"))["rows"]
    serial = sum(r["e2e_s"] for r in warmres if r["clip"] in names)
    for mode, interval in (("burst", 0.0), ("staggered", None)):
        if interval is None: interval = out["burst"]["bottleneck_stage_mean_s"] * 1.15
        qs = [queue.Queue() for _ in range(4)]; res = {}; lock = threading.Lock(); root = B / f"pipelined/{mode}"
        def runner(k, qin, qout, fn):
            while True:
                it = qin.get()
                if it is None:
                    if qout: qout.put(None)
                    return
                name, p = it; r = fn(name, p)
                with lock: res[name][k] = {"start": r["t_start"], "end": r["t_end"], "wall": r["wall_s"], "ok": r["ok"]}
                if qout: qout.put(it)
        fns = [("st", lambda n, p: stage_st(ws["st"], n, p, 1)), ("gmr", lambda n, p: stage_gmr(ws["gmr"], n, p)), ("sonic", lambda n, p: stage_sonic(ws["sonic"], n, p))]
        ths = [threading.Thread(target=runner, args=(k, qs[i], qs[i + 1] if i < 2 else None, f)) for i, (k, f) in enumerate(fns)]
        [t.start() for t in ths]; t0 = time.monotonic()
        for i, name in enumerate(names):
            tgt = t0 + i * interval; time.sleep(max(0, tgt - time.monotonic()))
            res[name] = {"arrive": time.monotonic()}; qs[0].put((name, rec(name, root, 1)))
        qs[0].put(None); [t.join() for t in ths]; mk = time.monotonic() - t0
        lat = [res[n]["sonic"]["end"] - res[n]["arrive"] for n in names]
        means = {k: st.mean(res[n][k]["wall"] for n in names) for k in ("st", "gmr", "sonic")}
        out[mode] = dict(n=n, interval_s=round(interval, 2), makespan_s=round(mk, 1), serial_baseline_s=round(serial, 1), lat_p50=pct(lat, .5), lat_p95=pct(lat, .95), lat_mean=round(st.mean(lat), 2),
                         stage_wall_mean_s={k: round(v, 2) for k, v in means.items()}, bottleneck_stage_mean_s=max(means.values()), all_ok=all(res[n][k]["ok"] for n in names for k in ("st", "gmr", "sonic")),
                         per_clip_latency={n: round(l, 2) for n, l in zip(names, lat)})
        print("pipe", mode, {k: v for k, v in out[mode].items() if k != "per_clip_latency"}, flush=True)
    time.sleep(1.5); sp.stop()
    for w in ws.values(): w.stop()
    json.dump(out, open(B / "pipe_results.json", "w"), indent=1)

def cold(clips):
    out = []
    ROOT = P.streamtalk_root(); MO = P.MOTION_DIR
    CK = ROOT / "checkpoints/pretrained/streamtalk_speaker_all_e0940_cfg3.pt"
    CSV = str(P.streamtalk_train_csv())
    PP = P.py_physicalai(); GP = P.py_gmr()
    for name in clips:
        p = rec(name, B / "cold", 1); M = p["M"]; (M / "x2/inputs").mkdir(parents=True, exist_ok=True); (M / "prof").mkdir(exist_ok=True); r = {"clip": name}
        def stg(sn, cmd, env=None):
            tf = M / f"prof/time_{sn}.txt"; sp = Sampler(M / f"prof/samples_{sn}.csv"); base = None
            t0 = time.monotonic(); sp.start(); time.sleep(0.6); base = sp.rows[0].get("vram_total_mb", 0)
            rc = subprocess.run(["/usr/bin/time", "-v", "-o", str(tf)] + cmd, stdout=open(M / f"prof/log_{sn}.txt", "w"), stderr=subprocess.STDOUT, env={**os.environ, **(env or {})}, cwd=ROOT).returncode
            wall = time.monotonic() - t0 - 0.6; sp.stop(); tx = tf.read_text()
            r[sn] = dict(rc=rc, wall_s=round(wall, 2), peak_rss_mb=round(int(re.search(r"Maximum resident set size.*?: (\d+)", tx).group(1)) / 1024), peak_vram_mb=(sp.peak("vram_total_mb") or 0) - base); print("cold", name, sn, r[sn], flush=True); return rc == 0
        ok = stg("S1_streamtalk", [PP, str(MO / "streamtalk_cached_entry.py"), p["wav"], str(CK), "--example_motion_npz", str(P.READY_PROMPT), "--pid", "1_wayne_0", "--seed", "1", "--output_file", p["smplx"],
              "--wavlm_model", str(ROOT / "assets/wavlm-libri-clean-100h-large"), "--smplx_model", str(P.smplx_neutral_npz()),
              "--retrieval_data_folder", str(P.beat2_smplx_dir()), "--retrieval_csv_file", CSV, "--cfg_scale", "3", "--retrieval_mode", "native",
              "--cache-dir", str(P.streamtalk_retrieval_cache()), "--csv", CSV])
        if ok:
            z = np.load(p["smplx"]); pose = np.asarray(z["poses"], np.float32); betas = np.zeros(16, np.float32); betas[:10] = np.asarray(z["betas"], np.float32).reshape(-1)[:10]
            np.savez_compressed(M / f"x2/inputs/{name}_gmr_stageii.npz", gender=np.asarray("neutral"), root_orient=pose[:, :3].copy(), pose_body=pose[:, 3:66].copy(), betas=betas, trans=np.asarray(z["trans"], np.float32).copy(),
                mocap_frame_rate=np.asarray(30.0, np.float32), source_model=np.asarray(name), source_frame_count=np.asarray(len(pose), np.int32), common_frame_count=np.asarray(len(pose), np.int32), adapter_schema=np.asarray("RCP01C_SMPLX_TO_X2_GMR_STAGEII_V1"))
            ok = stg("S2_gmr", [GP, str(MO / "x2_retarget.py"), "--model", name, "--input-dir", str(M / "x2/inputs"), "--output-dir", p["retarget"], "--force"])
        if ok: ok = stg("S3_convert", [PP, str(MO / "x2_sonic.py"), "convert", "--model", name, "--retarget-dir", p["retarget"], "--sonic-out-dir", p["sonic"], "--log-dir", p["logs"], "--force"])
        if ok: ok = stg("S4_rollout", [PP, str(MO / "x2_sonic.py"), "rollout", "--model", name, "--retarget-dir", p["retarget"], "--sonic-out-dir", p["sonic"], "--log-dir", p["logs"], "--trace-dir", p["traces"], "--no-record", "--force"], {"X2_TRACE_DIR": p["traces"], "X2_TRACE_TAG": name})
        r["total_s"] = round(sum(v["wall_s"] for k, v in r.items() if k.startswith("S")), 2); out.append(r)
    json.dump(out, open(B / "cold_results.json", "w"), indent=1)

def compare(clips):
    import pickle; res = []
    for name in clips:
        w, c = MOT / name, B / "cold" / name; d = {"clip": name}
        a, b = np.load(w / f"smplx/{name}.npz"), np.load(c / f"smplx/{name}.npz")
        for k in ("poses", "trans", "expressions"): d[f"smplx_{k}_maxabs"] = float(np.abs(a[k] - b[k]).max()) if a[k].shape == b[k].shape else "shape"
        a, b = np.load(w / f"x2/retarget/{name}_x2_gmr_full.npz"), np.load(c / f"x2/retarget/{name}_x2_gmr_full.npz"); d["retarget_qpos_maxabs"] = float(np.abs(a["qpos"] - b["qpos"]).max())
        a, b = pickle.load(open(w / f"sonic/{name}_full_ramp_sonic.pkl", "rb"))[name], pickle.load(open(c / f"sonic/{name}_full_ramp_sonic.pkl", "rb"))[name]; d["sonic_dof_maxabs"] = float(np.abs(a["dof"] - b["dof"]).max())
        import pandas as pd
        ta, tb = pd.read_csv(w / f"traces/full_{name}.csv"), pd.read_csv(c / f"traces/full_{name}.csv"); d["trace_rows"] = [len(ta), len(tb)]
        num = [k for k in ta.columns if ta[k].dtype.kind == "f" and k in tb.columns]; d["trace_actual_maxabs"] = float(max(np.abs(ta[k].values - tb[k].values).max() for k in num if k.endswith("_actual_rad"))) if len(ta) == len(tb) else "rows"
        d["trace_bytes_equal"] = (w / f"traces/full_{name}.csv").read_bytes() == (c / f"traces/full_{name}.csv").read_bytes(); res.append(d); print(d, flush=True)
    json.dump(res, open(B / "compare_results.json", "w"), indent=1)

if __name__ == "__main__":
    m = sys.argv[1]
    if m == "warm": warm()
    elif m == "pipe": pipe(int(sys.argv[2]) if len(sys.argv) > 2 else 12)
    elif m == "cold": cold(sys.argv[2].split(","))
    elif m == "compare": compare(sys.argv[2].split(","))
