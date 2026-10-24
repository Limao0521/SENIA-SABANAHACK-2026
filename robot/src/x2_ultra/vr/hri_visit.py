"""La recepción HRI pregrabada del reto 2 (``show.hri``) con el visitante de las gafas.

Quién es el visitante lo dice el check-in de Face (``person_id`` P001…P005 = las personas de
``assets/hri/identities.json``); sin check-in o con una cara desconocida se usa la visita «Desconocido». Su
visita son 6 turnos ya generados (voz Supertonic + gestos SONIC/preset/señalamiento): saludo, guía, tres
diálogos y despedida. Cada diálogo responde a una frase concreta del visitante, así que antes de cada uno el
robot **espera a que el visitante la diga** (``VoiceGate``): las gafas le muestran la frase y el turno sigue
cuando termina de hablar (o suelta el gatillo, o el operador pulsa «El visitante ya habló»).

El recorrido es el de ``show.hri.tour.hri_reception`` sin la llegada automática del huésped (aquí anda la
persona con las gafas): esperar a que llegue al mostrador → reconocimiento → punto de recepción → parar y
asentarse → turnos → acompañarlo por su ruta → volver.
"""
from __future__ import annotations

import json
import math
import os
import unicodedata
from pathlib import Path

import numpy as np

from x2_ultra.show.guide import CATEGORIES, Pause, WaitForGuest, escort_steps
from x2_ultra.show.hri.data import VisitStore, load_identities, use_person_visits
from x2_ultra.show.hri.llm_script import RANK_CATEGORY
from x2_ultra.show.hri.player import REST_ARMS, HriPlayer, plan_turn
from x2_ultra.show.hri.tour import HriReceptionTour, HriTurns, Settle

UNKNOWN = "Desconocido"
DEFAULT_FLOOR = 3


def _fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", str(text)) if unicodedata.category(c) != "Mn").casefold()


def person_for(profile: dict | None) -> str:
    """Persona de ``identities.json`` para un perfil de Face (por ``person_id`` o por nombre)."""
    people = load_identities()
    if profile:
        for name, entry in people.items():
            if entry.get("person_id") and entry["person_id"] == profile.get("person_id"):
                return name
        wanted = _fold(profile.get("display_name") or "")
        for name in people:
            if wanted and _fold(name) == wanted:
                return name
    return UNKNOWN


class VoiceGate:
    """Sabe cuándo el visitante ha dicho su frase, con el micrófono de las gafas: al menos MIN_SPEECH s de voz y
    luego SILENCE s de silencio, o el gatillo pulsado y soltado. Solo escucha mientras está armada (el robot ya
    calló), para no confundir su propia voz con la del visitante."""
    LEVEL = 0.06            # pico de una trama de 20 ms para contarla como voz (sin AGC: hablar da 0,2-0,6)
    MIN_SPEECH = 0.4        # s de voz
    SILENCE = 0.8           # s de silencio al final
    FRAME = 0.02

    def __init__(self):
        self.armed = False
        self.opened = False
        self._speech = 0.0
        self._silence = 0.0
        self._ptt_down = False
        self._ptt_used = False

    def arm(self) -> None:
        self.armed, self.opened = True, False
        self._speech = self._silence = 0.0
        self._ptt_used = False

    def force(self) -> None:
        self.opened = True

    def feed(self, pcm: bytes, ptt: bool) -> None:
        if not self.armed or self.opened:
            self._ptt_down = ptt
            return
        if ptt:
            self._ptt_used = True
        elif self._ptt_down and self._ptt_used:          # soltó el gatillo tras hablar
            self.opened = True
        self._ptt_down = ptt
        samples = np.frombuffer(pcm, dtype=np.int16)
        peak = float(np.abs(samples).max()) / 32768 if samples.size else 0.0
        if peak >= self.LEVEL:
            self._speech += self.FRAME
            self._silence = 0.0
        else:
            self._silence += self.FRAME
            if self._speech >= self.MIN_SPEECH and self._silence >= self.SILENCE and not self._ptt_down:
                self.opened = True


class GatedPlayer(HriPlayer):
    """``HriPlayer`` que antes de cada DIALOGUE espera, con los brazos en reposo, a que ``gate`` se abra."""

    def __init__(self, key, plans, gate: VoiceGate, utterances: dict, on_wait=None, **kw):
        super().__init__(key, plans, **kw)
        self.gate = gate
        self.utterances = utterances
        self.on_wait = on_wait or (lambda text: None)
        self.holding = False
        self._passed: set[int] = set()

    def _next_is_dialogue(self, t: float) -> bool:
        """El turno actual acaba en este ciclo y el siguiente es un diálogo que aún espera al visitante."""
        if math.isnan(self.t0) or not 0 <= self.index < len(self.plans) - 1:
            return False
        plan, nxt = self.plans[self.index], self.plans[self.index + 1]
        return (t - plan.t0 >= plan.end and nxt.turn.state == "DIALOGUE"
                and self.index + 1 not in self._passed)

    def tick(self, t: float, rig) -> bool:
        # La espera se decide ANTES de que HriPlayer.tick pase de turno: si no, en ese mismo ciclo calcularía los
        # brazos de un turno sin empezar (t0 = NaN) y la simulación se vuelve inestable.
        if not self.holding and not self.done and self.standing(rig) and self._next_is_dialogue(t):
            plan = self.plans[self.index]
            self.log("turn_end", t, turn_id=plan.turn.turn_id, emote=plan.turn.emote)
            self.index += 1
            self.holding = True
            self.gate.arm()
            text = self.utterances.get(self.plans[self.index].turn.turn_id) or ""
            self.status = f"esperando al visitante: «{text}»"
            self.log("wait_visitor", t, turn_id=self.plans[self.index].turn.turn_id, utterance=text)
            self.on_wait(text)
        if self.holding:
            if not self.standing(rig):
                return True
            rig.set_arm_targets(REST_ARMS)
            rig.set_waist_delta(np.zeros(3))
            rig.set_head_targets(0.0, 0.0)
            self._last_arms = np.asarray(rig.arm_cmd, dtype=float).copy()
            if not self.gate.opened:
                return True
            self.holding = False
            self._passed.add(self.index)
            self.gate.armed = False
            self.on_wait("")
            self.log("visitor_spoke", t, turn_id=self.plans[self.index].turn.turn_id)
            self._enter(t, rig)
        return super().tick(t, rig)


class VRHriTour(HriReceptionTour):
    """Recepción HRI con el reproductor con turnos de diálogo esperando al visitante."""

    def __init__(self, *args, gated: GatedPlayer, **kw):
        super().__init__(*args, **kw)
        self.player = gated                      # _update_hri lo usa en vez de crear uno nuevo
        self.follow_from = 1 << 30               # el huésped lo anda la persona: no se le arrastra


def _utterances(visits_dir: Path) -> dict[str, str]:
    log = json.loads((visits_dir / "visit_log.json").read_text())
    return {t["turn_id"]: t.get("visitor_utterance") or "" for t in log["turns"]}


def vr_reception(person: str, guest, audio_fn, gate: VoiceGate, on_wait=None, floor: int | None = None,
                 come_back: bool = True, key: str = "ultra") -> tuple[VRHriTour, dict]:
    """El recorrido de ``person`` y un resumen (categoría, piso, turnos con su texto) para la interfaz."""
    entry = load_identities()[person]
    category = RANK_CATEGORY.get(entry.get("rank_code") or "", "invitado")
    floor = (entry.get("floor") or floor or DEFAULT_FLOOR) if category in ("vip", "socio", "invitado") else None
    previous = os.environ.get("HRI_VISITS_ROOT")
    try:                                         # la tienda lee la visita de la persona de esta variable global
        visits = use_person_visits(person)
        store = VisitStore()
        plans = [plan_turn(t, store) for t in store.visit_plan(category)]
    finally:
        if previous is None:
            os.environ.pop("HRI_VISITS_ROOT", None)
        else:
            os.environ["HRI_VISITS_ROOT"] = previous
    utterances = _utterances(visits)
    gated = GatedPlayer(key, plans, gate, utterances, on_wait=on_wait, audio_fn=audio_fn)
    escort, _ = escort_steps(category, floor, come_back)
    name = entry.get("display_name") or person
    steps = [
        WaitForGuest(),
        Pause(1.0, f"reconocimiento facial: {name}" if person != UNKNOWN else "cara no reconocida: invitado"),
        Settle(0.8),                                   # el robot atiende desde su puesto, sin dar un paso
        HriTurns(plans=plans, audio_fn=audio_fn),
    ] + escort
    label = f"{name} · {CATEGORIES[category]}" + (f" · piso {floor}" if floor is not None else "")
    tour = VRHriTour(key, steps, label=label, guest=guest, gated=gated)
    summary = {"person": person, "name": name, "category": category, "floor": floor,
               "turns": [(p.turn.state, p.turn.speech, utterances.get(p.turn.turn_id) or "") for p in plans],
               "speech_by_wav": {str(p.turn.wav): p.turn.speech for p in plans}}
    return tour, summary


def guest_near(engine, distance: float = 4.0) -> bool:
    g, rig = engine.guest, engine.rigs["ultra"]
    return math.hypot(g.x - rig.info.base_pos[0], g.y - rig.info.base_pos[1]) < distance
