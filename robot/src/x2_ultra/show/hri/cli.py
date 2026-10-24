"""Punto de entrada único del reto 2 (recepción HRI), sin ventana (headless, EGL).

    python -m x2_ultra.show.hri.cli --person Laura --video     # una persona → out_reto2/laura.mp4
    python -m x2_ultra.show.hri.cli --all --video              # las 6 personas + validation.json
    python -m x2_ultra.show.hri.cli --rank VIP                 # rango sintético (sin identidad)
    scripts/run_reto2_hri.sh --person Laura --video            # equival. con el venv del repo

Por defecto usa las identidades precomputadas (``assets/hri/identities.json``, persona → rango;
sin datos biométricos) y los paquetes de visita realnames versionados en ``assets/hri/``. El
reconocimiento facial en vivo es opcional: ``--photo <carpeta>`` con el módulo Face disponible
(``X2_FACE_ROOT``/``X2_FACE_PYTHON``). Salidas: ``out_reto2/<persona>.mp4`` (con ``--video``),
``out_reto2/<Persona>_log.csv`` (50 Hz), ``_events.jsonl``, ``_summary.json``.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from x2_ultra.show.hri import render
from x2_ultra.show.hri.data import load_identities


def main(argv=None) -> None:
    names = sorted(load_identities())
    ap = argparse.ArgumentParser(prog="python -m x2_ultra.show.hri.cli", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    who = ap.add_mutually_exclusive_group(required=True)
    who.add_argument("--person", choices=names, help="persona de validación (identidad precomputada)")
    who.add_argument("--all", action="store_true", help="las 6 personas de validación")
    who.add_argument("--rank", help="rango sintético confirmado: VIP|REGULAR|GUEST|STAFF|EXTERNAL")
    ap.add_argument("--video", action="store_true", help="renderiza MP4 (por defecto solo métricas)")
    ap.add_argument("--photo", type=Path, help="carpeta/foto del huésped: identificación en vivo (módulo Face, opcional)")
    ap.add_argument("--out-dir", type=Path, default=Path("out_reto2"))
    ap.add_argument("--logdir", type=Path, default=None)
    ap.add_argument("--width", type=int, default=960)
    ap.add_argument("--height", type=int, default=540)
    ap.add_argument("--fps", type=int, default=25)
    ap.add_argument("--live-audio", action="store_true", help="además reproduce el audio por pw-play/aplay")
    args = ap.parse_args(argv)

    # render.main usa --out (archivo); el directorio de salidas sale de su .parent.
    fwd: list[str] = ["--width", str(args.width), "--height", str(args.height),
                      "--fps", str(args.fps)]
    if args.all:
        fwd += ["--all", "--out", str(args.out_dir / "todas.mp4")]
    elif args.person:
        name = args.person
        fwd += ["--person", name, "--identity", name,
                "--out", str(args.out_dir / f"{name.lower()}.mp4")]
    else:
        fwd += ["--rank", args.rank, "--out", str(args.out_dir / f"{args.rank.lower()}.mp4")]
    if args.photo:
        fwd += ["--photo", str(args.photo)]
    if not args.video:
        fwd.append("--no-video")
    if args.logdir:
        fwd += ["--logdir", str(args.logdir)]
    if args.live_audio:
        fwd.append("--live-audio")
    args.out_dir.mkdir(parents=True, exist_ok=True)
    render.main(fwd)


if __name__ == "__main__":
    main()
