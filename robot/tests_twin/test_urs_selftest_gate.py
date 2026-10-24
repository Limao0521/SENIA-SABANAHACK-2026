"""Criterios de aceptación de la marcha URS (sin arrancar la física)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "official_sim"))
from urs_selftest import acceptance


def passing_report():
    return {
        "pose": "al_frente", "pose_durante": None,
        "urs_aceptado": True, "fuente_registrada": True,
        "muestras_modo_mc_privado_andando": 30,
        "modos_mc_privados_andando": ["STAND_DEFAULT", "LOCOMOTION_DEFAULT"],
        "muestras_odom_andando": 200, "edad_odom_inicio_s": 0.005, "edad_odom_fin_s": 0.005,
        "duracion_orden_s": 5.001, "segundos_solicitados": 5.0,
        "factor_tiempo_real": 0.99, "frecuencia_ordenes_hz": 50.0,
        "intervalo_max_orden_s": 0.025,
        "caidas": 0, "altura_min_pelvis_m": 0.62, "inclinacion_max_grados": 8.0,
        "error_brazos_max_andando_rad": 0.04, "error_manos_max_andando_rad": 0.01,
        "avance_durante_orden_m": 1.8, "desplazamiento_post_parada_m": 1.3,
    }


def test_advance_uses_commanded_window_not_post_stop_drift():
    report = passing_report()
    report["avance_durante_orden_m"] = 1.49
    assert not acceptance(report, standard_forward=True, needs_rest=True,
                          rest_reference=2.0, expected_mode=None)["avance_minimo"]
    report["avance_durante_orden_m"] = 1.8
    assert all(acceptance(report, standard_forward=True, needs_rest=True,
                          rest_reference=2.0, expected_mode="LOCOMOTION_DEFAULT").values())


def test_rest_reference_is_required_for_non_neutral_pose():
    report = passing_report()
    checks = acceptance(report, standard_forward=True, needs_rest=True,
                        rest_reference=None, expected_mode=None)
    assert not checks["referencia_reposo_disponible"]
    assert not checks["avance_relativo_reposo"]
    assert not acceptance(report, standard_forward=True, needs_rest=True,
                          rest_reference=3.0, expected_mode=None)["avance_relativo_reposo"]


def test_pose_change_requires_post_settling_tracking():
    report = passing_report()
    report.update(pose_durante="arriba", cambio_pose_en_s=2.5,
                  muestras_brazos_tras_cambio=0, error_brazos_tras_cambio_rad=None)
    assert not acceptance(report, standard_forward=True, needs_rest=True,
                          rest_reference=2.0, expected_mode=None)["seguimiento_pose_durante"]
    report.update(muestras_brazos_tras_cambio=32, error_brazos_tras_cambio_rad=0.09)
    assert all(acceptance(report, standard_forward=True, needs_rest=True,
                          rest_reference=2.0, expected_mode=None).values())


def test_missing_private_mode_and_stale_odom_fail():
    report = passing_report()
    report.update(muestras_modo_mc_privado_andando=0, edad_odom_fin_s=0.3)
    checks = acceptance(report, standard_forward=True, needs_rest=True,
                        rest_reference=2.0, expected_mode=None)
    assert not checks["modo_mc_privado_recibido"]
    assert not checks["odometria_fresca"]


def test_performance_metrics_are_reported_but_not_undocumented_gates():
    report = passing_report()
    report.update(factor_tiempo_real=0.8, frecuencia_ordenes_hz=20.0,
                  intervalo_max_orden_s=0.3)
    checks = acceptance(report, standard_forward=True, needs_rest=True,
                        rest_reference=2.0, expected_mode=None)
    assert all(checks.values())
