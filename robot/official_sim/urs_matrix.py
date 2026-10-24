#!/usr/bin/env python3
"""Compara perfiles MC del gemelo con la misma matriz de marcha y brazos.

Se ejecuta en el host, con Docker y ``official_sim/twin.sh`` disponibles. Cada
corrida reinicia el robot con pórtico, pide STAND_DEFAULT, lo suelta y ejecuta
``urs-test``. El JSON se guarda tras cada corrida para conservar los resultados
si una prueba posterior se interrumpe.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TWIN = REPO / "official_sim" / "twin.sh"
POSES = ("reposo", "al_frente", "saludar", "arriba", "cargar")
TRANSITIONS = (("reposo", "al_frente"), ("al_frente", "arriba"), ("arriba", "cargar"))


def command(*args: str, timeout: int = 360) -> dict:
    started = time.monotonic()
    try:
        proc = subprocess.run([str(TWIN), *args], cwd=REPO, text=True, capture_output=True,
                              timeout=timeout, check=False)
        return {"cmd": [*args], "exit_code": proc.returncode, "stdout": proc.stdout[-8000:],
                "stderr": proc.stderr[-4000:], "duracion_s": round(time.monotonic() - started, 2)}
    except subprocess.TimeoutExpired as exc:
        return {"cmd": [*args], "exit_code": 124, "stdout": str(exc.stdout or "")[-8000:],
                "stderr": str(exc.stderr or "")[-4000:], "duracion_s": round(time.monotonic() - started, 2),
                "error": "timeout"}


def json_report(result: dict) -> dict | None:
    for line in reversed(result["stdout"].splitlines()):
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    return None


def json_document(result: dict) -> dict | None:
    """Read the first JSON object even when profile-info appends a mode line."""
    output = result["stdout"]
    first = output.find("{")
    if first < 0:
        return None
    try:
        value, _ = json.JSONDecoder().raw_decode(output[first:])
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def save(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    temp.replace(path)


def prepare_robot() -> list[dict]:
    steps = []
    for name in ("reset", "stand", "suelta"):
        result = command(name)
        steps.append(result)
        if result["exit_code"]:
            break
    if len(steps) == 3 and not steps[-1]["exit_code"]:
        time.sleep(2.0)
    return steps


def trial(pose: str, hand: str, seconds: float, vx: float, reference: float | None,
          during: str | None = None) -> dict:
    preparation = prepare_robot()
    if len(preparation) != 3 or any(step["exit_code"] for step in preparation):
        return {"pose": pose, "pose_durante": during, "preparacion": preparation,
                "aprobado": False, "error": "reset/stand/suelta falló"}
    args = ["urs-test", "--pose", pose, "--mano", hand, "--segundos", str(seconds),
            "--vx", str(vx), "--teclas", "w"]
    if reference is not None and (pose != "reposo" or during is not None):
        args += ["--referencia-reposo-m", str(reference)]
    if during:
        args += ["--pose-durante", during]
    result = command(*args, timeout=max(90, int(seconds) + 45))
    metrics = json_report(result)
    return {"pose": pose, "pose_durante": during, "mano": hand, "preparacion": preparation,
            "comando": result, "metricas": metrics,
            "aprobado": result["exit_code"] == 0 and bool(metrics and metrics.get("aprobado"))}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profiles", nargs="+", choices=("P0", "P1", "P2"), default=["P0", "P1", "P2"],
                    help="orden de perfiles MC; detiene la matriz al encontrar el primero que aprueba")
    ap.add_argument("--repeticiones", type=int, default=3)
    ap.add_argument("--segundos", type=float, default=5.0)
    ap.add_argument("--vx", type=float, default=0.4)
    ap.add_argument("--output", type=Path, default=None)
    args = ap.parse_args()
    if args.repeticiones < 1 or args.segundos <= 0 or args.vx <= 0:
        ap.error("repeticiones, segundos y vx deben ser positivos")
    if not math.isclose(args.segundos, 5.0, abs_tol=1e-9) or not math.isclose(args.vx, 0.4, abs_tol=1e-9):
        ap.error("la matriz de aceptación exige --segundos 5 y --vx 0.4")
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = args.output or REPO / "logs" / f"urs_matrix_{stamp}.json"
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True).stdout.strip()
    report = {"commit_inicial": commit, "fecha_utc": stamp, "configuracion": vars(args) | {"output": str(path)},
              "profiles": [], "perfil_elegido": None}
    save(path, report)
    up = command("up")
    report["contenedor"] = up
    save(path, report)
    if up["exit_code"]:
        print(f"No se pudo arrancar el contenedor; informe: {path}", file=sys.stderr)
        return 2

    for profile in args.profiles:
        profile_report = {"perfil": profile, "corridas": [], "aprobado": False}
        report["profiles"].append(profile_report)
        start = command("start", "--mc-profile", profile, timeout=180)
        profile_report["arranque"] = start
        save(path, report)
        if start["exit_code"]:
            profile_report["error"] = "no arrancó el perfil"
            save(path, report)
            continue
        for repetition in range(1, args.repeticiones + 1):
            baseline = trial("reposo", "agarre", args.segundos, args.vx, None)
            baseline["repeticion"] = repetition
            profile_report["corridas"].append(baseline)
            save(path, report)
            if baseline.get("error"):
                profile_report["error"] = baseline["error"]
                profile_report["parada_tras_error"] = command("stop", timeout=30)
                save(path, report)
                break
            if repetition == 1:
                info_result = command("profile-info", timeout=20)
                profile_report["profile_info"] = info_result
                info = json_document(info_result)
                profile_report["profile_config"] = info
                profile_report["configuracion_privada_verificada"] = bool(
                    info_result["exit_code"] == 0 and info and info.get("profile") == profile
                    and info.get("generated") and info.get("mc_loaded_private_config")
                    and info.get("locomotion_runner_initialized")
                )
                save(path, report)
                if not profile_report["configuracion_privada_verificada"]:
                    profile_report["error"] = "MC no cargó el perfil privado previsto"
                    break
            metrics = baseline.get("metricas") or {}
            measured_advance = metrics.get("avance_durante_orden_m")
            reference = (measured_advance if isinstance(measured_advance, (int, float))
                         and math.isfinite(measured_advance) and measured_advance > 0
                         and metrics.get("muestras_odom_andando", 0) >= 2 else None)
            for pose in POSES[1:]:
                result = trial(pose, "agarre", args.segundos, args.vx, reference)
                result["repeticion"] = repetition
                profile_report["corridas"].append(result)
                save(path, report)
                if result.get("error"):
                    profile_report["error"] = result["error"]
                    break
            if profile_report.get("error"):
                profile_report["parada_tras_error"] = command("stop", timeout=30)
                save(path, report)
                break
            for initial, after in TRANSITIONS:
                result = trial(initial, "agarre", args.segundos, args.vx, reference, during=after)
                result["repeticion"] = repetition
                profile_report["corridas"].append(result)
                save(path, report)
                if result.get("error"):
                    profile_report["error"] = result["error"]
                    break
            if profile_report.get("error"):
                profile_report["parada_tras_error"] = command("stop", timeout=30)
                save(path, report)
                break
        profile_report["aprobado"] = bool(profile_report.get("configuracion_privada_verificada")) and bool(profile_report["corridas"]) and all(
            item["aprobado"] for item in profile_report["corridas"])
        save(path, report)
        if profile_report["aprobado"]:
            report["perfil_elegido"] = profile
            break
    if report["perfil_elegido"] is None:
        report["restauracion"] = command("start", "--mc-profile", "P0", timeout=180)
    save(path, report)
    print(json.dumps({"informe": str(path), "perfil_elegido": report["perfil_elegido"],
                      "aprobado": report["perfil_elegido"] is not None}, ensure_ascii=False))
    return 0 if report["perfil_elegido"] is not None else 1


if __name__ == "__main__":
    raise SystemExit(main())
