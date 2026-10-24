#!/usr/bin/env python3
"""TASK TWIN-V driver: replay recorded motion through TwinBackend INSIDE x2-twin.

Sections: S2 SONIC replay, S3 POINT over SONIC, S4 EMOTES kinematic CSV, S5 WALK->SETTLE->POINT.
Dumps raw command/feedback traces + events + phases to /tmp/x2r_out/*.npz|json for host analysis.
Safety: fall interlock always on (backend), low-speed short walk, no gantry, no gestures while walking.
"""
import json, os, sys, threading, time
import numpy as np

os.environ.setdefault("X2_REAL", "gemelo")
os.environ.setdefault("X2_HRIEGO_ROOT", "/tmp/x2r_assets/hri/HRIEGO_gesture")
os.environ.setdefault("X2_MC_CLASSIC", "/tmp/x2r_assets/hri/mc_classic")
sys.path.insert(0, "/tmp/x2r")

from x2_ultra.hri_runtime.backends.twin import TwinBackend, twin_config
from x2_ultra.hri_runtime.backends.aimdk import READY_ARMS
from x2_ultra.hri_runtime.backends.base import TICK
from x2_ultra.robot import joints as J

OUT = "/tmp/x2r_out"; os.makedirs(OUT, exist_ok=True)
TRACKS = ["sebastian_t02", "laura_t02", "josaed_t03"]
EMOTES = [(3001, 11, "VIP_BOW"), (3004, 11, "SOCIAL_HEART"), (3017, 11, "APPLAUSE")]
PHASES = []           # [label, t0, t1]
_stop = [False]


def log(*a):
    print(f"[{time.strftime('%H:%M:%S')}]", *a, flush=True)


class Rec:
    """Wraps the live link: records every publish (LiveLink does not accumulate commands)."""
    def __init__(self, link):
        self.l = link
        self.upper = []   # (t, head_yaw, head_pitch, arms14, meas14|None)
        self.vel = []     # (t, vx, vy, wz)
        self.bp = []      # (t, u4)

    def __getattr__(self, n):
        return getattr(self.l, n)

    def publish_upper_body(self, head, arms, hsm, hp, sm):
        r = self.l.publish_upper_body(head, arms, hsm, hp, sm)
        try:
            d = self.l.latest("arms")
            meas = [float(d[n]) for n in J.ARM_JOINTS] if d else None
        except Exception:
            meas = None
        self.upper.append((time.monotonic(), float(head[0]), float(head[1]),
                           [float(v) for v in arms], meas))
        return r

    def publish_velocity(self, vx, vy, wz, sm):
        self.vel.append((time.monotonic(), float(vx), float(vy), float(wz)))
        return self.l.publish_velocity(vx, vy, wz, sm)

    def send_body_pose(self, u4):
        self.bp.append((time.monotonic(), [float(v) for v in np.asarray(u4, float).reshape(-1)[:4]]))
        return self.l.send_body_pose(u4)


def phase(label):
    PHASES.append([label, time.monotonic(), None])
    log("PHASE BEGIN", label)


def end_phase():
    PHASES[-1][2] = time.monotonic()
    log("PHASE END", PHASES[-1][0], f"{PHASES[-1][2]-PHASES[-1][1]:.2f}s")


def sleep_until(t):
    while time.monotonic() < t:
        time.sleep(min(0.002, max(0.0, t - time.monotonic())))


def main():
    cfg = twin_config(arm=True, routes={"short_fwd": [(0.12, 0.0, 2.0)]})
    be = TwinBackend(cfg)
    rec = Rec(be._link)
    be._link = rec; be.link = rec
    # warm-up: wait for MC STAND_DEFAULT before arming
    time.sleep(4.0)
    _t0 = time.time()
    while time.time() - _t0 < 20:
        try:
            if "STAND_DEFAULT" in str(be.observed(graph=True).mode):
                break
        except Exception:
            pass
        time.sleep(0.5)
    probs = be.arm_session(timeout_s=25.0)
    log("arm_session problems:", probs)
    if probs:
        dump(be, rec, {}, armed=False); return 2
    # wait stable
    t0 = time.time()
    while time.time() - t0 < 30:
        st = be.state()
        if st["stable"]:
            break
        time.sleep(0.1)
    log("state after arm:", be.state())

    states = []
    meas_waist = []; meas_legs = []
    def sampler():
        while not _stop[0]:
            try:
                _l = getattr(be, "_link", None)
                _w = getattr(_l, "wh_state", None) or {}
                if "waist" in _w:
                    meas_waist.append((time.monotonic(), *[float(x) for x in _w["waist"]]))
                _lg = _l.latest("leg") if _l is not None and hasattr(_l, "latest") else None
                if _lg is not None:
                    meas_legs.append((time.monotonic(), *[float(x) for x in (_lg.values() if isinstance(_lg, dict) else np.asarray(_lg).reshape(-1))]))
            except Exception:
                pass
            try:
                st = be.state()
                states.append((time.monotonic(), st.get("pelvis_z"), st.get("tilt_deg"),
                               st.get("mode"), st.get("stable"), st.get("walking"), st.get("fall"),
                               be.limiter.nlim, be.unsafe, be.estopped))
            except Exception:
                pass
            time.sleep(0.02)
    th = threading.Thread(target=sampler, daemon=True); th.start()

    # SONIC feed thread (for POINT-over-SONIC and post-emote transitions)
    feed_on = [False]
    tr = np.load(f"/tmp/x2r_tracks/{TRACKS[1]}_dialogue_online.npz")
    def feeder():
        i = 0
        while not _stop[0]:
            if feed_on[0] and be._point is None and be._track is None and not be.walking():
                n = len(tr["t"])
                be.stream_upper_body(float(tr["t"][i]), tr["arms"][i], tr["waist"][i], tr["head"][i])
                i = (i + 1) % n
            time.sleep(TICK)
    fth = threading.Thread(target=feeder, daemon=True); fth.start()

    meta = {}
    try:
        # ---------------- S2: SONIC dialogue replay, 3 tracks ----------------
        for name in TRACKS:
            if be.unsafe:
                log("UNSAFE - skipping S2", name); break
            d = np.load(f"/tmp/x2r_tracks/{name}_dialogue_online.npz")
            t, a, w, h = d["t"], d["arms"], d["waist"], d["head"]
            phase(f"S2_{name}")
            t_next = time.monotonic() + 0.05
            for i in range(len(t)):
                be.stream_upper_body(float(t[i]), a[i], w[i], h[i])
                t_next += TICK
                sleep_until(t_next)
            time.sleep(0.3)          # let the pump drain
            end_phase()
            time.sleep(0.7)

        # ---------------- S3: POINT right/left/front over SONIC ----------------
        feed_on[0] = True; time.sleep(1.0)
        for direction in ("RIGHT", "LEFT", "FRONT"):
            if be.unsafe:
                log("UNSAFE - skipping S3", direction); break
            phase(f"S3_point_{direction}")
            ts = time.monotonic() + 1.0
            be.point(direction, ts, hold_s=1.5)
            meta.setdefault("S3", {})[direction] = {"t_stroke": ts, "hold_s": 1.5,
                                                    "targets": be._point["target"].tolist() if be._point else None}
            # wait until the point track closes (prep 0.8 + hold + recov 1.65 + margin)
            t_end = ts + 1.5 + 1.65 + 0.5
            while time.monotonic() < t_end and be._point is not None:
                time.sleep(0.02)
            while time.monotonic() < t_end:
                time.sleep(0.02)
            end_phase()
            time.sleep(0.5)
        feed_on[0] = False; time.sleep(0.5)

        # ---------------- S4: EMOTES kinematic CSV through URS ----------------
        for mid, area, label in EMOTES:
            if be.unsafe:
                log("UNSAFE - skipping S4", label); break
            csv = be._csv_for(mid, area)
            log(f"S4 {label} csv:", "OK" if csv is not None else "MISSING",
                f"dur={getattr(csv,'duration',None)}")
            phase(f"S4_emote_{mid}_{label}")
            td = time.monotonic() + 0.8
            if os.environ.get("DRIVER_S4_CONTINUOUS", "1") == "1":
                feed_on[0] = True          # SONIC se sigue transmitiendo durante el emote
            be.play_emote(mid, area, td)
            t_end = time.monotonic() + 25.0
            while time.monotonic() < t_end and be._track is not None:
                time.sleep(0.02)
            end_phase()
            # transition back to SONIC: 2.5 s of stream, jerk measured on host
            phase(f"S4_sonic_after_{mid}")
            feed_on[0] = True
            time.sleep(2.5)
            feed_on[0] = False
            end_phase()
            time.sleep(0.3)

        # ---------------- S5: WALK -> STOP -> SETTLE -> POINT ----------------
        if not be.unsafe:
            phase("S5_walk")
            be.walk_to("short_fwd")
            time.sleep(0.1)
            tw = time.monotonic() + 1.0
            be.point("RIGHT", tw, hold_s=1.0)          # must be REFUSED (walking)
            be.stream_upper_body(time.monotonic(), READY_ARMS, np.zeros(3), np.zeros(2))  # refused too
            while be.walking() and time.monotonic() < tw + 8.0:
                time.sleep(0.02)
            t_walk_end = time.monotonic()
            end_phase()
            phase("S5_settle")
            t_stable = None
            while time.monotonic() - t_walk_end < 15.0:
                st = be.state()
                if st["stable"] and t_stable is None:
                    t_stable = time.monotonic()
                elif not st["stable"]:
                    t_stable = None
                if t_stable is not None and time.monotonic() - t_stable >= 1.0:
                    break
                time.sleep(0.02)
            meta["S5"] = {"walk_end": t_walk_end, "stable_after": t_stable,
                          "settle_s": (t_stable - t_walk_end) if t_stable else None}
            end_phase()
            phase("S5_point_after_walk")
            ts = time.monotonic() + 1.0
            be.point("FRONT", ts, hold_s=1.2)
            t_end = ts + 1.2 + 1.65 + 0.6
            while time.monotonic() < t_end:
                time.sleep(0.02)
            end_phase()
        else:
            log("UNSAFE - skipping S5")
    finally:
        _stop[0] = True
        time.sleep(0.2)
        be.stop()
        time.sleep(2.0)
        _stop[0] = True
        dump(be, rec, states, meta=meta, armed=True, meas_waist=meas_waist, meas_legs=meas_legs)
        be.close()
    log("final state:", be.state(), "unsafe:", be.unsafe)
    return 0


def dump(be, rec, states, meta=None, armed=True, meas_waist=None, meas_legs=None):
    up = rec.upper
    np.savez_compressed(
        f"{OUT}/trace.npz",
        up_t=np.array([r[0] for r in up]) if up else np.zeros(0),
        up_head=np.array([[r[1], r[2]] for r in up]) if up else np.zeros((0, 2)),
        up_arms=np.array([r[3] for r in up]) if up else np.zeros((0, 14)),
        up_meas=np.array([r[4] if r[4] is not None else [np.nan]*14 for r in up]) if up else np.zeros((0, 14)),
        meas_waist=np.array(meas_waist) if meas_waist else np.zeros((0, 4)),
        meas_legs=np.array(meas_legs) if meas_legs else np.zeros((0, 1)),
        vel=np.array(rec.vel) if rec.vel else np.zeros((0, 4)),
        bp=np.array([(t, *u) for t, u in rec.bp]) if rec.bp else np.zeros((0, 5)),
        states=np.array([(s[0], np.nan if s[1] is None else s[1], np.nan if s[2] is None else s[2],
                          1.0 if s[4] else 0.0, 1.0 if s[5] else 0.0, 1.0 if s[6] else 0.0,
                          s[7], 1.0 if s[8] else 0.0) for s in states]) if states else np.zeros((0, 8)),
    )
    with open(f"{OUT}/run.json", "w") as fh:
        json.dump({"phases": PHASES, "meta": meta or {}, "armed": armed,
                   "events": [{k: (v if isinstance(v, (int, float, str, bool, type(None))) else str(v))
                               for k, v in e.items()} for e in be.events],
                   "state_final": {k: str(v) for k, v in be.state().items()},
                   "unsafe": be.unsafe, "estopped": be.estopped,
                   "limiter_nlim": be.limiter.nlim}, fh, indent=1)
    log("dumped", f"{OUT}/trace.npz", f"{OUT}/run.json", "upper rows:", len(rec.upper))


if __name__ == "__main__":
    raise SystemExit(main())
