"""Informe del benchmark: JSON completo + Markdown legible en runs/bench_<fecha>/."""
from __future__ import annotations

import json
import subprocess
from datetime import datetime
from pathlib import Path

from x2_ultra.bench.power import robot_reference
from x2_ultra.bench.system import environment


def _git_info() -> dict:
    try:
        branch = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"],
                                capture_output=True, text=True, timeout=5).stdout.strip()
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                                capture_output=True, text=True, timeout=5).stdout.strip()
        return {"branch": branch, "commit": commit}
    except (OSError, subprocess.TimeoutExpired):
        return {}


def build_report(run: dict) -> dict:
    env = environment()
    env["git"] = _git_info()
    return {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "environment": env,
        "robot_reference": robot_reference(),
        **run,
    }


def _fmt(v, nd=2):
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.{nd}f}"
    return str(v)


def render_markdown(report: dict) -> str:
    env = report["environment"]
    ref = report["robot_reference"]
    git = env.get("git", {})
    lines = [
        "# Benchmark de consumo — X2 Ultra emulado en PC",
        "",
        f"Generado: {report['generated']} · rama `{git.get('branch', '?')}` commit `{git.get('commit', '?')}`"
        f" · escala {report['ctx']['scale']} · quick={report['ctx']['quick']}",
        "",
        "## Equipo (emulador)",
        "",
        f"- **SO:** {env['os']} · **arch:** {env['arch']}",
        f"- **CPU:** {env['cpu']} ({env['logical_cores']} hilos) · **RAM:** {env['ram_gb']} GB",
        f"- **GPU:** {env['gpu'] or 'sin GPU dedicada'}",
        f"- **Python:** {env['python']} · MuJoCo {env['mujoco']} · onnxruntime {env['onnxruntime']}"
        f" · numpy {env['numpy']}",
        "",
        "## Robot real (referencia)",
        "",
        f"- Batería **{ref['x2_battery_wh']:.0f} Wh**, autonomía **~{ref['x2_walk_autonomy_h']:.0f} h**"
        f" andando a 0,5 m/s → **~{ref['x2_walk_avg_power_w']:.0f} W medios totales**"
        " (casi todo actuadores; el compute es una fracción).",
        f"- Compute: PC1 RK3588s (MC, locomoción 50 Hz) + PC2 **Jetson Orin NX 16 GB** (HRI),"
        f" modos {ref['orin_nx_power_modes_w']['min']:.0f}–{ref['orin_nx_power_modes_w']['maxn']:.0f} W.",
        f"- Factores de estimación x86→Orin NX: {ref['orin_nx_factors']} — {ref['factor_source']}",
        f"- {ref['note_actuators']}",
        "",
        "## Resultados por escenario",
        "",
    ]
    for key, res in report["scenarios"].items():
        lines.append(f"### `{key}` — {res['description']} ({res['reto']})")
        lines.append("")
        if "skipped" in res:
            lines += [f"**OMITIDO:** {res['skipped']}", ""]
            continue
        m = res.get("metrics", {})
        sys_s = res.get("system", {})
        en = res.get("energy", {})
        orin = res.get("orin_nx_estimate", {})

        # Tabla de latencias principales
        lat_rows = []
        for mk, mv in m.items():
            if isinstance(mv, dict) and "p99" in mv:
                lat_rows.append((mk, mv))
            elif mk == "categories" and isinstance(mv, dict):
                for cat, cv in mv.items():
                    if isinstance(cv, dict) and "cycle_50hz" in cv:
                        lat_rows.append((f"categories.{cat}.cycle_50hz", cv["cycle_50hz"]))
        if lat_rows:
            lines += [
                "| Métrica | n | mean | p50 | p95 | p99 | max | overruns |",
                "|---|---|---|---|---|---|---|---|",
            ]
            for name, s in lat_rows:
                lines.append(
                    f"| {name} (ms) | {_fmt(s.get('n'), 0)} | {_fmt(s.get('mean'))} | {_fmt(s.get('p50'))}"
                    f" | {_fmt(s.get('p95'))} | {_fmt(s.get('p99'))} | {_fmt(s.get('max'))}"
                    f" | {_fmt(s.get('overrun_pct'), 1)}% |")
            lines.append("")

        # Otras métricas escalares
        others = {k: v for k, v in m.items()
                  if not isinstance(v, dict) or (k != "categories" and "p99" not in v)}
        others = {k: v for k, v in others.items() if not isinstance(v, dict)}
        if others:
            lines.append("**Métricas:** " + " · ".join(f"{k}={_fmt(v)}" for k, v in others.items()))
            lines.append("")

        lines += [
            "**Recursos:** "
            f"CPU proceso {_fmt(sys_s.get('proc_cpu_pct_mean'), 0)}% "
            f"({_fmt(sys_s.get('proc_cpu_cores_mean'))} núcleos) pico {_fmt(sys_s.get('proc_cpu_pct_peak'), 0)}%"
            f" · RSS pico {_fmt(sys_s.get('proc_rss_mb_peak'), 0)} MB"
            f" · CPU sistema {_fmt(sys_s.get('sys_cpu_pct_mean'), 0)}%"
            + (f" · GPU {_fmt(sys_s.get('gpu_power_w_mean'), 1)} W medios"
               f" (util {_fmt(sys_s.get('gpu_util_pct_mean'), 0)}%, {_fmt(sys_s.get('gpu_temp_c_peak'), 0)} °C pico)"
               if sys_s.get("gpu_power_w_mean") is not None else " · sin GPU"),
            "",
            "**Energía:** "
            f"CPU {_fmt(en.get('cpu_energy_j_estimated'), 0)} J *(estimada: {en.get('method', '')})*"
            f" + GPU {_fmt(en.get('gpu_energy_j_measured'), 0)} J *(medida)*"
            f" = **{_fmt(en.get('total_energy_j'), 0)} J** → {_fmt(en.get('total_avg_w'), 1)} W medios"
            f" durante {_fmt(en.get('wall_s'), 0)} s de pared.",
            "",
        ]
        if orin.get("estimated"):
            est = orin["estimated"]
            frac = orin.get("budget_20ms_fraction_estimated")
            fps = orin.get("estimated_fps")
            verdict = "**cabe** en el ciclo de 20 ms" if orin.get("fits_50hz_estimated") else "**NO cabe** en 20 ms"
            extra = (f" → {_fmt(frac * 100, 0)}% del presupuesto de 50 Hz ({verdict})" if frac is not None
                     else f" → ~{_fmt(fps, 1)} fps estimados" if fps is not None else "")
            lines += [
                f"**Jetson Orin NX (ESTIMADO, ×{orin['factor']} {orin['kind']}):**"
                f" mean {_fmt(est.get('mean'))} ms · p99 {_fmt(est.get('p99'))} ms · max {_fmt(est.get('max'))} ms"
                + extra,
                "",
            ]
        for note in res.get("notes", []):
            lines.append(f"> {note}")
        if res.get("notes"):
            lines.append("")

    lines += [
        "## Metodología y límites",
        "",
        "- **Latencias:** reloj de pared (`time.perf_counter`) alrededor de cada ciclo; percentiles exactos.",
        "- **Consumo CPU:** ESTIMADO con modelo lineal `P_idle + (P_tdp−P_idle)·cores_ocupados/n`"
        " (Windows no expone RAPL). **GPU:** MEDIDO con `nvidia-smi power.draw` integrado.",
        "- **Jitter en tiempo real:** con `timeBeginPeriod(1)` en Windows; sin ello el sleep se cuantiza"
        " a ~15,6 ms y falsearía la medida.",
        "- **Estimaciones Orin NX:** factores de Geekbench 5 single-core públicos (5800H ≈ 1,9–2,0× A78AE);"
        " son orientativas — la validación real exige correr el bench en el Orin (`--target` documentado en"
        " docs/12-benchmark-consumo.md).",
        "- **HRI (Face/LLM/TTS):** no emulable en este PC Windows (V4L2, select sobre pipes, docker compose"
        " x86-cuda). Números de referencia medidos en el stack HRI sobre el PC de desarrollo x86 + gemelo"
        " (el Orin NX NO está medido — HRIEGO_gesture/README.md §9):"
        " `HRIEGO_gesture/validation/latency_budget.json` (LLM p50 1,44 s; TTS RTF p50 0,162;"
        " alineación CTC p50 86 ms) y `HRIEGO_gesture/visits_v2/resource_profile.json`"
        " (71 tok/s generación; contenedor x2-llm: 5,7 GB RAM pico, GPU 99%).",
        "- El robot real reparte la carga: MC a 50 Hz en el **RK3588s (PC1)** y HRI en el **Orin NX (PC2)**;"
        " este benchmark emula ambos en un solo proceso x86 — es una cota superior conservadora por núcleo.",
        "",
    ]
    return "\n".join(lines)


def write_report(report: dict, out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "report.json"
    md_path = out_dir / "report.md"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, md_path
