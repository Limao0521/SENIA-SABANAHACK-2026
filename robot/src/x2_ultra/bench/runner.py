"""Ejecuta los escenarios del benchmark y ensambla las métricas por escenario.

Por cada escenario: arranca el SystemSampler, mide la pared, calcula la energía
(CPU estimada + GPU medida) con bench.power y produce la estimación de lo que
costarían las latencias clave en el Jetson Orin NX del robot real. Guarda las
muestras del sampler en CSV y devuelve un dict serializable para bench.report.
"""
from __future__ import annotations

import csv
import platform
import time
from pathlib import Path

from x2_ultra.bench.power import ORIN_NX_FACTORS, PowerModel, orin_budget_fraction, scale_latency
from x2_ultra.bench.scenarios import Context, Scenario
from x2_ultra.bench.system import SystemSampler

# Claves de latencia que describen COMPUTE (escalables al Orin). El periodo del lazo en
# tiempo real (period_50hz) NO está aquí: es scheduling del SO, no carga computacional.
_CYCLE_KEYS = ("cycle_50hz", "mj_step", "render_720p", "decode_720p_rgb24")


def _find_cycle_summary(metrics: dict) -> tuple[str, dict] | None:
    """Localiza el resumen de latencia principal del escenario (para escalarlo al Orin)."""
    for key in _CYCLE_KEYS:
        if key in metrics and isinstance(metrics[key], dict) and "p99" in metrics[key]:
            return key, metrics[key]
    cats = metrics.get("categories")                         # club_tours: la peor categoría
    if isinstance(cats, dict):
        worst = max(((c, v["cycle_50hz"]) for c, v in cats.items()
                     if isinstance(v, dict) and "p99" in v.get("cycle_50hz", {})),
                    key=lambda kv: kv[1]["mean"], default=None)
        if worst:
            return f"categories.{worst[0]}.cycle_50hz (peor)", worst[1]
    for key, value in metrics.items():                       # primer nivel anidado (p.ej. policy)
        if isinstance(value, dict) and "p99" in value:
            return key, value
    return None


def orin_estimate(metrics: dict, factors: dict | None = None) -> dict:
    """ESTIMACIÓN (no medida) de la latencia clave en el Jetson Orin NX: factor x2.0 (ver power.py)."""
    found = _find_cycle_summary(metrics)
    if found is None:
        return {}
    key, summ = found
    factors = factors or ORIN_NX_FACTORS
    kind = "memory_bound" if key.startswith("decode") else "cpu_bound"
    out = {
        "source_metric": key,
        "factor": factors.get(kind, 2.0),
        "kind": kind,
        "estimated": {p: round(scale_latency(summ[p], kind, factors), 3)
                      for p in ("mean", "p50", "p95", "p99", "max") if p in summ},
    }
    if "mean" in summ:
        if key.startswith(("render", "decode")):
            # Render/vídeo no compiten con el presupuesto de control: su métrica es el FPS.
            out["estimated_fps"] = round(1000.0 / scale_latency(summ["mean"], kind, factors), 1)
        else:
            frac = orin_budget_fraction(summ["mean"], 20.0, kind, factors)
            out["budget_20ms_fraction_estimated"] = round(frac, 3)
            out["fits_50hz_estimated"] = frac < 1.0
    return out


def _write_samples_csv(path: Path, sampler: SystemSampler) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["t_s", "proc_cpu_pct", "proc_rss_mb", "sys_cpu_pct", "sys_avail_mb",
                         "gpu_util_pct", "gpu_power_w", "gpu_temp_c"])
        for s in sampler.samples:
            writer.writerow([f"{s.t:.2f}", f"{s.proc_cpu:.1f}", f"{s.proc_rss_mb:.1f}",
                             f"{s.sys_cpu:.1f}", f"{s.sys_avail_mb:.0f}",
                             "" if s.gpu_util is None else s.gpu_util,
                             "" if s.gpu_power_w is None else s.gpu_power_w,
                             "" if s.gpu_temp_c is None else s.gpu_temp_c])


def run_scenario(scenario: Scenario, ctx: Context, power: PowerModel, gpu: bool = True) -> dict:
    """Ejecuta un escenario con muestreo de sistema y devuelve su resultado completo."""
    print(f"\n=== {scenario.key} ({scenario.reto}) :: {scenario.description}", flush=True)
    sampler = SystemSampler(gpu=gpu)
    t0 = time.perf_counter()
    with sampler:
        payload = scenario.run(ctx)
    wall = time.perf_counter() - t0

    result: dict = {
        "scenario": scenario.key,
        "reto": scenario.reto,
        "description": scenario.description,
        "wall_s": round(wall, 2),
    }
    if "skipped" in payload:
        result["skipped"] = payload["skipped"]
        print(f"    OMITIDO: {payload['skipped']}", flush=True)
        return result

    metrics = payload.get("metrics", {})
    sys_summary = sampler.summary()
    gpu_energy = sys_summary.get("gpu_energy_j_measured") or 0.0
    energy = power.energy_j(wall, sampler.cpu_seconds, gpu_energy)

    result["metrics"] = metrics
    result["unit"] = payload.get("unit", "ms")
    result["notes"] = payload.get("notes", [])
    result["system"] = sys_summary
    result["energy"] = energy
    result["orin_nx_estimate"] = {} if payload.get("no_orin") else orin_estimate(metrics)

    ctx.out_dir.mkdir(parents=True, exist_ok=True)
    _write_samples_csv(ctx.out_dir / f"samples_{scenario.key}.csv", sampler)

    _print_summary(result)
    return result


def _print_summary(result: dict) -> None:
    m = result.get("metrics", {})
    found = _find_cycle_summary(m)
    if found:
        key, summ = found
        line = (f"    {key}: mean {summ['mean']:.2f} ms · p95 {summ['p95']:.2f} · p99 {summ['p99']:.2f}"
                f" · max {summ['max']:.2f}")
        if "overrun_pct" in summ:
            line += f" · overruns {summ['overrun_pct']:.1f}%"
        print(line, flush=True)
    if "rtf" in m:
        print(f"    RTF {m['rtf']}x  (sim {m.get('sim_seconds', '?')} s en {result['wall_s']} s de pared)",
              flush=True)
    if "falls" in m:
        print(f"    caidas: {m['falls']}", flush=True)
    for cat, cv in (m.get("categories") or {}).items():
        print(f"    [{cat}] sim {cv['sim_seconds']}s rtf {cv['rtf']}x done={cv['tour_done']} "
              f"frases={cv['phrases']} caidas={cv['falls']} ciclo mean {cv['cycle_50hz']['mean']:.2f} ms "
              f"p99 {cv['cycle_50hz']['p99']:.2f} ms", flush=True)
    sys_s = result.get("system", {})
    print(f"    CPU proceso {sys_s.get('proc_cpu_pct_mean', '?')}% ({sys_s.get('proc_cpu_cores_mean', '?')} "
          f"nucleos) · RSS {sys_s.get('proc_rss_mb_peak', '?')} MB · CPU sistema "
          f"{sys_s.get('sys_cpu_pct_mean', '?')}%", flush=True)
    en = result.get("energy", {})
    print(f"    energia: CPU {en.get('cpu_energy_j_estimated', '?')} J (est.) + GPU "
          f"{en.get('gpu_energy_j_measured', '?')} J (med.) = {en.get('total_energy_j', '?')} J "
          f"-> {en.get('total_avg_w', '?')} W medios", flush=True)
    orin = result.get("orin_nx_estimate", {})
    if orin.get("estimated"):
        est = orin["estimated"]
        frac = orin.get("budget_20ms_fraction_estimated")
        fps = orin.get("estimated_fps")
        extra = (f" · {frac:.0%} del presupuesto de 20 ms -> {'CABE' if orin.get('fits_50hz_estimated') else 'NO CABE'}"
                 if frac is not None else f" · ~{fps} fps estimados" if fps is not None else "")
        print(f"    Orin NX (est. x{orin['factor']}): p99 {est.get('p99', '?')} ms{extra}", flush=True)


def run_all(scenarios: list[Scenario], ctx: Context, power: PowerModel, gpu: bool = True) -> dict:
    """Corre la selección y devuelve el resultado global (para bench.report)."""
    if platform.system() == "Windows":
        import os
        # app.py fija MUJOCO_GL=egl (Linux); en Windows el rasterizador nativo es wgl.
        if os.environ.get("MUJOCO_GL", "").lower() in ("egl", "osmesa"):
            os.environ["MUJOCO_GL"] = "wgl"
    results = {}
    t0 = time.perf_counter()
    for scenario in scenarios:
        results[scenario.key] = run_scenario(scenario, ctx, power, gpu)
    total_wall = time.perf_counter() - t0
    return {
        "scenarios": results,
        "total_wall_s": round(total_wall, 1),
        "ctx": {"scale": ctx.scale, "quick": ctx.quick, "realtime_s": ctx.realtime_s,
                "out_dir": str(ctx.out_dir)},
    }
