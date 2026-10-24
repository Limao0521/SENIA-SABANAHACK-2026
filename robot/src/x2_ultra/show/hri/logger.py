"""Registro a 50 Hz de una recepción HRI: estabilidad, seguimiento, reloj de audio y eventos.

Columnas (CSV, una fila por ciclo de control): tiempo de simulación, estado del paso, pelvis z,
tilt, |cmd| de locomoción, velocidad/jerk máximos de brazos (medidos), error comando−medida de los
brazos, delta de waist comandado y si había gestos activos. Eventos (JSONL): los del reproductor
(inicio de turno, audio_start con el reloj monotónico de pared, dispatch de preset, stroke de
señalamiento, supresiones por seguridad) y los del registro.

El resumen valida: 0 caídas (pelvis > 0,5 m, tilt < 20°), 0 gestos caminando, picos de velocidad y
jerk, y el error stroke−ancla por turno (stroke medido = pico de velocidad de brazos en la ventana
del gesto, como el detector de c12).
"""
from __future__ import annotations

import csv
import json
import math
import resource
from pathlib import Path

import mujoco
import numpy as np

from x2_ultra.robot import joints as J

FALL_Z = 0.5            # m: pelvis por debajo = caída (criterio de validación)
FALL_TILT_DEG = 20.0    # °
SPEED_LIMIT = 2.5       # rad/s: límite del rig (ARM_RATE); pico admisible con margen
JERK_LIMIT = 4000.0     # rad/s³: cota informativa (SONIC propio llega a ~3800 p99)
STROKE_FRAC = 0.9       # detector oficial c08/c12: primer cruce del 90 % del desplazamiento máx.
HEADER = ["t", "step", "turn_id", "pelvis_z", "tilt_deg", "cmd_vel_max", "arm_speed_max",
          "arm_jerk_max", "arm_cmd_err", "waist_delta_max", "gesture_active", "guest_dist",
          "stroke_dev"]


def detect_stroke_t(t: np.ndarray, dev: np.ndarray) -> float | None:
    """Instante del stroke según c08/c12: primera vez que el desplazamiento de la articulación
    dominante respecto a su postura de reposo alcanza el 90 % del máximo de la ventana. (El argmax
    de velocidad usado antes se adelanta cientos de ms en presets de rampa larga, p. ej.
    SOCIAL_HEART: el gesto sube despacio y la velocidad pico cae a mitad de la rampa.)"""
    dev = np.asarray(dev, dtype=float)
    ok = ~np.isnan(dev)
    if ok.sum() < 5:
        return None
    d, tt = dev[ok], np.asarray(t)[ok]
    peak = float(np.nanmax(d))
    if peak <= 0:
        return None
    hit = np.where(d >= STROKE_FRAC * peak)[0]
    return float(tt[hit[0]]) if len(hit) else None


class RunLogger:
    def __init__(self, out_dir: Path, name: str):
        self.dir = Path(out_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.name = name
        self.csv_path = self.dir / f"{name}_log.csv"
        self.events_path = self.dir / f"{name}_events.jsonl"
        self.summary_path = self.dir / f"{name}_summary.json"
        self._fh = self.csv_path.open("w", newline="")
        self._csv = csv.writer(self._fh)
        self._csv.writerow(HEADER)
        self._ev = self.events_path.open("w")
        self.events: list[dict] = []
        self._prev_qvel: np.ndarray | None = None
        self._arm_dof: np.ndarray | None = None
        self._probes: dict[str, tuple[str, float]] = {}     # turn_id → (articulación, reposo)
        self._probe_q: dict[str, tuple[int, float]] = {}    # turn_id → (qpos adr, reposo)
        self.rows = 0

    def set_probe(self, turn_id: str, joint: str, rest: float) -> None:
        """Sonda de stroke c12: desplazamiento de la articulación dominante respecto a reposo."""
        self._probes[turn_id] = (joint, float(rest))

    # ----------------------------------------------------------------------------- registro
    def event(self, **kw) -> None:
        self.events.append(kw)
        self._ev.write(json.dumps(kw, ensure_ascii=False, default=str) + "\n")
        self._ev.flush()

    def attach(self, model, rig) -> None:
        jid = lambda n: mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, rig.robot.prefix + n)
        self._arm_dof = np.array([model.jnt_dofadr[jid(n)] for n in J.ARM_JOINTS])
        for tid, (joint, rest) in self._probes.items():
            j = jid(joint)
            if j >= 0:
                self._probe_q[tid] = (int(model.jnt_qposadr[j]), rest)

    def row(self, t: float, engine, rig, step: str, turn_id: str, gesture_active: bool,
            guest_dist: float = math.nan) -> None:
        d = engine.data
        q = d.qpos[rig.qbase + 3:rig.qbase + 7]
        up_z = 1 - 2 * (q[1] ** 2 + q[2] ** 2)
        tilt = math.degrees(math.acos(max(-1.0, min(1.0, up_z))))
        qvel = d.qvel[self._arm_dof]
        speed = float(np.max(np.abs(qvel))) if qvel.size else 0.0
        if self._prev_qvel is not None and self._prev_qvel.shape == qvel.shape:
            jerk = float(np.max(np.abs(qvel - self._prev_qvel)) / 0.02)
        else:
            jerk = 0.0
        self._prev_qvel = qvel.copy()
        err = float(np.max(np.abs(rig.arm_cmd - d.qpos[rig.q_arms])))
        probe = self._probe_q.get(turn_id)
        dev = f"{abs(float(d.qpos[probe[0]]) - probe[1]):.4f}" if probe else ""
        self._csv.writerow([f"{t:.3f}", step, turn_id,
                            f"{d.qpos[rig.qbase + 2] - rig.floor_z:.4f}", f"{tilt:.2f}",
                            f"{float(np.max(np.abs(rig.cmd))):.3f}", f"{speed:.3f}", f"{jerk:.1f}",
                            f"{err:.4f}", f"{float(np.max(np.abs(rig.waist_delta))):.3f}",
                            int(gesture_active), f"{guest_dist:.2f}", dev])
        self.rows += 1

    def close(self) -> None:
        self._fh.close()
        self._ev.close()

    # ----------------------------------------------------------------------------- resumen
    def summarize(self, extra: dict | None = None) -> dict:
        rows = list(csv.DictReader(self.csv_path.open()))
        if not rows:
            return {"error": "sin filas"}
        z = np.array([float(r["pelvis_z"]) for r in rows])
        tilt = np.array([float(r["tilt_deg"]) for r in rows])
        speed = np.array([float(r["arm_speed_max"]) for r in rows])
        jerk = np.array([float(r["arm_jerk_max"]) for r in rows])
        err = np.array([float(r["arm_cmd_err"]) for r in rows])
        cmdv = np.array([float(r["cmd_vel_max"]) for r in rows])
        gest = np.array([int(r["gesture_active"]) for r in rows])
        t = np.array([float(r["t"]) for r in rows])
        falls = int(np.sum((z < FALL_Z) | (tilt > FALL_TILT_DEG)))
        violations = int(np.sum((gest == 1) & (cmdv > 0.05)))
        dev = np.array([float(r["stroke_dev"]) if r.get("stroke_dev") else math.nan
                        for r in rows])
        tid = np.array([r["turn_id"] for r in rows])
        stroke_errors = []
        for ev in self.events:
            if ev.get("event") != "preset_dispatch" and ev.get("event") != "point_stroke":
                continue
            expected = ev.get("stroke_expected_t")
            turn_id = ev.get("turn_id")
            if expected is None:
                continue
            lo, hi = float(ev["t_sim"]), float(ev.get("csv_end_t") or expected + 1.2)
            win = (t >= lo - 0.02) & (t <= hi + 0.05) & (tid == turn_id)
            measured = detect_stroke_t(t[win], dev[win]) if win.sum() >= 5 else None
            detector = "c12_pos90_dominant"
            if measured is None:               # respaldo: argmax de velocidad (detector viejo)
                w2 = (t >= expected - 1.2) & (t <= expected + 1.2)
                if w2.sum() < 5:
                    continue
                measured, detector = float(t[int(np.argmax(np.where(w2, speed, -1.0)))]), "speed_argmax"
            stroke_errors.append({"turn_id": turn_id, "emote": ev.get("emote"),
                                  "detector": detector,
                                  "expected_t": round(float(expected), 3),
                                  "measured_t": round(float(measured), 3),
                                  "error_ms": round(float((measured - expected) * 1000.0), 1)})
        summary = {
            "name": self.name,
            "seconds": round(float(t[-1]), 2),
            "falls": falls,
            "pelvis_z_min": round(float(z.min()), 4),
            "tilt_max_deg": round(float(tilt.max()), 2),
            "gesture_while_walking": violations,
            "safety_suppressions": sum(1 for e in self.events if e.get("event") == "safety_gesture_suppressed"),
            "arm_speed_peak": round(float(speed.max()), 3),
            "arm_jerk_peak": round(float(jerk.max()), 1),
            "arm_cmd_err_peak": round(float(err.max()), 4),
            "stroke_errors": stroke_errors,
            "stroke_abs_err_max_ms": max((abs(s["error_ms"]) for s in stroke_errors), default=None),
            "turns": [e for e in self.events if e.get("event") == "turn_start"],
            "audio_starts": [{k: e.get(k) for k in ("t_sim", "turn_id", "wav",
                                                    "audio_wall_monotonic")}
                             for e in self.events if e.get("event") == "audio_start"],
            "rss_peak_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0, 1),
            **(extra or {}),
        }
        self.summary_path.write_text(json.dumps(summary, indent=1, ensure_ascii=False, default=str))
        return summary
