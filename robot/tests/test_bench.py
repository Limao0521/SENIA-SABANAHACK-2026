"""Tests rápidos del harness de benchmark (sin escenarios de física: esos van con --quick)."""
from __future__ import annotations

import json

import pytest

from x2_ultra.bench.power import PowerModel, orin_budget_fraction, scale_latency
from x2_ultra.bench.runner import orin_estimate
from x2_ultra.bench.stats import LatencyStats
from x2_ultra.bench.system import SystemSampler


def test_latency_stats_percentiles_and_overruns():
    st = LatencyStats(budget=0.020)
    st.extend([0.010] * 90 + [0.030] * 10)          # 10% de overruns de 30 ms
    s = st.summary_ms()
    assert s["n"] == 100
    assert s["p50"] == pytest.approx(10.0)
    assert s["p95"] == pytest.approx(30.0)
    assert s["max"] == pytest.approx(30.0)
    assert s["overruns"] == 10
    assert s["overrun_pct"] == pytest.approx(10.0)


def test_latency_stats_empty():
    assert LatencyStats().summary_ms() == {}


def test_power_model_energy_is_linear_in_busy_cores():
    pm = PowerModel(p_idle_w=12.0, p_tdp_w=45.0, n_logical=16)
    idle = pm.energy_j(wall_s=10.0, cpu_busy_s=0.0)
    one_core = pm.energy_j(wall_s=10.0, cpu_busy_s=10.0)
    full = pm.energy_j(wall_s=10.0, cpu_busy_s=160.0)
    assert idle["cpu_avg_w_estimated"] == pytest.approx(12.0)
    assert one_core["cpu_avg_w_estimated"] == pytest.approx(12.0 + 33.0 / 16, abs=0.05)  # redondeo 0.1 W
    assert full["cpu_avg_w_estimated"] == pytest.approx(45.0)
    # cpu_busy mayor que wall*n no debe desbordar el TDP
    over = pm.energy_j(wall_s=10.0, cpu_busy_s=1000.0)
    assert over["cpu_avg_w_estimated"] == pytest.approx(45.0)


def test_orin_scaling_and_budget():
    assert scale_latency(10.0, "cpu_bound") == pytest.approx(20.0)
    assert scale_latency(10.0, "memory_bound") == pytest.approx(9.0)
    # un ciclo de 8 ms en x86 -> 16 ms en Orin -> 80% del presupuesto de 20 ms
    assert orin_budget_fraction(8.0) == pytest.approx(0.8)
    est = orin_estimate({"cycle_50hz": {"mean": 5.0, "p50": 5.0, "p95": 7.0, "p99": 8.0, "max": 9.0}})
    assert est["source_metric"] == "cycle_50hz"
    assert est["estimated"]["p99"] == pytest.approx(16.0)
    assert est["fits_50hz_estimated"] is True


def test_orin_estimate_picks_cycle_key_first():
    est = orin_estimate({"otros": {"mean": 1.0, "p99": 2.0},
                         "cycle_50hz": {"mean": 6.0, "p99": 9.0}})
    assert est["source_metric"] == "cycle_50hz"


def test_system_sampler_collects_samples():
    import time as _time
    with SystemSampler(interval=0.02, gpu=False) as sampler:
        _time.sleep(0.1)               # ventana suficiente para >=2 muestras a 20 ms
        sum(range(200_000))
    assert len(sampler.samples) >= 2
    assert sampler.cpu_seconds >= 0.0
    summary = sampler.summary()
    assert summary["proc_rss_mb_peak"] > 0
    assert "gpu_power_w_mean" not in summary


def test_scenarios_registry_unique_and_callable():
    from x2_ultra.bench.scenarios import BY_KEY, SCENARIOS
    keys = [s.key for s in SCENARIOS]
    assert len(keys) == len(set(keys)) == len(BY_KEY)
    for s in SCENARIOS:
        assert callable(s.run)
        assert s.reto in ("componente", "reto1", "reto2")


def test_report_roundtrip(tmp_path):
    from x2_ultra.bench.report import build_report, render_markdown, write_report
    run = {"scenarios": {"policy_inference": {
        "scenario": "policy_inference", "reto": "componente", "description": "d",
        "wall_s": 1.0, "metrics": {"brazos_libres": {"n": 10, "mean": 0.03, "p50": 0.03,
                                                     "p95": 0.04, "p99": 0.05, "max": 0.06}},
        "system": {"proc_cpu_pct_mean": 100.0, "proc_cpu_cores_mean": 1.0,
                   "proc_rss_mb_peak": 200.0, "sys_cpu_pct_mean": 10.0},
        "energy": {"cpu_energy_j_estimated": 20.0, "gpu_energy_j_measured": 0.0,
                   "total_energy_j": 20.0, "total_avg_w": 20.0, "wall_s": 1.0, "method": "m"},
        "orin_nx_estimate": orin_estimate({"brazos_libres": {"mean": 0.03, "p50": 0.03,
                                                             "p95": 0.04, "p99": 0.05, "max": 0.06}}),
        "notes": ["n"]}},
        "total_wall_s": 1.0,
        "ctx": {"scale": 1.0, "quick": True, "realtime_s": 30.0, "out_dir": str(tmp_path)}}
    report = build_report(run)
    md = render_markdown(report)
    assert "policy_inference" in md and "Orin NX" in md
    j, m = write_report(report, tmp_path)
    assert json.loads(j.read_text(encoding="utf-8"))["scenarios"]["policy_inference"]["wall_s"] == 1.0
    assert m.read_text(encoding="utf-8").startswith("# Benchmark")


def test_deployment_build_render_roundtrip(tmp_path):
    from x2_ultra.bench.deployment import build_deployment, render_deployment_md, write_deployment
    dep = build_deployment(None)                       # sin bench citado
    assert dep["elements"] and dep["veredicto"]["hoy"].startswith("NO")
    totals = [c["total_gb"] for c in dep["pc2_memory"]["configs"]]
    assert totals == sorted(totals)                    # configs A ≤ B ≤ C
    assert dep["pc2_memory"]["configs"][0]["veredicto"] == "CABE"
    md = render_deployment_md(dep)
    assert "sin corrida de bench citada" in md and "Checklist" in md

    bench = {"ctx": {"out_dir": "runs/x"}, "environment": {"git": {"commit": "abc1234"}},
             "scenarios": {"club_tours": {
                 "metrics": {"categories": {"vip": {"cycle_50hz": {"mean": 2.0, "p99": 3.3}}}},
                 "orin_nx_estimate": {"estimated": {"mean": 4.0, "p99": 6.6},
                                      "budget_20ms_fraction_estimated": 0.2}}}}
    dep2 = build_deployment(bench)
    assert dep2["bench"]["club_tours"]["orin_budget_frac"] == 0.2
    md2 = render_deployment_md(dep2)
    assert "abc1234" in md2 and "20% del ciclo de 20 ms" in md2
    j, m = write_deployment(dep2, tmp_path)
    assert json.loads(j.read_text(encoding="utf-8"))["pc2_memory"]["ram_gb"] == 16.0
    assert m.read_text(encoding="utf-8").startswith("# Viabilidad")
