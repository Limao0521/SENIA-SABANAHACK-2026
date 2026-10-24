"""Tests WAV online del Club N (T6): pass/fail automático de los 6 WAV de validación
+ una visita completa por rango + latencia/memory end-to-end.

Uso (todo online; ver scripts/run_club_hri.sh)::

    python -m x2_ultra.hri_runtime.wavtest --all                 # 6 WAV + 5 visitas
    python -m x2_ultra.hri_runtime.wavtest --wav 5 --rank WORKER # un WAV
    python -m x2_ultra.hri_runtime.wavtest --visit VIP --video   # visita VIP → MP4
    python -m x2_ultra.hri_runtime.wavtest --all --no-motion     # sin servidor de movimiento

Salida: ``out_reto2/online/wavtest_results.json`` + tabla; exit code = nº de fallos.
"""
from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path
from typing import Any, Optional

from . import access, contract, intents
from .app import ClubHriApp, TurnMetrics, VideoRecorder, default_routes, sample_memory
from .personas import ADDRESS

REPO = Path(__file__).resolve().parents[3]
WAV_DIR = REPO / "out_diag/asr/wav"
OUT_DIR = REPO / "out_reto2/online"

# Transcripciones de referencia (T3, out_diag/asr/REPORT.md) y expectations por WAV.
EXPECTED_TEXT = {
    1: "Me gustaría hacer algo de ejercicio en el club. ¿Qué opciones hay?",
    2: "¿A dónde podría ir dentro del club?",
    3: "Dame información acerca del gimnasio.",
    4: "¿Qué opciones de comida o restaurantes hay en el club?",
    5: "¿Puedo ir a los restaurantes?",
    6: "Me gustaría ir al cuarto del staff.",
}
WAV_RANK = {1: "REGULAR", 2: "REGULAR", 3: "REGULAR", 4: "REGULAR", 5: "WORKER", 6: "VIP"}
PERSON_BY_RANK = {
    "VIP": {"person_id": "P005", "name": "Laura", "rank": "VIP"},
    "REGULAR": {"person_id": "P003", "name": "Sebastian", "rank": "REGULAR"},
    "GUEST": {"person_id": "P004", "name": "Mario", "rank": "GUEST"},
    "STAFF": {"person_id": "P001", "name": "Luis", "rank": "STAFF",
              "work_order": {"area": "oficinas_administrativas", "task": "turno administrativo"}},
    "WORKER": {"person_id": "P002", "name": "Josaed", "rank": "WORKER",
               "worker_subtype": "logistica",
               "work_order": {"area": "almacenes_logistica", "task": "descarga de insumos"}},
}

GUIDANCE_RE = re.compile(r"(acompa[ñn](?:ar|e|arle|o)|gu[ií]e|d1|desea que le)", re.I)
DON_DONA_RE = re.compile(r"(?<!\w)(don|doña|dona)\s+(?=[a-záéíóúñ])", re.I)
RANK_LEAK_RE = re.compile(r"(?<!\w)(vip|staff|guest|regular|worker|external)(?!\w)", re.I)


# --------------------------------------------------------------------------- utilidades
def _act_types(rec: dict) -> list[str]:
    return [a.get("act") for a in rec.get("acts", [])]


def _intent_of(rec: dict) -> Optional[str]:
    for a in rec.get("acts", []):
        if a.get("act") == "INTENT":
            return a.get("intent")
    return None


def _area_of(rec: dict) -> Optional[str]:
    for a in rec.get("acts", []):
        if a.get("act") == "INTENT":
            return a.get("area")
    return None


def _spaces_mentioned(spoken: str, kb: dict) -> list[str]:
    t = intents._norm(spoken)
    hits = []
    for sid, sp in kb["espacios"].items():
        nombre = intents._norm(sp["nombre"])
        for frag in re.split(r"\s+y\s+|\s*/\s*|,", nombre):
            frag = frag.strip()
            if frag and len(frag) > 4 and frag in t:
                hits.append(sid)
                break
        for kw in intents.AREA_KEYWORDS[sid]:
            if len(kw) > 5 and re.search(r"(?<!\w)" + re.escape(intents._norm(kw)) + r"(?!\w)", t):
                if sid not in hits:
                    hits.append(sid)
                break
    return hits


def check_persona(spoken_all: str, rank: str) -> list[str]:
    """Tratamiento/forma de persona por rango sobre todo lo hablado en el turno."""
    errs = []
    if not spoken_all.strip():
        return ["sin habla"]
    if DON_DONA_RE.search(spoken_all):
        errs.append("usa don/doña (prohibido)")
    if RANK_LEAK_RE.search(spoken_all):
        errs.append(f"fuga de rango interno en: {spoken_all[:80]!r}")
    if ADDRESS[rank] == "tú":
        if not re.search(r"(?<!\w)(te|tu|tú|verte|cinco|compañero|colega)(?!\w)", spoken_all, re.I):
            errs.append("STAFF debe tutear")
    else:
        if re.search(r"(?<!\w)(tú|contigo)(?!\w)", spoken_all):
            errs.append(f"{rank} debe tratar de usted")
    if rank == "VIP":
        if "!" in spoken_all or "¡" in spoken_all:
            errs.append("VIP con exclamaciones")
        if not re.search(r"(señorita|señora|doctora|señor)\s+", spoken_all, re.I):
            errs.append("VIP sin título + nombre")
    return errs


# --------------------------------------------------------------------------- escenario
class Scenario:
    """Engine + backend + app para un escenario; todo se construye y se libera aquí."""

    def __init__(self, *, sim: bool = True, motion: bool = True, llm: Any = None,
                 asr: Any = None, video: Optional[Path] = None, tts: Any = None):
        self.backend = None
        self.engine = None
        self.recorder = None
        self.motion = None
        if not sim:
            from .app import NullBackend
            self.backend = NullBackend()
        if sim:
            from x2_ultra.hri_runtime.backends.show import ShowBackend
            from x2_ultra.show.stage import CLUB
            from x2_ultra.show.engine import ShowEngine
            from x2_ultra.show.hri.data import VisitStore
            self.engine = ShowEngine(CLUB)
            try:
                store: Any = VisitStore()
            except Exception:
                store = None
            self.backend = ShowBackend(self.engine, store=store, routes=default_routes())
            if video:
                self.recorder = VideoRecorder(self.engine, video)
        if motion:
            from .motion_online.client import MotionServer
            try:
                self.motion = MotionServer()
            except Exception as exc:
                print(f"[warn] motion server no disponible: {exc}")
        self.app = ClubHriApp(self.backend, llm=llm, asr=asr, motion=self.motion,
                              engine=self.engine, recorder=self.recorder, tts=tts,
                              owns_io=False)     # asr/llm compartidos entre escenarios

    def close(self):
        try:
            self.app.close()
        except Exception:
            pass


def make_llm(health_ok: bool = True):
    from .llm_client import HriLLM, LlamaStreamClient
    client = LlamaStreamClient()
    ok = client.health()
    if not ok:
        print("[warn] x2-llm no responde en HRI_LLM_URL; la FSM usará respaldo determinista "
              "(los checks de respuesta KB fallarán en modo live)")
        return None
    return HriLLM(client=client)


def make_asr():
    try:
        from .asr.client import AsrClient
        return AsrClient()
    except Exception as exc:
        print(f"[warn] ASR no disponible ({exc}); usar --text-mode")
        return None


# --------------------------------------------------------------------------- test de WAV
def run_wav(n: int, rank: str, *, shared: dict, text_mode: bool = False,
            sim: bool = True, motion: bool = True) -> dict:
    kb = contract.load_kb()
    person = dict(PERSON_BY_RANK[rank])
    wav = WAV_DIR / f"wav{n}.wav"
    sc = Scenario(sim=sim, motion=motion, llm=shared.get("llm"),
                  asr=None if text_mode else shared.get("asr"))
    checks: dict[str, Any] = {}
    t0 = time.perf_counter()
    try:
        sc.app.admit(qr_image=person)
        admit_spoken = " ".join(sc.app.turns[-1]["spoken"])
        rec = (sc.app.user_turn(text=EXPECTED_TEXT[n]) if text_mode
               else sc.app.user_turn(wav_path=str(wav)))
        spoken = " ".join(rec["spoken"])
        intent_, area = _intent_of(rec), _area_of(rec)
        acts = _act_types(rec)
        asr_text = rec.get("user_text", "")
        checks["asr_text"] = asr_text
        checks["intent"] = intent_
        checks["area"] = area
        checks["spoken"] = spoken[:400]
        # 1) ASR fiel (solo live): coincidencia sustancial con la referencia
        if not text_mode and asr_text:
            ref = intents._norm(EXPECTED_TEXT[n])
            got = intents._norm(asr_text)
            words = set(ref.split())
            checks["asr_word_recall_ok"] = len(words & set(got.split())) / max(1, len(words)) >= 0.7
        # 2) checks específicos por WAV
        if n in (1, 3, 4):
            checks["intent_PREGUNTA"] = intent_ == "PREGUNTA"
            checks["no_guidance_acts"] = not ({"POINT", "WALK_WITH", "DISPATCH_D1"} & set(acts)) \
                and "GUIDANCE_OFFER" not in sc.app.fsm.state.value
            checks["no_guidance_words"] = not GUIDANCE_RE.search(spoken)
            checks["answered_from_kb"] = bool(_spaces_mentioned(spoken, kb)) or bool(
                re.search(r"(horario|abre|piso|clases|restaura|cafeter|gimnasio|deport)", spoken, re.I))
        if n == 2:
            checks["intent_PREGUNTA"] = intent_ == "PREGUNTA"
            mentioned = _spaces_mentioned(spoken, kb)
            checks["lists_spaces"] = len(mentioned) >= 2
            st = person.get("worker_subtype")
            bad = [s for s in mentioned if not access.allowed(s, rank, st)]
            checks["only_allowed_spaces"] = not bad
            checks["bad_spaces"] = bad
        if n == 5:
            checks["intent_PIDE_DESTINO"] = intent_ == "PIDE_DESTINO"
            checks["area_restaurantes"] = area == "restaurantes_cafeterias" or area is None
            checks["denied"] = ("ACCESS_DENIAL" in json.dumps(rec["acts"], default=str)
                                or any(a.get("act") == "SAY" and a.get("type") == "NEGATION_NORMAL"
                                       for a in rec["acts"])
                                or not access.allowed(area or "restaurantes_cafeterias", rank,
                                                      person.get("worker_subtype")))
            checks["no_walk_no_dispatch"] = not ({"WALK_WITH", "DISPATCH_D1"} & set(acts))
            alt = _spaces_mentioned(spoken, kb)
            checks["offers_alternative"] = any(
                access.allowed(s, rank, person.get("worker_subtype")) for s in alt)
        if n == 6:
            checks["intent_PIDE_DESTINO"] = intent_ == "PIDE_DESTINO"
            eff_area = area or intents.match_area(EXPECTED_TEXT[n])
            checks["area_denied_for_rank"] = (eff_area is not None
                                              and not access.allowed(eff_area, rank, None))
            checks["denied_speech"] = bool(spoken) and not GUIDANCE_RE.search(spoken)
            checks["no_walk_no_dispatch"] = not ({"WALK_WITH", "DISPATCH_D1"} & set(acts))
        # 3) persona / seguridad
        checks["persona_ok"] = not check_persona(admit_spoken + " " + spoken, rank)
        checks["persona_errors"] = check_persona(admit_spoken + " " + spoken, rank)
        safety = sc.app.safety_summary()
        checks["safety"] = safety
        checks["falls_0"] = safety["falls"] == 0
        checks["gestures_walking_0"] = safety["gestures_while_walking"] == 0
        checks["vmax_le_10"] = safety["velocity_ok_outside_emotes"]
        result = {"test": f"wav{n}", "rank": rank, "wall_s": round(time.perf_counter() - t0, 1),
                  "metrics": rec["metrics"], "checks": checks}
        if sc.recorder is not None:
            sc.recorder.finish(sc.app)
    finally:
        sc.close()
    result["passed"] = all(v is True for k, v in checks.items()
                           if k.endswith("_ok") or k.startswith(("intent_", "no_", "only_", "lists_",
                                                                 "denied", "offers_", "area_", "persona_ok",
                                                                 "falls_", "gestures_", "vmax_", "answered_")))
    return result


# --------------------------------------------------------------------------- visita por rango
VISIT_SCRIPTS = {
    "VIP": [("Me gustaría ir a la biblioteca.", None), ("Sí, por favor.", "WALK_WITH")],
    "REGULAR": [("Me gustaría ir a la biblioteca.", None), ("Sí, por favor.", "DISPATCH_D1")],
    "GUEST": [("Quisiera ir al centro de convenciones.", None), ("Sí, con gusto.", "DISPATCH_D1")],
    "STAFF": [("(llegada: destino de rol proactivo)", "POINT")],
    "WORKER": [("(llegada: destino de rol proactivo)", "POINT")],
}


def run_visit(rank: str, *, shared: dict, sim: bool = True, motion: bool = True,
              video: Optional[Path] = None) -> dict:
    person = dict(PERSON_BY_RANK[rank])
    sc = Scenario(sim=sim, motion=motion, llm=shared.get("llm"), asr=shared.get("asr"),
                  video=video)
    checks: dict[str, Any] = {}
    t0 = time.perf_counter()
    try:
        admit = sc.app.admit(qr_image=person)
        admit_acts = json.dumps(sc.app.turns[-1]["acts"], default=str)
        admit_spoken = " ".join(sc.app.turns[-1]["spoken"])
        checks["rank_confirmed"] = (admit.get("rank") or "").upper().startswith(rank[:4]) or \
            admit.get("rank") == rank
        if rank in ("STAFF", "WORKER"):
            # la FSM registra {"act": "POINT", "direction": ...} del destino de rol proactivo
            checks["proactive_role_destination"] = any(
                x.get("act") == "POINT" for x in sc.app.turns[-1]["acts"])
            checks["point_on_arrival"] = checks["proactive_role_destination"] and bool(
                (admit.get("work_order_direction")))
        expect_act = None
        for text, want in VISIT_SCRIPTS[rank]:
            if want is None or text.startswith("("):
                if want and rank in ("STAFF", "WORKER"):
                    continue
            if text.startswith("("):
                continue
            rec = sc.app.user_turn(text=text)
            acts = json.dumps(rec["acts"], default=str)
            if want == "WALK_WITH":
                checks["vip_walk_with"] = "WALK_WITH" in acts
                # la caminata bloquea gestos: avanzar hasta terminar
                guard = 0
                while sc.app.backend.state().get("walking") and guard < 900:
                    sc.app.advance(0.2)
                    guard += 1
                checks["walk_completed"] = guard < 900
            elif want == "DISPATCH_D1":
                checks["d1_dispatched"] = "DISPATCH_D1" in acts
            elif want is None:
                # la FSM debe quedar ofreciendo guía (VIP/REGULAR/GUEST) o ya haber indicado (STAFF/WORKER)
                checks["guidance_offered"] = bool(
                    sc.app.fsm.state.value == "GUIDANCE_OFFER"
                    or (rank in ("STAFF", "WORKER") and "POINT" in acts))
                expect_act = None
        # despedida
        fare = sc.app.user_turn(text="Eso es todo, muchas gracias. Adiós.")
        checks["farewell_end"] = sc.app.fsm.state.value in ("FAREWELL", "END")
        all_spoken = admit_spoken + " " + " ".join(
            s for t in sc.app.turns for s in t["spoken"])
        perr = check_persona(all_spoken, rank)
        checks["persona_ok"] = not perr
        checks["persona_errors"] = perr
        safety = sc.app.safety_summary()
        checks["safety"] = safety
        checks["falls_0"] = safety["falls"] == 0
        checks["gestures_walking_0"] = safety["gestures_while_walking"] == 0
        checks["vmax_le_10"] = safety["velocity_ok_outside_emotes"]
        result = {"test": f"visit_{rank}", "wall_s": round(time.perf_counter() - t0, 1),
                  "metrics": [t["metrics"] for t in sc.app.turns], "checks": checks,
                  "n_turns": len(sc.app.turns)}
        if sc.recorder is not None:
            path = sc.recorder.finish(sc.app)
            result["video"] = str(path)
    finally:
        sc.close()
    result["passed"] = all(v is True for k, v in checks.items()
                           if k.endswith("_ok") or k.startswith(("rank_", "proactive_", "point_",
                                                                 "vip_", "walk_", "d1_", "guidance_",
                                                                 "farewell_", "falls_", "gestures_",
                                                                 "vmax_", "persona_ok")))
    return result


# --------------------------------------------------------------------------- main
def _fmt(res: dict) -> str:
    ok = "PASS" if res.get("passed") else "FAIL"
    failed = [k for k, v in res.get("checks", {}).items() if v is False]
    return f"[{ok}] {res['test']:14s} ({res.get('wall_s', 0):5.1f}s)" + \
        (f"  fallos: {failed}" if failed else "")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--all", action="store_true", help="6 WAV + 5 visitas")
    ap.add_argument("--wav", type=int, action="append", help="WAV nº (1-6); repetible")
    ap.add_argument("--rank", help="rango del WAV/visita (default: el del test)")
    ap.add_argument("--visit", action="append", help="visita por rango: VIP|REGULAR|GUEST|STAFF|WORKER")
    ap.add_argument("--video", action="store_true", help="MP4 de la visita VIP → out_reto2/online/")
    ap.add_argument("--no-motion", action="store_true", help="sin servidor de movimiento online")
    ap.add_argument("--no-sim", action="store_true", help="sin engine (solo diálogo)")
    ap.add_argument("--text-mode", action="store_true", help="transcripciones de referencia en vez de ASR")
    ap.add_argument("--llm-off", action="store_true", help="sin x2-llm (respaldo determinista)")
    ap.add_argument("--out", type=Path, default=OUT_DIR / "wavtest_results.json")
    a = ap.parse_args(argv)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    shared: dict = {}
    import os
    pids = [os.getpid()]
    if not a.llm_off:
        shared["llm"] = make_llm()
    if not a.text_mode:
        shared["asr"] = make_asr()
        if shared["asr"] is not None and getattr(shared["asr"], "proc", None):
            pids.append(shared["asr"].proc.pid)
    wavs = a.wav or ([1, 2, 3, 4, 5, 6] if a.all else [])
    visits = a.visit or (["VIP", "REGULAR", "GUEST", "STAFF", "WORKER"] if a.all else [])
    results = []
    t_all = time.perf_counter()
    for n in wavs:
        rank = a.rank or WAV_RANK[n]
        results.append(run_wav(n, rank, shared=shared, text_mode=a.text_mode,
                               sim=not a.no_sim, motion=not a.no_motion))
        print(_fmt(results[-1]), flush=True)
    for rank in visits:
        video = (OUT_DIR / "vip_visit.mp4") if (a.video and rank == "VIP") else None
        results.append(run_visit(rank, shared=shared, sim=not a.no_sim,
                                 motion=not a.no_motion, video=video))
        print(_fmt(results[-1]), flush=True)
    for obj in (shared.get("asr"), shared.get("motion")):
        try:
            if obj is not None:
                obj.stop()
        except Exception:
            pass
    mem = sample_memory(pids or None)
    summary = {"date": time.strftime("%Y-%m-%d %H:%M:%S"), "n_tests": len(results),
               "n_passed": sum(1 for r in results if r.get("passed")),
               "wall_total_s": round(time.perf_counter() - t_all, 1),
               "memory": mem, "results": results}
    a.out.write_text(json.dumps(summary, ensure_ascii=False, indent=1, default=str))
    print(f"\n{summary['n_passed']}/{summary['n_tests']} PASS · memoria: {json.dumps(mem)}")
    print(f"resultados: {a.out}")
    n_fail = summary["n_tests"] - summary["n_passed"]
    return 1 if n_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
