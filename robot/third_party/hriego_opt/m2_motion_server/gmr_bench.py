#!/usr/bin/env python3
"""GMR-only micro-benchmark inside the physicalai process (cpu pinned by the caller): per-frame cost breakdown, QC (after ground_track, as offline), and
arm/waist qpos difference vs the offline native_qp qpos, for solver x rate x (full | upper-body-only tasks) on a few clips."""
import json, sys, time
import numpy as np
from m2common import *
import gmr_stream as GS
GS.load()
B = GS.G["batch"]; mink_skip = ("foot", "toe", "ankle", "knee", "hip", "Foot", "Toe", "Ankle", "Knee", "Hip", "pelvis", "Pelvis")
z0 = np.load(P.READY_PROMPT, allow_pickle=True); betas = z0["betas"].astype(np.float32)
clips = sys.argv[1].split(",") if len(sys.argv) > 1 else ["c010", "c018", "v_guest_t01_guidance_POINT"]
specs = [("native", 30, False), ("native", 15, False), ("native", 50, False), ("analytic", 30, False), ("analytic", 15, False), ("native", 30, True), ("native", 15, True)]
out = []
for solver, rate, upper in specs:
    for c in clips:
        d = np.load(HERE / "out/offline_ref" / c / f"{c}_smplx.npz"); ref = np.load(HERE / "out/offline_ref" / c / "retarget" / f"{c}_x2_gmr_full.npz"); names = list(ref["joint_names"])
        g = GS.GmrStream(solver, rate, betas=betas, warm_iters=20)
        if upper:   # keep only arm/torso/head body tasks: zero the leg/foot/pelvis frame tasks (posture task untouched)
            import mink
            n0 = 0
            for tasks in (g.rt.tasks1, g.rt.tasks2):
                for t in tasks:
                    if isinstance(t, mink.FrameTask) and any(k in t.frame_name for k in ("hip", "knee", "ankle", "foot", "toe", "pelvis")):
                        t.set_position_cost(0.0); t.set_orientation_cost(0.0); n0 += 1
        t = time.perf_counter(); fr = g.frames(d["poses"], d["trans"]); t_fr = time.perf_counter() - t
        qs = []; t = time.perf_counter()
        # feed pre-converted frames through the same solve path
        g.prev = None; g.src_idx = 0; g.next_j = 0
        class Dummy: pass
        for f in fr:
            if g.prev is None:
                for _ in range(20): g.rt.retarget({k: (v[0].copy(), v[1].copy()) for k, v in f.items()})
            if rate in (30, 15, 10):
                if g.src_idx % (30 // rate) == 0: qs.append(g._solve(f))
            else:
                if g.prev is not None:
                    while g.next_j * 0.6 <= g.src_idx and g.next_j * 0.6 > g.src_idx - 1 - 1e-9:
                        qs.append(g._solve(GS._lerp_frames(g.prev, f, g.next_j * 0.6 - (g.src_idx - 1)))); g.next_j += 1
                else: qs.append(g._solve(f)); g.next_j = 1
            g.prev = f; g.src_idx += 1
        t_ik = time.perf_counter() - t; q = np.stack(qs)
        qg, _ = B.ground_track(q, float(rate)); qc = B.qc_checks(qg, float(rate))
        # compare with offline qpos on the 30 fps timeline (linear interp of joints)
        tq = np.arange(len(q)) / rate; tr = np.arange(len(ref["qpos"])) / 30.0; m = tr <= tq[-1]
        qi = np.stack([np.interp(tr[m], tq, q[:, k]) for k in range(7, 38)], 1); rq = ref["qpos"][m][:, 7:]
        arm = [i for i, n in enumerate(names) if ("shoulder" in n or "elbow" in n or "wrist" in n)]; wst = [i for i, n in enumerate(names) if "waist" in n]
        out.append(dict(solver=solver, rate=rate, upper_only=upper, clip=c, n_src=len(fr), n_solved=len(q), frames_ms=1000 * t_fr / len(fr), ik_ms_per_solve=1000 * t_ik / len(q), ik_ms_per_src_frame=1000 * t_ik / len(fr),
                        qc_fail=qc.get("fail"), selfcol=qc.get("selfcol_frac"), max_joint_vel=qc.get("max_joint_vel"), arm_rmse_vs_offline=float(np.sqrt(((qi[:, arm] - rq[:, arm]) ** 2).mean())),
                        waist_rmse_vs_offline=float(np.sqrt(((qi[:, wst] - rq[:, wst]) ** 2).mean()))))
        print(json.dumps(out[-1]), flush=True); trim()
json.dump(out, open(HERE / "gmr_bench.json", "w"), indent=1)
