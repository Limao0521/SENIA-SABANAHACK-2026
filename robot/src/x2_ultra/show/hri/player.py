"""Reproductor HRI a 50 Hz: un turno precomputado tras otro sobre un robot parado.

Por turno (``TurnPlan``), con el reloj del turno τ = t − t0:
- la base es el clip SONIC (brazos 14 + waist delta acotado + cabeza), cuyo cuadro 100 (τ = 2,0 s)
  es el t0 del audio: el WAV arranca ahí (reloj de audio = tiempo de simulación; en directo,
  además, se lanza ``pw-play`` y se registra el instante monotónico);
- turnos con preset (GREETING_*, APPLAUSE, SOCIAL_HEART, ...): reproducción cinemática del CSV de
  AgiBot colocada para que el *stroke* caiga sobre el ancla CTC (dispatch = ancla − offset de
  stroke medido en c08; si no cabe, se retrasa el audio hasta 1,5 s como en la política del
  anchor_plan). Transición V2 de c12: desvanecido min-jerk 0,6 s de SONIC a D, preset, y
  desvanecido 0,7 s de vuelta a SONIC (o al reposo si ya terminó el audio);
- turnos POINT: overlay de señalamiento sobre la base (``pointing.py``);
- turnos NONE: solo SONIC.

Interlock de seguridad (no negociable): ``tick`` no envía gestos si el robot no está parado
(|cmd| ≥ CMD_EPS o dance activo); la violación se registra y los brazos quedan en reposo. Entre
turnos hay una mezcla min-jerk de ``BLEND_S`` desde el último comando, sin saltos.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

import numpy as np

from x2_ultra.show.hri.data import TurnData, VisitStore
from x2_ultra.show.hri.motion import (AUDIO_LEADIN, BLEND_S, D_ARMS, FADE_IN_S, FADE_OUT_S, READY_ARMS,
                                      REST_ARMS, SONIC_ARM_AMP_K, TRACK_LAG_S, WAIST_LIMIT, CsvPreset,
                                      SonicClip, fade)
from x2_ultra.show.hri.pointing import PointOverlay

CMD_EPS = 0.05          # m/s | rad/s: por encima, el robot NO está parado → nada de gestos
MAX_AUDIO_SHIFT = 1.5   # s: retardo máximo del audio para que quepa el preset (política anchor_plan)
ANCHOR_FALLBACK_FRAC = 0.35    # sin ancla CTC: stroke al 35 % del audio (aprox. al punto medio)


@dataclass
class TurnPlan:
    turn: TurnData
    sonic: SonicClip | None
    preset: CsvPreset | None
    point: PointOverlay | None
    audio_start: float          # s relativos a t0 del turno
    csv_start: float
    csv_end: float
    end: float                  # duración total del turno
    stroke_at: float | None     # instante (relativo) en que debe caer el stroke
    t0: float = math.nan        # tiempo de simulación absoluto (lo pone el reproductor)
    fired: dict = field(default_factory=dict)

    @property
    def audio_abs(self) -> float:
        return self.t0 + self.audio_start


def plan_turn(turn: TurnData, store: VisitStore) -> TurnPlan:
    sonic = SonicClip.load(turn.sonic) if turn.sonic is not None else None
    audio_start = AUDIO_LEADIN if sonic is not None else 0.4
    base_dur = sonic.duration if sonic is not None else turn.duration_s + audio_start + 0.5
    anchor = turn.anchor_start_s
    if anchor is None:
        anchor = ANCHOR_FALLBACK_FRAC * turn.duration_s
    preset = point = None
    csv_start = csv_end = 0.0
    stroke_at = None
    if not turn.is_point:
        csv_path = store.csv_for(turn.emote)
        if csv_path is not None and csv_path.is_file():
            preset = CsvPreset.load(csv_path)
            offset = store.stroke_offset(turn.emote)
            csv_start = audio_start + anchor - offset - TRACK_LAG_S   # compensa el retardo del PD
            if csv_start < FADE_OUT_S + 0.05:      # no cabe el desvanecido: retrasar el audio
                shift = min(MAX_AUDIO_SHIFT, FADE_OUT_S + 0.05 - csv_start)
                audio_start += shift
                csv_start += shift
            csv_end = csv_start + preset.duration
            stroke_at = csv_start + offset + TRACK_LAG_S
    else:
        point = PointOverlay(turn.direction, audio_start + anchor, store.point_targets())
        stroke_at = point.ts
        csv_start = point.start()
        csv_end = point.t_end
    gesture_end = 0.0
    if preset is not None:
        gesture_end = csv_end + FADE_IN_S
    elif point is not None:
        gesture_end = csv_end + 0.2
    end = max(base_dur, audio_start + turn.duration_s + 0.2, gesture_end)
    return TurnPlan(turn=turn, sonic=sonic, preset=preset, point=point, audio_start=audio_start,
                    csv_start=csv_start, csv_end=csv_end, end=end, stroke_at=stroke_at)


class HriPlayer:
    """Cola de turnos para un robot: ``tick(t, rig)`` en cada ciclo de control (50 Hz)."""

    def __init__(self, key: str, plans: list[TurnPlan], audio_fn=None, log_event=None):
        self.key = key
        self.plans = plans
        self.audio_fn = audio_fn            # audio_fn(wav_path) -> reloj monotónico de inicio
        self._log = log_event or (lambda **kw: None)
        self.index = -1
        self.t0 = math.nan                  # inicio de la secuencia (primer tick parado)
        self.done = False
        self.status = ""
        self.blend_from: np.ndarray | None = None
        self.blend_t0 = math.nan
        self._last_arms: np.ndarray | None = None
        self.violations = 0
        self.events: list[dict] = []
        self.current: TurnPlan | None = None

    # ----------------------------------------------------------------------------- utilidades
    def log(self, event: str, t: float, **kw) -> None:
        rec = {"event": event, "t_sim": round(float(t), 4), "wall": time.monotonic(), **kw}
        self.events.append(rec)
        self._log(**rec)

    @staticmethod
    def standing(rig) -> bool:
        return (rig.dance is None and float(np.max(np.abs(rig.cmd))) < CMD_EPS
                and float(np.max(np.abs(rig.cmd_target))) < CMD_EPS)

    # ----------------------------------------------------------------------------- stream
    def _base(self, plan: TurnPlan, tau: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        if plan.sonic is not None:
            arms = plan.sonic.arms(tau)
            if plan.point is None:   # fix legs2: amplitud k sobre el reposo (pointing va a plena)
                arms = REST_ARMS + SONIC_ARM_AMP_K * (arms - REST_ARMS)
            return arms, plan.sonic.waist(tau), plan.sonic.head(tau)
        return READY_ARMS.copy(), np.zeros(3), np.zeros(2)

    def _stream(self, plan: TurnPlan, tau: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """(arms14, waist3 delta, head2) de un turno en su tiempo relativo ``tau``."""
        arms, waist, head = self._base(plan, tau)
        audio_left = plan.audio_start + plan.turn.duration_s - tau
        if plan.preset is not None:
            csv = plan.preset
            if plan.csv_start - FADE_OUT_S <= tau < plan.csv_start:      # SONIC → D (c12 V2)
                w = fade(tau, plan.csv_start - FADE_OUT_S, FADE_OUT_S)
                arms = D_ARMS + (1.0 - w) * (arms - D_ARMS)
                waist, head = (1.0 - w) * waist, (1.0 - w) * head
            elif plan.csv_start <= tau < plan.csv_end:                   # preset cinemático
                arms, w3, h2 = csv.sample(tau - plan.csv_start)
                waist = np.clip(w3, -WAIST_LIMIT, WAIST_LIMIT) if w3.size == 3 else waist * 0
                head = h2 if h2.size == 2 else head * 0
            elif plan.csv_end <= tau < plan.csv_end + FADE_IN_S:         # preset → SONIC/reposo
                w = fade(tau, plan.csv_end, FADE_IN_S)
                last_a, last_w, last_h = csv.sample(csv.duration)
                if plan.sonic is not None and tau < plan.sonic.duration and audio_left > FADE_IN_S:
                    tgt_a, tgt_w, tgt_h = arms, waist, head
                else:
                    tgt_a, tgt_w, tgt_h = REST_ARMS, np.zeros(3), np.zeros(2)
                arms = tgt_a + (1.0 - w) * (last_a - tgt_a)
                waist = tgt_w + (1.0 - w) * (np.clip(last_w, -WAIST_LIMIT, WAIST_LIMIT) - tgt_w)
                head = tgt_h + (1.0 - w) * ((last_h if last_h.size == 2 else tgt_h) - tgt_h)
        elif plan.point is not None:
            arms, head = plan.point.apply(tau, arms, head)
        overlay = ((plan.preset is not None and plan.csv_start - FADE_OUT_S <= tau
                    < plan.csv_end + FADE_IN_S)
                   or (plan.point is not None and plan.point.start() <= tau <= plan.point.t_end))
        if plan.sonic is not None and tau > plan.sonic.duration and not overlay:
            w = fade(tau, plan.sonic.duration, BLEND_S)                  # cola: al reposo del show
            arms = REST_ARMS + (1.0 - w) * (arms - REST_ARMS)
            waist, head = (1.0 - w) * waist, (1.0 - w) * head
        return arms, waist, head

    # ----------------------------------------------------------------------------- ciclo 50 Hz
    def tick(self, t: float, rig) -> bool:
        """Avanza el reproductor; devuelve True mientras la secuencia sigue activa."""
        if self.done:
            return False
        if math.isnan(self.t0):
            if not self.standing(rig):        # la secuencia solo arranca con el robot parado
                return True
            self.t0 = t
            self.log("hri_sequence_start", t, turn_count=len(self.plans))
        if not self.standing(rig):            # interlock: nunca gestos caminando
            self.violations += 1
            self.log("safety_gesture_suppressed", t, cmd=[float(c) for c in rig.cmd])
            rig.set_waist_gate(False)
            rig.set_waist_gentle(False)
            rig.set_arm_targets(REST_ARMS)
            rig.set_waist_delta(np.zeros(3))
            self._last_arms = np.asarray(rig.arm_cmd, dtype=float).copy()
            return True
        # avanzar de turno
        while True:
            if self.index >= len(self.plans):
                break
            if self.index < 0:
                self._enter(t, rig)
                break
            plan = self.plans[self.index]
            if t - plan.t0 >= plan.end:
                self.log("turn_end", t, turn_id=plan.turn.turn_id, emote=plan.turn.emote)
                self.index += 1
                if self.index < len(self.plans):
                    self._enter(t, rig)
                break
            break
        if self.index >= len(self.plans):
            # fin: brazos al reposo con mezcla, y listo para caminar
            rig.set_waist_gate(False)         # g(t) 1→0 (min-jerk 0,4 s) al cerrar la secuencia
            w = fade(t, self.plans[-1].t0 + self.plans[-1].end, BLEND_S) if self.plans else 1.0
            src = self._last_arms if self._last_arms is not None else REST_ARMS
            rig.set_arm_targets(REST_ARMS + (1.0 - w) * (src - REST_ARMS))
            self._last_arms = np.asarray(rig.arm_cmd, dtype=float).copy()
            rig.set_waist_delta(np.zeros(3))
            rig.set_head_targets(0.0, 0.0)
            if w >= 1.0:
                self.done = True
                self.status = "secuencia HRI terminada"
                self.log("hri_sequence_end", t, violations=self.violations)
            return not self.done
        plan = self.plans[self.index]
        self.current = plan
        tau = t - plan.t0
        self._fire_audio(t, plan)
        self._fire_marks(t, plan, tau)
        arms, waist, head = self._stream(plan, tau)
        if self.blend_from is not None and t - self.blend_t0 < BLEND_S:   # mezcla entre turnos
            w = fade(t, self.blend_t0, BLEND_S)
            arms = arms + (1.0 - w) * (self.blend_from - arms)
            waist, head = w * waist, w * head
        # preset cinemático (p. ej. venia VIP): canal de waist en modo gentle (cap 0,10 + slew 0,06)
        rig.set_waist_gentle(plan.preset is not None
                             and plan.csv_start - FADE_OUT_S <= tau < plan.csv_end + FADE_IN_S)
        rig.set_arm_targets(arms)
        rig.set_waist_delta(waist)
        rig.set_head_targets(float(head[0]), float(head[1]))
        self._last_arms = np.asarray(rig.arm_cmd, dtype=float).copy()
        self.status = f"«{plan.turn.speech}»"
        return True

    def _enter(self, t: float, rig) -> None:
        if self.index < 0:
            self.index = 0
        plan = self.plans[self.index]
        plan.t0 = t
        plan.fired = {}
        rig.set_waist_gate(True)              # g(t) 0→1 (min-jerk 0,4 s) al abrir el turno
        # mezclar desde el último comando real (evita saltos al cambiar de turno)
        self.blend_from = self._last_arms
        self.blend_t0 = t
        self.log("turn_start", t, turn_id=plan.turn.turn_id, state=plan.turn.state,
                 emote=plan.turn.emote, speech=plan.turn.speech, borrowed=plan.turn.borrowed)

    def _fire_audio(self, t: float, plan: TurnPlan) -> None:
        if "audio" in plan.fired or t < plan.audio_abs:
            return
        wall = None
        if self.audio_fn is not None:
            wall = self.audio_fn(plan.turn.wav)
        plan.fired["audio"] = t
        self.log("audio_start", t, turn_id=plan.turn.turn_id, wav=str(plan.turn.wav),
                 audio_wall_monotonic=wall, scheduled_t=plan.audio_abs)

    def _fire_marks(self, t: float, plan: TurnPlan, tau: float) -> None:
        if plan.preset is not None and "dispatch" not in plan.fired and tau >= plan.csv_start:
            plan.fired["dispatch"] = t
            self.log("preset_dispatch", t, turn_id=plan.turn.turn_id, emote=plan.turn.emote,
                     stroke_expected_t=plan.t0 + (plan.stroke_at or 0.0),
                     csv_end_t=plan.t0 + plan.csv_end)
        if plan.point is not None and "stroke" not in plan.fired and tau >= plan.point.ts:
            plan.fired["stroke"] = t
            self.log("point_stroke", t, turn_id=plan.turn.turn_id, direction=plan.point.direction)

    def abort_to_rest(self, rig) -> None:
        self.done = True
        rig.set_waist_gate(False)
        rig.set_waist_gentle(False)
        rig.set_arm_targets(REST_ARMS)
        rig.set_waist_delta(np.zeros(3))
