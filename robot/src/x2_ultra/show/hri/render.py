"""Recepción HRI completa por persona, sin ventana (EGL): simulación + vídeo MP4 + audio mezclado.

    python -m x2_ultra.show.hri.render --person Laura --out out_reto2/laura.mp4
    python -m x2_ultra.show.hri.render --photo ~/fotos/Laura --person Laura --out out_reto2/laura.mp4

Flujo: foto → ``face_id`` (venv de Face, subproceso) → persona/rango → ``ShowEngine(CLUB)`` →
``hri_reception`` (el huésped entra, el robot camina a su punto, PARA, se asienta y solo entonces
los turnos HRIEGO; al terminar, brazos a reposo y camina la ruta) → registro 50 Hz → fotogramas →
mezcla del audio precomputado (WAV Supertonic de visits_v2 + espeak-ng para las frases de cierre)
en la línea de tiempo de la simulación. ``--no-video`` ejecuta la misma simulación sin renderizar
(validación rápida de métricas).
"""
from __future__ import annotations

import os

os.environ.setdefault("MUJOCO_GL", "egl")

import argparse  # noqa: E402
import json  # noqa: E402
import math  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402
import tempfile  # noqa: E402
import time  # noqa: E402
from pathlib import Path  # noqa: E402

import imageio.v2 as imageio  # noqa: E402
import mujoco  # noqa: E402
import numpy as np  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from x2_ultra.show import club as C  # noqa: E402
from x2_ultra.show.engine import ShowEngine  # noqa: E402
from x2_ultra.show.hri.data import (ROUTE_DIRECTION, VisitStore, load_identities,  # noqa: E402
                                    use_person_visits)
from x2_ultra.show.hri.face_id import (identify_photo, precomputed_identity,  # noqa: E402
                                       reception_identity)
from x2_ultra.show.hri.logger import RunLogger  # noqa: E402
from x2_ultra.show.hri.player import plan_turn  # noqa: E402
from x2_ultra.show.hri.tour import HriTurns, hri_reception  # noqa: E402
from x2_ultra.show.stage import CLUB  # noqa: E402

MAX_SIM_SECONDS = 240.0
TAIL_SECONDS = 2.5
ROUTE_STEP_INDEX = 6            # primer Walk de la ruta: cambia la cámara a «seguir robot»
# Si el usuario fijó HRI_VISITS_ROOT a mano, manda; si no, cada persona usa su paquete realnames.
_USER_VISITS_ROOT = os.environ.get("HRI_VISITS_ROOT")


def people() -> dict:
    """Las 6 personas de validación (5 identidades + 1 desconocida) desde
    ``assets/hri/identities.json``: persona → (foto opcional, categoría, piso). Las fotos son
    datos personales y NO se versionan; sin foto (o sin módulo Face) se usa la identidad
    precomputada (persona → rango)."""
    return {name: (e.get("photo"), e.get("category"), e.get("floor"))
            for name, e in load_identities().items()}


PEOPLE = people()


def _font(size: int):
    for path in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                 "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf"):
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _caption(frame: np.ndarray, top: str, bottom: str, font, small) -> np.ndarray:
    img = Image.fromarray(frame)
    draw = ImageDraw.Draw(img, "RGBA")
    w, h = img.size
    draw.rectangle([0, h - int(h * 0.11), w, h], fill=(10, 14, 22, 175))
    if bottom:
        draw.text((int(w * 0.025), h - int(h * 0.095)), bottom[:110], font=font, fill=(240, 244, 250, 255))
    draw.rectangle([0, 0, w, int(h * 0.06)], fill=(10, 14, 22, 150))
    draw.text((int(w * 0.02), int(h * 0.008)), top[:130], font=small, fill=(225, 232, 242, 235))
    return np.asarray(img)


def _espeak_wav(text: str, out: Path) -> Path | None:
    if shutil.which("espeak-ng") is None or not text.strip():
        return None
    voice = "es-419"
    try:
        voices = subprocess.run(["espeak-ng", "--voices=es"], capture_output=True, text=True,
                                timeout=10).stdout
        if "la-1" in voices:
            voice = "es-la-1" if "es-la-1" in voices else voice
    except (subprocess.SubprocessError, OSError):
        pass
    try:
        subprocess.run(["espeak-ng", "-v", voice, "-s", "150", "-w", str(out), text],
                       check=True, timeout=60, capture_output=True)
        return out
    except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired):
        return None


def _mux_audio(video: Path, segments: list[tuple[Path, float]], total_s: float, out: Path) -> bool:
    """Mezcla los WAV en la línea de tiempo de la simulación (adelay + amix) sobre el vídeo."""
    segments = [(w, t0) for w, t0 in segments if Path(w).is_file()]
    if not segments or shutil.which("ffmpeg") is None:
        return False
    inputs: list[str] = []
    parts: list[str] = []
    for i, (wav, t0) in enumerate(segments):
        ms = max(0, int(round(t0 * 1000)))
        inputs += ["-i", str(wav)]
        parts.append(f"[{i + 1}:a]adelay={ms}|{ms},apad[a{i}]")   # entrada 0 = el vídeo
    mix = "".join(f"[a{i}]" for i in range(len(segments)))
    parts.append(f"{mix}amix=inputs={len(segments)}:normalize=0:duration=longest,"
                 f"atrim=0:{total_s:.3f}[outa]")
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), *inputs,
           "-filter_complex", ";".join(parts), "-map", "0:v", "-map", "[outa]",
           "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", str(out)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    return proc.returncode == 0


def rank_identity(rank_code: str) -> dict:
    """Identidad sintética para ``--rank``: rango confirmado sin foto ni Face."""
    from x2_ultra.show.hri.llm_script import RANK_CATEGORY
    rank = str(rank_code).upper()
    if rank not in RANK_CATEGORY:
        raise SystemExit(f"rango desconocido: {rank_code} (use uno de {sorted(RANK_CATEGORY)})")
    return {"photo": None, "status": "KNOWN", "source": "precomputed", "top1_score": None,
            "profile": {"person_id": None, "display_name": f"huésped {rank}",
                        "rank_code": rank, "identity_status": "CONFIRMED"}}


def run_person(photo: str | Path | None, person: str, out: Path | None, category: str | None = None,
               floor: int | None = None, width: int = 960, height: int = 540, fps: int = 25,
               video: bool = True, logdir: Path | None = None, face_result: dict | None = None,
               live_audio: bool = False) -> dict:
    t_start = time.perf_counter()
    if face_result is None:
        # con foto: reconocimiento en vivo (módulo Face, opcional); sin foto: identidad
        # precomputada del repo (assets/hri/identities.json, persona → rango).
        face_result = identify_photo(photo) if photo else precomputed_identity(person)
    face_category, profile, face_status = reception_identity(face_result)
    if category is None:
        category = face_category
    if floor is None and category in ("vip", "socio", "invitado"):
        floor = 3
    store = VisitStore()
    turns = store.visit_plan(category)
    plans = [plan_turn(t, store) for t in turns]
    logdir = Path(logdir or (out.parent if out else Path("out_reto2/logs")))
    logger = RunLogger(logdir, person or Path(str(photo)).name)
    for p in plans:                        # sonda de stroke c12: articulación dominante + reposo
        probe = store.stroke_probe(p.turn.emote) if p.preset is not None else None
        if probe is not None:
            logger.set_probe(p.turn.turn_id, probe[0], probe[1])
    logger.event(event="face_id", photo=str(photo), **{k: face_result.get(k) for k in
                 ("status", "faces", "top1_id", "top1_score", "margin", "latency_s")})
    logger.event(event="identity", category=category, floor=floor,
                 display_name=profile.get("display_name"), rank=profile.get("rank_code"),
                 direction=ROUTE_DIRECTION.get(category),
                 turns=[(p.turn.turn_id, p.turn.emote, p.turn.borrowed) for p in plans])

    audio_segments: list[tuple[Path, float]] = []
    audio_proc: list[subprocess.Popen] = []

    def audio_fn(wav: Path) -> float:
        """Reloj de audio: en directo lanza pw-play y devuelve el monotónico de inicio."""
        if live_audio:
            player = shutil.which("pw-play") or shutil.which("aplay")
            if player:
                audio_proc.append(subprocess.Popen([player, str(wav)], stdin=subprocess.DEVNULL,
                                                   stdout=subprocess.DEVNULL,
                                                   stderr=subprocess.DEVNULL))
        return time.monotonic()

    eng = ShowEngine(CLUB)
    rig = eng.rigs["ultra"]
    logger.attach(eng.model, rig)
    tour = hri_reception("ultra", category, plans, floor=floor, guest=eng.guest,
                         audio_fn=audio_fn, log_event=logger.event, face_status=face_status,
                         label=f"{person or 'huésped'} · {category}")
    eng.start_tour(tour)
    eng.run_for(0.5)

    renderer = None
    writer = None
    cam = mujoco.MjvCamera()
    lookat, dist, azim, elev, _ceiling = C.CAMERAS["Recepción"]
    cam.lookat[:] = lookat
    cam.distance, cam.azimuth, cam.elevation = dist, azim, elev
    if video:
        renderer = mujoco.Renderer(eng.model, height=height, width=width)
        out.parent.mkdir(parents=True, exist_ok=True)
        writer = imageio.get_writer(str(out.with_suffix(".video.mp4")), fps=fps, codec="libx264",
                                    quality=8, macro_block_size=1)
    font, small = _font(max(14, height // 30)), _font(max(11, height // 45))
    steps_per_frame = max(1, int(round(1.0 / (fps * 0.02))))
    subtitle, subtitle_until = "", 0.0
    speech_seen = 0
    frames = 0
    interrupted = ""
    try:
        while eng.data.time < MAX_SIM_SECONDS:
            eng.step_control()
            t = eng.data.time
            step = tour.steps[tour.index] if tour.index < len(tour.steps) else None
            in_hri = isinstance(step, HriTurns) and tour.player is not None and not tour.player.done
            turn_id = ""
            if in_hri and tour.player.current is not None:
                turn_id = tour.player.current.turn.turn_id
            gdist = (float(np.linalg.norm(eng.guest.xy - rig.info.base_pos[:2]))
                     if eng.guest is not None else math.nan)
            logger.row(t, eng, rig, type(step).__name__ if step else "fin", turn_id, in_hri, gdist)
            # subtítulos: frases del motor (Say) y el habla de los turnos HRI
            while speech_seen < len(eng.speech):
                _, _, text = eng.speech[speech_seen]
                speech_seen += 1
                if text:
                    subtitle, subtitle_until = text, t + max(1.5, 0.09 * len(text))
            if in_hri:
                subtitle = tour.status.strip("«»")
                subtitle_until = t + 0.4
            if t > subtitle_until:
                subtitle = ""
            if writer is not None and int(round(t / 0.02)) % steps_per_frame == 0:
                if tour.index >= ROUTE_STEP_INDEX:      # seguir al robot
                    cam.lookat[:] = cam.lookat + 0.25 * (
                        np.array([rig.info.base_pos[0], rig.info.base_pos[1], 0.8]) - cam.lookat)
                    cam.distance, cam.elevation = 4.0, -15.0
                    cam.azimuth = math.degrees(rig.info.yaw)
                renderer.update_scene(eng.data, camera=cam)
                frame = renderer.render()
                top = (f"Reto 2 · Club N · {person or ''} · {profile.get('display_name', '?')} "
                       f"({face_result.get('status', '?')}) · {tour.label} · t={t:.1f}s")
                frame = _caption(frame, top, subtitle, font, small)
                writer.append_data(frame)
                frames += 1
            if tour.done:
                break
        # cola: un par de segundos más de escena
        tail_frames = int(TAIL_SECONDS / 0.02)
        for k in range(tail_frames):
            eng.step_control()
            if writer is not None and k % steps_per_frame == 0:
                renderer.update_scene(eng.data, camera=cam)
                frame = _caption(renderer.render(), f"Reto 2 · fin de la recepción de {person or ''}",
                                 "", font, small)
                writer.append_data(frame)
                frames += 1
        if tour.status == "interrumpida" or rig.state != "activo":
            interrupted = f"tour={tour.status} rig={rig.state} falls={rig.info.falls}"
    finally:
        if writer is not None:
            writer.close()
        if renderer is not None:
            renderer.close()
        for p in audio_proc:
            if p.poll() is None:
                p.terminate()

    # audio: WAV de los turnos (reloj de simulación) + espeak para las frases del motor
    for ev in logger.events:
        if ev.get("event") == "audio_start":
            audio_segments.append((Path(ev["wav"]), float(ev["t_sim"])))
    tmpdir = Path(tempfile.mkdtemp(prefix="reto2_espeak_"))
    try:
        for i, (tsay, _key, text) in enumerate(eng.speech):
            if not text:
                continue
            wav = _espeak_wav(text, tmpdir / f"say{i:02d}.wav")
            if wav is not None:
                audio_segments.append((wav, float(tsay)))
        total_s = eng.data.time
        if video:
            ok = _mux_audio(out.with_suffix(".video.mp4"), audio_segments, total_s, out)
            if ok:
                out.with_suffix(".video.mp4").unlink(missing_ok=True)
            else:
                out.with_suffix(".video.mp4").rename(out)
                logger.event(event="audio_mux_failed", out=str(out))
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    logger.close()
    vram = None
    try:
        vram = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader"],
                              capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    summary = logger.summarize(extra={
        "person": person, "photo": str(photo), "category": category, "floor": floor,
        "face_status": face_result.get("status"), "face_latency_s": face_result.get("latency_s"),
        "identity_expected_ok": (face_category == category),
        "tour_status": tour.status, "interrupted": interrupted,
        "sim_seconds": round(eng.data.time, 2), "frames": frames,
        "wall_seconds": round(time.perf_counter() - t_start, 1),
        "video": str(out) if video and Path(out).is_file() else None,
        "vram_used": vram, "falls_rig": rig.info.falls,
        "speech_turns": [(p.turn.turn_id, p.turn.emote, p.turn.state, p.turn.borrowed)
                         for p in plans],
    })
    return summary


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--photo", type=Path, help="carpeta/foto del huésped (requiere el módulo Face; opcional)")
    ap.add_argument("--person", default="")
    ap.add_argument("--identity", default=None, choices=sorted(load_identities()),
                    help="usa la identidad precomputada de esta persona (sin Face ni foto)")
    ap.add_argument("--rank", default=None, help="rango sintético confirmado: VIP|REGULAR|GUEST|STAFF|EXTERNAL")
    ap.add_argument("--category", default=None, choices=(None, "vip", "socio", "invitado", "personal",
                                                         "externo"))
    ap.add_argument("--floor", type=int, default=None)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--width", type=int, default=960)
    ap.add_argument("--height", type=int, default=540)
    ap.add_argument("--fps", type=int, default=25)
    ap.add_argument("--no-video", action="store_true")
    ap.add_argument("--live-audio", action="store_true")
    ap.add_argument("--logdir", type=Path, default=None)
    ap.add_argument("--all", action="store_true", help="las 6 personas de validación")
    args = ap.parse_args(argv)
    outdir = Path(args.out.parent if args.out else "out_reto2")
    if args.all:
        cases = [argparse.Namespace(photo=(Path(p) if p else None), person=n, category=None,
                                    floor=f, out=outdir / f"{n.lower()}.mp4",
                                    width=args.width, height=args.height, fps=args.fps,
                                    no_video=args.no_video, live_audio=args.live_audio,
                                    logdir=args.logdir, identity=n, rank=None)
                 for n, (p, c, f) in PEOPLE.items()]
    else:
        if not args.identity and args.person in load_identities():
            args.identity = args.person            # persona conocida → datos realnames + identidad
        cases = [args]
    results = []
    for case in cases:
        identity = getattr(case, "identity", None)
        face_result = None
        if getattr(case, "rank", None):
            face_result = rank_identity(case.rank)
        elif identity and case.photo is None:
            face_result = precomputed_identity(identity)
        if identity and _USER_VISITS_ROOT is None:
            use_person_visits(identity)            # paquete realnames versionado de la persona
        summary = run_person(case.photo, case.person, None if getattr(case, "no_video", False)
                             else case.out, category=case.category, floor=case.floor,
                             width=case.width, height=case.height, fps=case.fps,
                             video=not getattr(case, "no_video", False), logdir=case.logdir,
                             face_result=face_result,
                             live_audio=getattr(case, "live_audio", False))
        results.append(summary)
        print(json.dumps({k: summary[k] for k in
                          ("person", "category", "face_status", "tour_status", "falls",
                           "gesture_while_walking", "stroke_abs_err_max_ms", "arm_speed_peak",
                           "sim_seconds", "wall_seconds", "video")}, ensure_ascii=False))
    if args.all:
        outdir = Path(args.out.parent if args.out else "out_reto2")
        (outdir / "validation.json").write_text(json.dumps(results, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
