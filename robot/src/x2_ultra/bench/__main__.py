"""CLI del benchmark de consumo: ``python -m x2_ultra.bench`` (o ``x2-bench``).

Ejemplos:
    x2-bench --list
    x2-bench --quick                        # vuelta corta a todos los escenarios
    x2-bench --only stage_walk,club_tours   # escenarios sueltos
    x2-bench --cores 8                      # emula los 8 núcleos del Orin NX
    x2-bench --realtime-s 60 --output runs/bench_largo
"""
from __future__ import annotations

import argparse
import os
import platform
import sys
from datetime import datetime
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    # Consola Windows en cp1252: los acentos del informe no caben; forzar UTF-8.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    # En Windows el rasterizador offscreen nativo es wgl (app.py fija egl, que es de Linux).
    if platform.system() == "Windows" and os.environ.get("MUJOCO_GL", "").lower() in ("egl", "osmesa"):
        os.environ["MUJOCO_GL"] = "wgl"

    from x2_ultra.bench.power import PowerModel
    from x2_ultra.bench.report import build_report, write_report
    from x2_ultra.bench.runner import run_all
    from x2_ultra.bench.scenarios import BY_KEY, SCENARIOS, Context
    from x2_ultra.bench.system import limit_cores

    parser = argparse.ArgumentParser(prog="x2-bench", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--list", action="store_true", help="lista los escenarios y sale")
    parser.add_argument("--only", metavar="K1,K2", help="solo estos escenarios (claves separadas por comas)")
    parser.add_argument("--quick", action="store_true", help="duraciones reducidas (vuelta de reconocimiento)")
    parser.add_argument("--scale", type=float, default=1.0, help="multiplica duraciones e iteraciones")
    parser.add_argument("--realtime-s", type=float, default=30.0, help="segundos del escenario realtime_50hz")
    parser.add_argument("--cores", type=int, default=None,
                        help="fija la afinidad a N núcleos lógicos (8 emula el Orin NX)")
    parser.add_argument("--no-gpu", action="store_true", help="no consultar nvidia-smi")
    parser.add_argument("--p-idle", type=float, default=12.0, help="potencia idle estimada del equipo [W]")
    parser.add_argument("--p-tdp", type=float, default=45.0, help="TDP del paquete CPU [W]")
    parser.add_argument("--output", metavar="DIR", default=None,
                        help="carpeta de salida (por defecto runs/bench_<fecha-hora>)")
    parser.add_argument("--deployment", nargs="?", const="latest", default=None, metavar="REPORT_JSON",
                        help="análisis de viabilidad de despliegue a bordo del robot (no corre escenarios): "
                             "cita el report.json indicado o, por omisión, el runs/bench_* más reciente")
    args = parser.parse_args(argv)

    if args.list:
        print(f"{'clave':18s} {'reto':11s} descripción")
        for s in SCENARIOS:
            print(f"{s.key:18s} {s.reto:11s} {s.description}")
        return 0

    if args.deployment is not None:
        import json

        from x2_ultra.bench.deployment import build_deployment, find_latest_bench_report, write_deployment
        src = find_latest_bench_report() if args.deployment == "latest" else Path(args.deployment)
        bench_report = json.loads(src.read_text(encoding="utf-8")) if src and src.is_file() else None
        if src:
            print(f"citando corrida: {src}")
        else:
            print("sin corrida de bench que citar (solo documentación y medidas HRIEGO)")
        dep = build_deployment(bench_report)
        out_dir = Path(args.output) if args.output else Path("runs") / f"deployment_{datetime.now():%Y%m%d_%H%M%S}"
        json_path, md_path = write_deployment(dep, out_dir)
        print(f"\nanálisis: {md_path}\ndatos:   {json_path}")
        return 0

    selected = SCENARIOS
    if args.only:
        keys = [k.strip() for k in args.only.split(",") if k.strip()]
        unknown = [k for k in keys if k not in BY_KEY]
        if unknown:
            parser.error(f"escenarios desconocidos: {unknown}; ver --list")
        selected = [BY_KEY[k] for k in keys]

    out_dir = Path(args.output) if args.output else Path("runs") / f"bench_{datetime.now():%Y%m%d_%H%M%S}"
    ctx = Context(scale=args.scale, quick=args.quick, realtime_s=args.realtime_s, out_dir=out_dir)
    power = PowerModel(p_idle_w=args.p_idle, p_tdp_w=args.p_tdp, n_logical=os.cpu_count() or 16)
    used_cores = limit_cores(args.cores)
    if used_cores:
        print(f"afinidad fijada a {used_cores} núcleos lógicos")
    power.n_logical = used_cores or power.n_logical

    print(f"x2-bench · {len(selected)} escenarios · quick={args.quick} scale={args.scale} · salida {out_dir}")
    run = run_all(selected, ctx, power, gpu=not args.no_gpu)
    report = build_report(run)
    json_path, md_path = write_report(report, out_dir)
    print(f"\ninforme: {md_path}\ndatos:   {json_path}\nmuestras CSV en {out_dir}/samples_*.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
