#!/usr/bin/env python3
"""M2 online motion server prototype: ONE physicalai process, StreamTalk(lean fp16) -> GMR(lean .mjb, mink) -> SONIC(lean .mjb), streaming.
`MotionServer.run(audio)` returns the 50 Hz command stream (arms14 + waist3 + head2 actual joints per tick) with wall-clock stamps.
CLI (benchmark): python server.py --tag native30 --solver native --rate 30 --prefix fast [--pipeline threads] [--clips c010,c001]"""
import argparse, json, os, sys, time
import numpy as np
from m2common import *

PREFIX = {"exact": (25, 1.5), "fast": (2, 0.5)}      # (hold frames, blend seconds); exact == x2_sonic.convert_one


class MotionServer:
    def __init__(self, solver="native", rate=30, prefix="fast", pipeline="serial", warm_iters=20, armed=False, ort_threads=2):
        self.solver, self.rate, self.prefix, self.pipeline, self.warm_iters, self.armed = solver, rate, prefix, pipeline, warm_iters, armed
        self.ort_threads = ort_threads; self.mem = {}

    def load(self):
        import st_stream, gmr_stream, sonic_stream
        self.ST, self.GM, self.SO = st_stream, gmr_stream, sonic_stream
        t = {}; s0 = time.perf_counter(); self.mem["start"] = dict(pss=smaps()["Pss"])
        t["streamtalk"] = st_stream.load(); self.mem["after_st"] = dict(pss=smaps()["Pss"], vram=vram_mb())
        t0 = time.perf_counter(); gmr_stream.load(); t["gmr"] = time.perf_counter() - t0; self.mem["after_gmr"] = dict(pss=smaps()["Pss"], vram=vram_mb())
        t0 = time.perf_counter(); sonic_stream.load(ort_threads=self.ort_threads); t["sonic"] = time.perf_counter() - t0; trim()
        self.mem["after_sonic"] = dict(pss=smaps()["Pss"], rss=smaps()["Rss"], vram=vram_mb()); t["total"] = time.perf_counter() - s0
        self.load_s = t
        return t

    def arm(self):
        """Idle-time preparation for the next request: retargeter object (+collision limit) and a READY-pose warm start."""
        if not self.armed: return
        t = time.perf_counter(); S = self.ST.S
        self._armed = self.GM.GmrStream(self.solver, self.rate, betas=S["betas"], warm_iters=self.warm_iters)
        fr = self._armed.frames(S["poses0"][:2].copy(), S["trans0"][:2].copy())
        for _ in range(20): self._armed.rt.retarget({k: (v[0].copy(), v[1].copy()) for k, v in fr[0].items()})
        self._armed.warm_iters = 3; self.t_arm = time.perf_counter() - t

    def run(self, audio, audio_rtf=0.0, keep_st=False):
        ST, GM, SO = self.ST, self.GM, self.SO
        t0 = time.perf_counter(); now = lambda: time.perf_counter() - t0
        if self.armed: gmr = self._armed; self._armed = None
        else: gmr = GM.GmrStream(self.solver, self.rate, betas=ST.S["betas"], warm_iters=self.warm_iters)
        t_gmr_ready = now()
        hold, blend = PREFIX[self.prefix]; sonic = SO.SonicOnline(hold, blend); rs = SO.Resampler(self.rate)

        def avail(b):
            if audio_rtf > 0:
                w = audio_rtf * b / 16000 - now()
                if w > 0: time.sleep(w)
        def st_gen():
            for n, nw, p, t, info in ST.stream(audio, 1, avail_fn=avail):
                info["t_ready"] = now(); yield n, p, t, info
        def gmr_gen():
            for n, p, t, info in st_iter:
                b0 = gmr.busy
                for q in gmr.push_iter(p, t): yield "q", q
                info["gmr_s"] = gmr.busy - b0; info["t_gmr"] = now(); yield "end", (n, info, (p, t))
        st_iter = ThreadedIter(st_gen(), 2) if self.pipeline == "threads" else st_gen()
        gmr_iter = ThreadedIter(gmr_gen(), 4) if self.pipeline == "threads" else gmr_gen()
        walls, mfs, arms, whs, pz, tl = [], [], [], [], [], []; wins = []; st_p, st_t = [], []; first = [True]; tick_s = [0.0]

        def feed(q):
            if first[0]: sonic.start(q); first[0] = False
            sonic.push_ref(q)
        def run_ticks():
            while sonic.can_tick():
                ts = time.perf_counter(); mf, a, w, z, t = sonic.tick(); tick_s[0] += time.perf_counter() - ts
                walls.append(now()); mfs.append(mf); arms.append(a); whs.append(w); pz.append(z); tl.append(t)
        for kind, item in gmr_iter:
            if kind == "q":
                for q50 in rs.push(item): feed(q50)
                run_ticks()
            else:
                n, info, blk = item
                if keep_st: st_p.append(blk[0]); st_t.append(blk[1])
                info["sonic_s"] = tick_s[0]; tick_s[0] = 0.0; info["t_sonic"] = now(); info["win"] = n; wins.append(info)
        for q50 in rs.flush(): feed(q50)
        sonic.end_ref(); run_ticks(); wins[-1]["sonic_s"] += tick_s[0]; t_end = now()
        walls = np.array(walls); k = np.arange(len(walls)); prefix_n = hold + int(round(blend * 50))
        D = float((walls - 0.02 * k).max())
        res = dict(first_cmd_s=float(walls[0]), first_motion_cmd_s=float(walls[prefix_n]) if len(walls) > prefix_n else None, lead_D_s=D, audio_start_s=D + prefix_n * 0.02,
                   prefix_n=prefix_n, t_total_s=t_end, ticks=len(walls), duration_s=len(walls) * 0.02, audio_s=len(audio) / 16000, arm_s=t_gmr_ready,
                   rtf=t_end / (len(audio) / 16000), windows=wins, fell=sonic.fell, tilt_max=float(max(tl)), pelvis_min=float(min(pz)),
                   t_first_window_ready=wins[0]["t_ready"], lag_last_s=float(walls[-1] - 0.02 * (len(walls) - 1)))
        data = dict(wall=walls, mf=np.array(mfs), arms=np.array(arms, np.float32), wh=np.array(whs, np.float32), pelvis_z=np.array(pz, np.float32), tilt=np.array(tl, np.float32))
        if keep_st: data["st_poses"] = np.concatenate(st_p); data["st_trans"] = np.concatenate(st_t)
        return res, data


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True); ap.add_argument("--solver", default="native", choices=["native", "analytic"]); ap.add_argument("--rate", type=int, default=30)
    ap.add_argument("--prefix", default="fast", choices=list(PREFIX)); ap.add_argument("--pipeline", default="serial", choices=["serial", "threads"])
    ap.add_argument("--clips", default=""); ap.add_argument("--armed", action="store_true"); ap.add_argument("--audio-rtf", type=float, default=0.0)
    ap.add_argument("--warm-iters", type=int, default=20); ap.add_argument("--no-warmup", action="store_true"); ap.add_argument("--save-st", action="store_true")
    a = ap.parse_args()
    out = HERE / "out" / a.tag; out.mkdir(parents=True, exist_ok=True)
    srv = MotionServer(a.solver, a.rate, a.prefix, a.pipeline, a.warm_iters, a.armed)
    sam = MemSampler(); sam.start()
    load_s = srv.load(); print("LOAD", json.dumps(load_s), json.dumps(srv.mem), flush=True)
    import librosa
    clips = json.load(open(CLIPS_JSON)); names = a.clips.split(",") if a.clips else list(clips)
    t_run0 = time.perf_counter() - sam.t0
    if not a.no_warmup:
        srv.arm(); w, _ = srv.run(librosa.load(clips["c010"], sr=16000)[0][:16000 * 3]); print("WARMUP ok first_cmd", round(w["first_cmd_s"], 3), flush=True)
    rows = []
    for name in names:
        audio = librosa.load(clips[name], sr=16000)[0]
        srv.arm(); res, data = srv.run(audio, a.audio_rtf, keep_st=a.save_st)
        sm = smaps(); res.update(name=name, pss_mb=sm["Pss"], rss_mb=sm["Rss"], vram_mb=vram_mb(), torch_peak_mb=srv.ST.S["torch"].cuda.max_memory_allocated() / 2**20)
        np.savez(out / f"{name}.npz", **data); rows.append(res)
        print(name, f"audio {res['audio_s']:.1f}s first_cmd {res['first_cmd_s']:.2f} D {res['lead_D_s']:.2f} audio_start {res['audio_start_s']:.2f} total {res['t_total_s']:.1f} rtf {res['rtf']:.2f} pss {sm['Pss']:.0f} vram {res['vram_mb']} fell {res['fell']}", flush=True)
        trim()
    sam.stop(); hwm = status_mb()["VmHWM"]
    summ = dict(tag=a.tag, args=vars(a), load_s=load_s, mem_load=srv.mem, vmhwm_mb=hwm, pss_peak_run_mb=sam.peak(t_run0)[0], rss_peak_run_mb=sam.peak(t_run0)[1],
                pss_peak_all_mb=sam.peak(0)[0], rows=rows, cpu_affinity=sorted(os.sched_getaffinity(0)))
    json.dump(summ, open(out / "summary.json", "w"), indent=1, default=float)
    print("DONE", a.tag, "VmHWM", round(hwm), flush=True); sys.stdout.flush(); sys.stderr.flush(); os._exit(0)   # skip CUDA/ORT teardown (occasionally hangs)


if __name__ == "__main__":
    main()
