#!/usr/bin/env python3
"""O3 BRAIN: M2 MotionServer (serial pull chain, native_qp 15 Hz, fast prefix) + (a) selectable O1a models (lean|v1|v2),
(b) first-block split of the GMR frame conversion, (c) a per-tick sink (chunks to the motor), (d) GMR qpos capture for QC.
physicalai env.  Imports the M2 modules in place (opt/m2_motion_server) and changes none of them."""
import json, os, sys, time
from pathlib import Path
import numpy as np
M2 = Path(__file__).resolve().parents[1] / "m2_motion_server"
sys.path.insert(0, str(M2))
from m2common import *            # noqa: E402  (HERE == M2 dir)
import server as M2S              # noqa: E402
from server import MotionServer, PREFIX   # noqa: E402,F401

O1A = Path(__file__).resolve().parents[1] / "o1a_meshes/models"
MODELS = {"lean": (str(MJB_DIR / "ik_lean_3.11.0.mjb"), str(MJB_DIR / "sonic_x2_ultra.mjb")),     # M2 default (IK = V1-equivalent, SONIC = full)
          "v1": (str(O1A / "ik_v1_3.11.0.mjb"), str(O1A / "sonic_v1_3.11.0.mjb")),
          "v2": (str(O1A / "ik_v2_3.11.0.mjb"), str(O1A / "sonic_v2_3.11.0.mjb"))}


class Brain(MotionServer):
    def __init__(self, models="v2", **kw):
        kw.setdefault("solver", "native"); kw.setdefault("rate", 15); kw.setdefault("prefix", "fast"); kw.setdefault("pipeline", "serial")
        super().__init__(**kw); self.models = models

    def load(self):
        import st_stream, gmr_stream, sonic_stream
        self.ST, self.GM, self.SO = st_stream, gmr_stream, sonic_stream
        ik, so = MODELS[self.models]; t = {}; s0 = time.perf_counter()
        t["streamtalk"] = st_stream.load()
        t0 = time.perf_counter(); gmr_stream.load(ik); t["gmr"] = time.perf_counter() - t0
        t0 = time.perf_counter(); sonic_stream.load(mjb_name=so, ort_threads=self.ort_threads); t["sonic"] = time.perf_counter() - t0; trim()
        t["total"] = time.perf_counter() - s0; self.load_s = t; return t

    def run(self, audio, sink=None, split=0, keep_q=False, audio_rtf=0.0, t_origin=None, on_first=None):
        """sink(k0, arr[n,19] float32 (arms14, waist3, head2), t_mono) is called for every batch of ticks as soon as they exist.
        split>0: the first `split` SMPL-X frames of window 0 are converted/solved first (their qpos are pushed before the rest is converted)."""
        ST, GM, SO = self.ST, self.GM, self.SO
        t0 = time.perf_counter() if t_origin is None else t_origin; now = lambda: time.perf_counter() - t0
        gmr = self._armed if (self.armed and getattr(self, "_armed", None) is not None) else GM.GmrStream(self.solver, self.rate, betas=ST.S["betas"], warm_iters=self.warm_iters)
        self._armed = None; t_gmr_ready = now()
        hold, blend = PREFIX[self.prefix]; sonic = SO.SonicOnline(hold, blend); rs = SO.Resampler(self.rate)

        def avail(b):
            if audio_rtf > 0:
                w = audio_rtf * b / 16000 - now()
                if w > 0: time.sleep(w)
        def st_gen():
            for n, nw, p, t, info in ST.stream(audio, 1, avail_fn=avail):
                info["t_ready"] = now(); yield n, p, t, info
        def gmr_gen():
            for n, p, t, info in st_gen():
                b0 = gmr.busy
                if split and n == 0 and len(p) > split:
                    for q in gmr.push_iter(p[:split], t[:split]): yield "q", q
                    info["t_split_done"] = now()
                    for q in gmr.push_iter(p[split:], t[split:]): yield "q", q
                else:
                    for q in gmr.push_iter(p, t): yield "q", q
                info["gmr_s"] = gmr.busy - b0; info["t_gmr"] = now(); yield "end", (n, info)
        walls, mfs, arms, whs, pz, tl = [], [], [], [], [], []; wins = []; first = [True]; tick_s = [0.0]; qs = []; wall_mono = []
        pending = []

        def feed(q):
            if first[0]: sonic.start(q); first[0] = False
            sonic.push_ref(q)
        def run_ticks():
            k0 = len(walls); out = []
            while sonic.can_tick():
                ts = time.perf_counter(); mf, a, w, z, t = sonic.tick(); tick_s[0] += time.perf_counter() - ts
                walls.append(now()); wall_mono.append(time.monotonic()); mfs.append(mf); arms.append(a); whs.append(w); pz.append(z); tl.append(t); out.append(np.concatenate([a, w]))
            if out:
                if len(walls) == len(out) and on_first: on_first(now())
                if sink: sink(k0, np.asarray(out, np.float32), time.monotonic())
        for kind, item in gmr_gen():
            if kind == "q":
                if keep_q: qs.append(item)
                for q50 in rs.push(item): feed(q50)
                run_ticks()
            else:
                n, info = item; info["sonic_s"] = tick_s[0]; tick_s[0] = 0.0; info["t_sonic"] = now(); info["win"] = n; wins.append(info)
        for q50 in rs.flush(): feed(q50)
        sonic.end_ref(); run_ticks(); wins[-1]["sonic_s"] += tick_s[0]; t_end = now()
        walls = np.array(walls); k = np.arange(len(walls)); prefix_n = hold + int(round(blend * 50)); D = float((walls - 0.02 * k).max())
        res = dict(first_cmd_s=float(walls[0]), first_motion_cmd_s=float(walls[prefix_n]) if len(walls) > prefix_n else None, lead_D_s=D, audio_start_s=D + prefix_n * 0.02,
                   prefix_n=prefix_n, t_total_s=t_end, ticks=len(walls), duration_s=len(walls) * 0.02, audio_s=len(audio) / 16000, arm_s=t_gmr_ready,
                   rtf=t_end / (len(audio) / 16000), windows=wins, fell=sonic.fell, tilt_max=float(max(tl)), pelvis_min=float(min(pz)),
                   t_first_window_ready=wins[0]["t_ready"], t_split_done=wins[0].get("t_split_done"), lag_last_s=float(walls[-1] - 0.02 * (len(walls) - 1)))
        data = dict(wall=walls, wall_mono=np.array(wall_mono), mf=np.array(mfs), arms=np.array(arms, np.float32), wh=np.array(whs, np.float32), pelvis_z=np.array(pz, np.float32), tilt=np.array(tl, np.float32))
        if keep_q: data["gmr_q"] = np.array(qs, np.float32)
        return res, data


def qc_for(q, rate):
    """Same QC as the offline chain / gmr_bench (after ground_track)."""
    B = M2S.sys.modules["gmr_stream"].G["batch"]
    qg, _ = B.ground_track(np.asarray(q, np.float64), float(rate)); return B.qc_checks(qg, float(rate))
