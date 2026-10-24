"""Escenarios de carga del benchmark: emulan el procesamiento del X2 Ultra en este PC.

Cada escenario reproduce una parte del pipeline que en el robot real corre en el
PC1 (MC: lazo de locomoción a 50 Hz) y/o en el PC2 (Orin NX: HRI). Aquí todo corre
en un único proceso x86-64; las latencias medidas se extrapolan al Orin NX con los
factores de bench.power (estimaciones documentadas).

Escenarios (ver --list):
  policy_inference  inferencia pura de las dos policies ONNX (el núcleo del MC)
  physics_single    paso de física MuJoCo de un solo robot (2 ms)
  stage_walk        reto 1: los dos robots andando en la tarima (ciclo de control 50 Hz)
  stage_dance       reto 1: los dos robots bailando Tai Chi estilo Chen (cinemático)
  stage_show        reto 1: show comercial completo (19 parlamentos, brazos a 50 Hz)
  club_tours        reto 2: recepción guiada de las cinco categorías, con rutas y puertas
  realtime_50hz     reto 1: el lazo en tiempo real del engine, jitter real contra 20 ms
  render_offscreen  render 720p de la cámara del público (lo que paga la GPU/CPU al ver)
  video_decode      decodificación ffmpeg del vídeo de la pantalla LED (show/screen.py)
"""
from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

from x2_ultra.bench.stats import LatencyStats
from x2_ultra.bench.system import timer_resolution
from x2_ultra.control import obs_spec as S
from x2_ultra.paths import REPO_ROOT, agibot_policy, default_policy

MOTIONS = REPO_ROOT / "assets" / "motions"


@dataclass
class Context:
    scale: float = 1.0               # multiplica duraciones/iteraciones por escenario
    quick: bool = False              # atajos: menos categorías, menos segundos
    realtime_s: float = 30.0         # duración del escenario en tiempo real
    out_dir: Path = field(default_factory=lambda: REPO_ROOT / "runs" / "bench")


@dataclass
class Scenario:
    key: str
    reto: str                        # "componente" | "reto1" | "reto2"
    description: str
    run: Callable[[Context], dict]


# --------------------------------------------------------------------------- utilidades

def _rtf(eng, wall_s: float) -> float:
    return eng.sim_time / wall_s if wall_s > 0 else float("nan")


# --------------------------------------------------------------------------- componentes

def sc_policy_inference(ctx: Context) -> dict:
    """Inferencia ONNX pura: lo que el MC del robot hace 50 veces por segundo."""
    import onnxruntime as ort

    out: dict = {}
    n = int(2000 * ctx.scale) if not ctx.quick else 500
    for label, path in (("brazos_libres", default_policy()), ("agibot_mcrl", agibot_policy())):
        sess = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
        inp = sess.get_inputs()[0]
        shape = [1 if isinstance(d, (str, type(None))) else int(d) for d in inp.shape]
        x = np.zeros(shape, dtype=np.float32)
        for _ in range(200):                                  # calentamiento
            sess.run(None, {inp.name: x})
        st = LatencyStats()
        for _ in range(n):
            t0 = time.perf_counter()
            sess.run(None, {inp.name: x})
            st.add(time.perf_counter() - t0)
        summ = st.summary_ms()
        summ["hz_max_theoretical"] = round(1000.0 / summ["p99"], 0) if summ.get("p99") else None
        out[label] = summ
    return {"metrics": out, "unit": "ms",
            "notes": ["Entrada obs 1x430 (brazos_libres) y 1x500 (agibot_mcrl), CPUExecutionProvider."]}


def sc_physics_single(ctx: Context) -> dict:
    """Paso de física de un robot sin escenario: el coste base de MuJoCo (timestep 2 ms)."""
    import mujoco

    model = mujoco.MjModel.from_xml_path(str(REPO_ROOT / "models" / "x2_ultra_n_omnihand_mesa.xml"))
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    n = int(10000 * ctx.scale) if not ctx.quick else 2000
    st = LatencyStats()
    for _ in range(n):
        t0 = time.perf_counter()
        mujoco.mj_step(model, data)
        st.add(time.perf_counter() - t0)
    summ = st.summary_ms()
    summ["rtf_single_thread"] = round(model.opt.timestep / (summ["mean"] / 1000.0), 2)
    return {"metrics": {"mj_step": summ}, "unit": "ms",
            "notes": [f"nq={model.nq} nu={model.nu} timestep={model.opt.timestep}s integrator=implicitfast"]}


# --------------------------------------------------------------------------- reto 1

def sc_stage_walk(ctx: Context) -> dict:
    """Reto 1: los dos robots (Ultra-N + EDU) andando en la tarima con el lazo de 50 Hz."""
    from x2_ultra.show.engine import ShowEngine
    from x2_ultra.show.stage import ESCENARIO

    eng = ShowEngine(decor=ESCENARIO)
    sim_s = 60.0 * ctx.scale if not ctx.quick else 15.0
    cycles = int(sim_s / S.POLICY_DT)
    # Trayectoria circular: vx=0.4, wz=0.5 -> radio ~0.8 m, cabe en la tarima (x∈(−1.9,1.4),
    # y∈(−3,3)). Los dos robots describen círculos paralelos en fase: su separación es constante
    # (1,5 m), nunca chocan (trazas verificadas: ultra y∈[−0.8,0.8], edu y∈[0.7,2.3]). Siempre
    # avanzan hacia delante, que es lo que la policy hace mejor; invertir el sentido en el borde
    # los hacía caer (marcha atrás a 0,5 m/s cerca del canto), y con wz=0.35 (radio 1,14 m) el
    # edu se salía por y>3.
    eng.rigs["ultra"].set_velocity_target(0.4, 0, 0.5)
    eng.rigs["edu"].set_velocity_target(0.4, 0, 0.5)

    st = LatencyStats(budget=S.POLICY_DT)
    prev = eng.data.qpos[eng.rigs["ultra"].qbase:eng.rigs["ultra"].qbase + 2].copy()
    length = 0.0
    t0 = time.perf_counter()
    for _ in range(cycles):
        c0 = time.perf_counter()
        eng.step_control()
        st.add(time.perf_counter() - c0)
        xy = eng.data.qpos[eng.rigs["ultra"].qbase:eng.rigs["ultra"].qbase + 2]
        length += float(np.linalg.norm(xy - prev))
        prev = xy.copy()
    wall = time.perf_counter() - t0
    return {"metrics": {
        "cycle_50hz": st.summary_ms(),
        "sim_seconds": round(eng.sim_time, 1),
        "wall_seconds": round(wall, 1),
        "rtf": round(_rtf(eng, wall), 2),
        "ultra_path_m": round(length, 1),
        "falls": sum(r.info.falls for r in eng.rigs.values()),
    }, "notes": ["ESCENARIO: 2 robots, política brazos_libres a 50 Hz, decimation=10 pasos de 2 ms.",
                 "Círculos paralelos de radio ~0,8 m (vx 0,4 m/s, wz 0,5 rad/s): marcha delantera",
                 "continua dentro de la tarima, separación constante de 1,5 m."]}


def sc_stage_dance(ctx: Context) -> dict:
    """Reto 1: los dos robots bailando Tai Chi estilo Chen (reproducción cinemática)."""
    from x2_ultra.show.engine import ShowEngine
    from x2_ultra.show.stage import ESCENARIO

    clip = MOTIONS / "chen_style_tai_chi.json"
    if not clip.is_file():
        return {"skipped": f"falta {clip} (scripts/fetch_linkcraft_dance.py --all-requested)"}
    eng = ShowEngine(decor=ESCENARIO)
    eng.start_dance_many(("ultra", "edu"), clip)
    st = LatencyStats(budget=S.POLICY_DT)
    limit = 150.0 if not ctx.quick else 30.0
    t0 = time.perf_counter()
    while eng.sim_time < limit and any(r.dance is not None for r in eng.rigs.values()):
        c0 = time.perf_counter()
        eng.step_control()
        st.add(time.perf_counter() - c0)
    wall = time.perf_counter() - t0
    return {"metrics": {
        "cycle_50hz": st.summary_ms(),
        "sim_seconds": round(eng.sim_time, 1),
        "wall_seconds": round(wall, 1),
        "rtf": round(_rtf(eng, wall), 2),
        "dance_finished": all(r.dance is None for r in eng.rigs.values()),
        "falls": sum(r.info.falls for r in eng.rigs.values()),
    }, "notes": ["Baile cinemático: se impone la postura del clip en cada paso (sin policy de brazos)."]}


def sc_stage_show(ctx: Context) -> dict:
    """Reto 1: show comercial completo (19 parlamentos), brazos a 50 Hz sobre la marcha."""
    from x2_ultra.show.commercial import CommercialShow
    from x2_ultra.show.engine import ShowEngine
    from x2_ultra.show.stage import ESCENARIO

    show = CommercialShow()
    eng = ShowEngine(decor=ESCENARIO)
    st = LatencyStats(budget=S.POLICY_DT)
    fired = 0
    limit = show.duration + 5.0 if not ctx.quick else 30.0
    t0 = time.perf_counter()
    while eng.sim_time < limit:
        fired += len(show.due(eng.sim_time))
        targets = show.targets(eng.sim_time)
        eng.set_arm_targets("ultra", targets["ultra"])
        eng.set_arm_targets("edu", targets["edu"])
        c0 = time.perf_counter()
        eng.step_control()
        st.add(time.perf_counter() - c0)
        if ctx.quick and fired >= 3:
            break
    wall = time.perf_counter() - t0
    return {"metrics": {
        "cycle_50hz": st.summary_ms(),
        "sim_seconds": round(eng.sim_time, 1),
        "wall_seconds": round(wall, 1),
        "rtf": round(_rtf(eng, wall), 2),
        "lines_fired": fired,
        "show_duration_s": round(show.duration, 1),
        "complete": fired == 19,
        "falls": sum(r.info.falls for r in eng.rigs.values()),
    }, "notes": ["Timeline wall-clock del pack comercial; sin audio (la UI lo posee). Solo gesticula quien habla."]}


def sc_realtime_50hz(ctx: Context) -> dict:
    """Reto 1: el lazo en tiempo real (thread del engine): jitter real contra el periodo de 20 ms.

    En Windows se activa timeBeginPeriod(1): sin ello el SO cuantiza el sleep a ~15,6 ms
    y el jitter medido sería un artefacto de la plataforma, no del pipeline.
    """
    from x2_ultra.show.engine import ShowEngine
    from x2_ultra.show.stage import ESCENARIO

    eng = ShowEngine(decor=ESCENARIO)
    eng.rigs["ultra"].set_velocity_target(0.4, 0, 0.5)   # círculos paralelos, como en stage_walk
    eng.rigs["edu"].set_velocity_target(0.4, 0, 0.5)
    starts: list[float] = []
    inner = eng.step_control

    def wrapped() -> None:
        starts.append(time.perf_counter())
        inner()

    eng.step_control = wrapped        # type: ignore[method-assign]
    dur = ctx.realtime_s if not ctx.quick else min(10.0, ctx.realtime_s)
    with timer_resolution(1):
        t0 = time.perf_counter()
        eng.start()
        try:
            time.sleep(dur)
        finally:
            eng.stop()
        wall = time.perf_counter() - t0
    periods = LatencyStats(budget=S.POLICY_DT * 1.25)   # tolerancia de jitter: 25 ms (ver nota)
    periods.extend(np.diff(starts))
    summ = periods.summary_ms()
    late = int(sum(1 for p in np.diff(starts) if p > S.POLICY_DT * 1.25))
    return {"metrics": {
        "period_50hz": summ,
        "sim_seconds": round(eng.sim_time, 1),
        "wall_seconds": round(wall, 1),
        "cycles": len(starts),
        "expected_cycles": int(wall / S.POLICY_DT),
        "late_over_25ms": late,
        "rtf": round(_rtf(eng, wall), 3),
        "falls": sum(r.info.falls for r in eng.rigs.values()),
    }, "unit": "ms", "no_orin": True,
        "notes": ["Periodo ENTRE ciclos del hilo en tiempo real (objetivo 20,00 ms; la tolerancia de",
                  "overrun son 25 ms: un sleep de Windows/Linux aterriza 0-2 ms tarde por diseño).",
                  "No se escala al Orin: es scheduling del SO, no compute. El compute por ciclo está",
                  "en stage_walk (cycle_50hz), que SÍ se escala.",
                  "En el robot real este lazo lo garantiza el MC del PC1 (RK3588s) con RT."]}


def sc_render_offscreen(ctx: Context) -> dict:
    """Render 720p de la cámara del público: lo que cuesta mantener la vista de la demo."""
    import mujoco

    from x2_ultra.show.engine import ShowEngine
    from x2_ultra.show.stage import ESCENARIO

    eng = ShowEngine(decor=ESCENARIO)
    view = ESCENARIO.cameras["Público"]
    cam = mujoco.MjvCamera()
    cam.lookat[:] = view.lookat
    cam.distance = view.distance
    cam.azimuth = view.azimuth
    cam.elevation = view.elevation
    rend = mujoco.Renderer(eng.model, height=720, width=1280)
    n = int(600 * ctx.scale) if not ctx.quick else 120
    st = LatencyStats()
    eng.rigs["ultra"].set_velocity_target(0.4, 0, 0)
    draw = getattr(rend, "render", None) or (lambda: rend.read_pixels(depth=False))
    for i in range(n):
        eng.step_control()
        if i % 2 == 0:                       # ~25 fps de render sobre control a 50 Hz
            t0 = time.perf_counter()
            rend.update_scene(eng.data, camera=cam)
            draw()
            st.add(time.perf_counter() - t0)
    summ = st.summary_ms()
    summ["fps_achievable"] = round(1000.0 / summ["mean"], 1) if summ.get("mean") else None
    return {"metrics": {"render_720p": summ}, "unit": "ms",
            "notes": ["mujoco.Renderer offscreen (rasterización CPU/OpenGL sin ventana); la app Qt usa",
                      "la misma escena en pantalla. El upload de la textura de vídeo no está incluido."]}


def sc_video_decode(ctx: Context) -> dict:
    """Decodificación ffmpeg del vídeo de la pantalla LED (1280x720), como show/screen.py."""
    try:
        import imageio_ffmpeg
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        return {"skipped": "imageio-ffmpeg no instalado"}
    video = REPO_ROOT / "docs" / "media" / "demo.mp4"
    if not video.is_file():
        return {"skipped": f"falta {video}"}
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(video),
           "-vf", "scale=1280:720", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    assert proc.stdout is not None
    frame_bytes = 1280 * 720 * 3
    st = LatencyStats()
    frames = 0
    limit = int(900 * ctx.scale) if not ctx.quick else 150
    try:
        while frames < limit:
            t0 = time.perf_counter()
            buf = proc.stdout.read(frame_bytes)
            st.add(time.perf_counter() - t0)
            if len(buf) < frame_bytes:
                break
            frames += 1
    finally:
        proc.kill()
        proc.wait()
    if frames == 0:
        return {"skipped": "ffmpeg no produjo fotogramas"}
    summ = st.summary_ms()
    summ["fps_achievable"] = round(1000.0 / summ["mean"], 1) if summ.get("mean") else None
    return {"metrics": {"decode_720p_rgb24": summ, "frames": frames}, "unit": "ms",
            "notes": [f"{video.name} escalado a 1280x720 rgb24 por pipe (como screen.py en su hilo)."]}


# --------------------------------------------------------------------------- reto 2

def sc_club_tours(ctx: Context) -> dict:
    """Reto 2: recepción guiada completa por categoría (saludo, ruta, puertas, vuelta)."""
    from x2_ultra.show.engine import ShowEngine
    from x2_ultra.show.guide import club_tour
    from x2_ultra.show.stage import CLUB

    plans = [("vip", 3)] if ctx.quick else [("vip", 3), ("socio", 2), ("invitado", 6),
                                            ("personal", None), ("externo", None)]
    per_cat: dict[str, dict] = {}
    for category, floor in plans:
        eng = ShowEngine(decor=CLUB)
        tour = club_tour("ultra", category=category, floor=floor, come_back=True)
        eng.start_tour(tour)
        st = LatencyStats(budget=S.POLICY_DT)
        limit = 300.0 if not ctx.quick else 60.0
        t0 = time.perf_counter()
        while not tour.done and eng.sim_time < limit:
            c0 = time.perf_counter()
            eng.step_control()
            st.add(time.perf_counter() - c0)
        wall = time.perf_counter() - t0
        per_cat[category] = {
            "cycle_50hz": st.summary_ms(),
            "sim_seconds": round(eng.sim_time, 1),
            "wall_seconds": round(wall, 1),
            "rtf": round(_rtf(eng, wall), 2),
            "tour_done": bool(tour.done),
            "phrases": len(eng.speech),
            "falls": int(sum(r.info.falls for r in eng.rigs.values())),
        }
    cycles = [v["cycle_50hz"] for v in per_cat.values() if v["cycle_50hz"]]
    return {"metrics": {
        "categories": per_cat,
        "cycle_50hz_worst_mean_ms": max((c["mean"] for c in cycles), default=None),
        "all_tours_done": all(v["tour_done"] for v in per_cat.values()),
        "any_fall": any(v["falls"] for v in per_cat.values()),
    }, "notes": ["CLUB: 1 robot + huésped mocap + puertas automáticas (props) + texturas procedurales.",
                 "Recepción manual (sin Face): el recorrido y las frases los dirige club_tour."]}


# --------------------------------------------------------------------------- registro

SCENARIOS: list[Scenario] = [
    Scenario("policy_inference", "componente", "Inferencia ONNX de las dos policies (núcleo del MC)",
             sc_policy_inference),
    Scenario("physics_single", "componente", "mj_step de un robot a 2 ms (física base)", sc_physics_single),
    Scenario("stage_walk", "reto1", "Dos robots andando en la tarima (lazo 50 Hz)", sc_stage_walk),
    Scenario("stage_dance", "reto1", "Los dos robots bailando Tai Chi Chen (cinemático)", sc_stage_dance),
    Scenario("stage_show", "reto1", "Show comercial completo: 19 parlamentos + brazos 50 Hz", sc_stage_show),
    Scenario("realtime_50hz", "reto1", "Lazo en tiempo real: jitter del periodo de 20 ms", sc_realtime_50hz),
    Scenario("render_offscreen", "reto1", "Render offscreen 720p de la cámara del público", sc_render_offscreen),
    Scenario("video_decode", "reto1", "Decodificación ffmpeg 720p del vídeo de la pantalla", sc_video_decode),
    Scenario("club_tours", "reto2", "Recepción guiada de las cinco categorías del Club N", sc_club_tours),
]

BY_KEY = {s.key: s for s in SCENARIOS}
