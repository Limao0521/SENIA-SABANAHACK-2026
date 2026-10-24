"""ShowBackend: el motor del show del reto 2 (``show.engine.ShowEngine`` + reproductor HRI) como Backend.

Envoltorio fino y probado sobre lo que YA existe y está validado 6/6 (docs/reto-2/README_HRI.md):

- ``stream_upper_body``: cola a 50 Hz (el último cuadro manda, como un stream real); la bomba
  aplica al ``RobotRig`` del show, que ya pone los límites (``clamp_arms``, ``WaistFilter`` con
  bounds ±0.10/0.05/0.03 + slew, recorte de cabeza a ``JOINT_RANGE`` y rate-limit ARM_RATE);
- ``play_emote``: reproductor cinemático del CSV de AgiBot (``motion.CsvPreset``), colocado para
  que el dispatch caiga en ``t_dispatch``: mezcla min-jerk 0.6 s a la postura D, preset, mezcla
  0.7 s de vuelta al reposo del show (patrón V2 de c12, mismo que ``player.py``);
- ``point``: overlay de señalamiento (``pointing.PointOverlay``, stroke en ``t_stroke``, hold
  ``hold_s`` ≤ 2 s, prep 0.8 s / recov 1.65 s);
- ``walk_to``: tour ``GuideTour`` con ``Walk`` + ``Face`` del show (rutas por área inyectables);
  mientras el tour camina, los gestos quedan bloqueados (interlock de robot parado, no negociable);
- ``dispatch_d1``: stub — registro + marcador en pantalla (``engine.say``: subtítulo de la ventana).

Interlock de seguridad (idéntico a ``player.HriPlayer``): nada de gestos si el robot no está
parado (|cmd| ≥ CMD_EPS, |cmd_target| ≥ CMD_EPS o tour/baile activo); la violación se registra
(``safety_gesture_suppressed``) y los brazos quedan en reposo.
"""
from __future__ import annotations

import math
import threading
import time
from collections import deque
from dataclasses import dataclass

import numpy as np

from x2_ultra.hri_runtime.backends.base import TICK
from x2_ultra.robot import joints as J
from x2_ultra.show.guide import Face, GuideTour, Walk
from x2_ultra.show.hri.motion import (D_ARMS, FADE_IN_S, FADE_OUT_S, READY_ARMS, REST_ARMS,
                                      WAIST_LIMIT, CsvPreset, fade, min_jerk)
from x2_ultra.show.hri.player import CMD_EPS
from x2_ultra.show.hri.pointing import LEAD_S, PREP_S, RECOV_S, PointOverlay

HEAD_SIGN_OK = ("LEFT", "RIGHT", "FRONT")


class _HoldOverlay(PointOverlay):
    """PointOverlay con ``hold_s`` configurable (1.0–2.0 s; el weight() original usa constantes)."""

    def __init__(self, direction: str, anchor_t: float, targets: dict, hold_s: float = 1.5):
        super().__init__(direction, anchor_t, targets)
        self.hold_s = float(np.clip(hold_s, 1.0, 2.0))     # ≤ 2 s por diseño
        self.t_end = self.ts + self.hold_s + RECOV_S

    def weight(self, t: float) -> float:
        if t < self.ts - PREP_S or t > self.t_end:
            return 0.0
        if t < self.ts:
            return min_jerk((t - (self.ts - PREP_S)) / PREP_S)
        if t <= self.ts + self.hold_s:
            return 1.0
        return 1.0 - min_jerk((t - self.ts - self.hold_s) / RECOV_S)


@dataclass
class _EmoteTrack:
    """Reproducción cinemática de un CSV con dispatch en ``t_dispatch`` (reloj de simulación)."""
    csv: CsvPreset
    t_dispatch: float
    motion_id: int
    area: int
    t_enter: float                 # sim-time en que la bomba lo empezó
    from_arms: np.ndarray          # brazos al entrar (para la mezcla a D)

    def t_end(self) -> float:
        return self.t_dispatch + self.csv.duration + FADE_IN_S

    def sample(self, t: float):
        """(arms14, waist3, head2, gentle) en tiempo de simulación absoluto."""
        if t < self.t_dispatch:                       # mezcla min-jerk hacia D (0.6 s antes)
            w = fade(t, self.t_dispatch - FADE_OUT_S, FADE_OUT_S)
            arms = D_ARMS + (1.0 - w) * (self.from_arms - D_ARMS)
            return arms, np.zeros(3), np.zeros(2), False
        tau = t - self.t_dispatch
        if tau < self.csv.duration:                   # preset cinemático
            arms, w3, h2 = self.csv.sample(tau)
            waist = np.clip(w3, -WAIST_LIMIT, WAIST_LIMIT) if w3.size == 3 else np.zeros(3)
            head = h2 if h2.size == 2 else np.zeros(2)
            return arms, waist, head, True
        w = fade(t, self.t_dispatch + self.csv.duration, FADE_IN_S)   # vuelta al reposo del show
        last_a, last_w, last_h = self.csv.sample(self.csv.duration)
        arms = REST_ARMS + (1.0 - w) * (last_a - REST_ARMS)
        waist = (1.0 - w) * (np.clip(last_w, -WAIST_LIMIT, WAIST_LIMIT) if last_w.size == 3
                             else np.zeros(3))
        head = (1.0 - w) * (last_h if last_h.size == 2 else np.zeros(2))
        return arms, waist, head, w < 1.0


class ShowBackend:
    """Backend sobre el ``ShowEngine`` del reto 2 (rig ``key``, por defecto el Ultra)."""

    def __init__(self, engine, key: str = "ultra", store=None, routes: dict | None = None,
                 d1_marker=None, pump: bool = True):
        self.engine = engine
        self.key = key
        self.store = store                    # VisitStore (o fake) para CSV y point_targets
        self.routes = dict(routes or {})      # área → (puntos [(x, y), ...], yaw final)
        self.d1_marker = d1_marker            # callable(area, t) opcional; si no, engine.say
        self.d1_log: list[dict] = []          # registro del stub de D1
        self.events: list[dict] = []
        self.estopped = False
        self._lock = threading.Lock()
        self._q: deque = deque(maxlen=2)      # cola 50 Hz: el último cuadro manda
        self._track = None                    # _EmoteTrack | PointOverlay
        self._track_kind = ""
        self._streaming = False
        self._thread: threading.Thread | None = None
        if pump:
            self.start()

    # ----------------------------------------------------------------------------- registro
    def log(self, event: str, **kw) -> None:
        rec = {"event": event, "t": time.monotonic(), "t_sim": self._t(), **kw}
        self.events.append(rec)
        return None

    def _t(self) -> float:
        try:
            return float(self.engine.sim_time)
        except Exception:
            return float("nan")

    @property
    def rig(self):
        return self.engine.rigs[self.key]

    # ----------------------------------------------------------------------------- Protocolo
    def state(self) -> dict:
        rig = self.rig
        t = self._t()
        q = np.asarray(rig.quat(self.engine.data), dtype=float)
        up_z = 1.0 - 2.0 * (q[1] ** 2 + q[2] ** 2)
        tilt = math.degrees(math.acos(float(np.clip(up_z, -1.0, 1.0))))
        walking = self.walking()
        fall = str(rig.state) != "activo"
        if self.estopped:
            mode = "estop"
        elif fall:
            mode = "fall"
        elif walking:
            mode = "walking"
        else:
            mode = self._track_kind or ("stream" if self._streaming else "idle")
        return {"t": t, "pelvis_z": float(np.asarray(rig.info.base_pos)[2]), "tilt_deg": tilt,
                "mode": mode, "stable": not (fall or self.estopped or walking),
                "walking": bool(walking), "fall": bool(fall)}

    def stream_upper_body(self, t: float, arms14, waist3, head2) -> None:
        if self.estopped:
            self.log("refused", what="stream_upper_body", reason="estop")
            return
        arms = self._vec(arms14, 14)
        waist = self._vec(waist3, 3)
        head = self._vec(head2, 2)
        if arms is None or waist is None or head is None:
            self.log("stream_rejected", reason="malformed", t_cmd=float(t))
            return
        with self._lock:
            self._q.append((float(t), arms, waist, head))
            self._streaming = True

    def play_emote(self, motion_id: int, area: int, t_dispatch: float) -> None:
        if self._refuse("play_emote"):
            return
        csv = self._csv_for(motion_id, area)
        if csv is None:
            self.log("emote_unavailable", motion_id=int(motion_id), area=int(area))
            return
        with self._lock:
            self._track = _EmoteTrack(csv=csv, t_dispatch=float(t_dispatch), motion_id=int(motion_id),
                                      area=int(area), t_enter=self._t(),
                                      from_arms=np.asarray(self.rig.arm_cmd, dtype=float).copy())
            self._track_kind = "emote"
            self._q.clear()
        self.log("emote_dispatch", motion_id=int(motion_id), area=int(area), t_dispatch=float(t_dispatch),
                 path="kinematic_csv")

    def point(self, direction: str, t_stroke: float, hold_s: float = 1.5) -> None:
        if self._refuse("point"):
            return
        direction = str(direction).upper()
        if direction not in HEAD_SIGN_OK:
            self.log("point_rejected", direction=direction, reason="dirección desconocida")
            return
        targets = self._point_targets()
        if not targets:
            self.log("point_unavailable", direction=direction)
            return
        ov = _HoldOverlay(direction, float(t_stroke) + LEAD_S, targets, hold_s)   # ts == t_stroke
        with self._lock:
            self._track, self._track_kind = ov, "point"
            self._q.clear()
        self.log("point_dispatch", direction=direction, t_stroke=float(t_stroke), hold_s=hold_s)

    def walk_to(self, area: str) -> None:
        if self._refuse("walk_to"):
            return
        route = self.routes.get(area)
        if route is None:
            self.log("walk_no_route", area=area)
            return
        points, yaw = route
        with self._lock:                       # orden no negociable: gestos → reposo → caminar
            self._abort_track("walk_to")
        self.rig.set_arm_targets(REST_ARMS)
        self.rig.set_waist_delta(np.zeros(3))
        self.rig.set_waist_gate(False)
        tour = GuideTour(self.key, [Walk(list(points), f"walk_to {area}"), Face(float(yaw))],
                         label=f"walk_to {area}")
        self.engine.start_tour(tour)
        self.log("walk_start", area=area, waypoints=len(points))

    def set_velocity(self, vx: float, wz: float) -> None:
        if self._refuse("set_velocity"):
            return
        with self._lock:
            if self._track is not None:        # nunca locomoción durante un gesto
                self.log("velocity_refused", reason="gesture_active", kind=self._track_kind)
                return
        self.rig.set_velocity_target(float(vx), 0.0, float(wz))
        self.log("set_velocity", vx=float(vx), wz=float(wz))

    def dispatch_d1(self, area: str) -> None:
        """Stub: la API del D1 es desconocida — registro + marcador en pantalla."""
        rec = {"t": time.monotonic(), "t_sim": self._t(), "area": str(area)}
        self.d1_log.append(rec)
        if self.d1_marker is not None:
            self.d1_marker(str(area), rec["t"])
        else:
            try:
                self.engine.say(f"Draco, por favor guía a {area}", self.key)
            except Exception:
                pass
        self.log("dispatch_d1", area=str(area), stub=True)

    def stop(self) -> None:
        with self._lock:
            self._abort_track("stop")
            self._q.clear()
            self._streaming = False
        self.rig.stop_walking()
        try:
            self.engine.stop_tour()
        except Exception:
            pass
        self.log("stop")

    def estop(self) -> None:
        self.stop()
        self.estopped = True
        self.rig.set_arm_targets(REST_ARMS)
        self.rig.set_waist_delta(np.zeros(3))
        self.rig.set_head_targets(0.0, 0.0)
        self.log("estop")

    # ----------------------------------------------------------------------------- bomba 50 Hz
    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, name="hri-show-backend", daemon=True)
            self._thread.start()

    def close(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None

    _running = True

    def _loop(self) -> None:
        nxt = time.monotonic()
        while self._running:
            self.pump_once()
            nxt += TICK
            delay = nxt - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            elif delay < -0.25:
                nxt = time.monotonic()

    def pump_once(self, t: float | None = None) -> None:
        """Un ciclo de 50 Hz: aplica la pista activa o el último cuadro del stream al rig."""
        t = self._t() if t is None else float(t)
        if self.estopped:
            return
        with self.engine.lock:
            rig = self.rig
            if not self._standing(rig):        # interlock: nunca gestos caminando
                with self._lock:
                    had = self._track is not None or self._streaming
                    self._abort_track("walking")
                    self._q.clear()
                    self._streaming = False
                if had:
                    self.log("safety_gesture_suppressed", cmd=[float(c) for c in rig.cmd])
                    rig.set_arm_targets(REST_ARMS)
                    rig.set_waist_delta(np.zeros(3))
                    rig.set_waist_gate(False)
                return
            with self._lock:
                track, kind = self._track, self._track_kind
            if isinstance(track, _EmoteTrack):
                if t >= track.t_end():
                    self._finish_track(t)
                    return
                arms, waist, head, gentle = track.sample(t)
                rig.set_waist_gentle(gentle)
                rig.set_waist_gate(True)
                rig.set_arm_targets(arms)
                rig.set_waist_delta(waist)
                rig.set_head_targets(float(head[0]), float(head[1]))
                return
            if isinstance(track, PointOverlay):
                if t > track.t_end:
                    self._finish_track(t)
                    return
                arms, head = track.apply(t, REST_ARMS.copy(), np.zeros(2))
                rig.set_waist_gentle(False)
                rig.set_waist_gate(True)
                rig.set_arm_targets(arms)
                rig.set_waist_delta(np.zeros(3))
                rig.set_head_targets(float(head[0]), float(head[1]))
                return
            item = None
            with self._lock:
                if self._q:
                    item = self._q.pop()
            if item is not None:
                _, arms, waist, head = item
                rig.set_waist_gentle(False)
                rig.set_waist_gate(True)
                rig.set_arm_targets(arms)      # el rig aplica clamp_arms + rate ARM_RATE
                rig.set_waist_delta(waist)     # WaistFilter del engine: bounds + low-pass + slew
                rig.set_head_targets(float(head[0]), float(head[1]))
            elif self._streaming:              # stream terminado: cerrar la ganancia de waist
                self._streaming = False
                rig.set_waist_gate(False)

    # ----------------------------------------------------------------------------- utilidades
    def walking(self) -> bool:
        rig = self.rig
        try:
            touring = bool(self.engine.touring(self.key))
        except Exception:
            touring = False
        return (touring or float(np.max(np.abs(rig.cmd))) >= CMD_EPS
                or float(np.max(np.abs(rig.cmd_target))) >= CMD_EPS)

    @staticmethod
    def _standing(rig) -> bool:
        return (rig.dance is None and float(np.max(np.abs(rig.cmd))) < CMD_EPS
                and float(np.max(np.abs(rig.cmd_target))) < CMD_EPS)

    def _refuse(self, what: str) -> bool:
        if self.estopped:
            self.log("refused", what=what, reason="estop")
            return True
        if self.walking():
            self.log("refused", what=what, reason="walking")
            return True
        return False

    def _abort_track(self, why: str) -> None:
        if self._track is not None:
            self.log("track_aborted", kind=self._track_kind, why=why)
        self._track = None
        self._track_kind = ""

    def _finish_track(self, t: float) -> None:
        kind = self._track_kind
        self._abort_track("done")
        rig = self.rig
        rig.set_waist_gentle(False)
        rig.set_waist_gate(False)
        rig.set_arm_targets(REST_ARMS)
        rig.set_waist_delta(np.zeros(3))
        rig.set_head_targets(0.0, 0.0)
        self.log("track_done", kind=kind, t=t)

    @staticmethod
    def _vec(v, n: int) -> np.ndarray | None:
        a = np.asarray(v, dtype=float).reshape(-1)
        if a.size != n or not np.all(np.isfinite(a)):
            return None
        return a

    def _csv_for(self, motion_id: int, area: int) -> CsvPreset | None:
        if self.store is None:
            return None
        path = None
        try:
            path = self.store.csv_for_id(int(motion_id), int(area))
        except AttributeError:
            anims = getattr(self.store, "animations", {})
            path = anims.get((int(motion_id), int(area)))
        except Exception:
            path = None
        if path is None:
            return None
        from pathlib import Path
        path = Path(path)
        return CsvPreset.load(path) if path.is_file() else None

    def _point_targets(self) -> dict:
        if self.store is None:
            return {}
        try:
            return self.store.point_targets()
        except Exception:
            return {}
