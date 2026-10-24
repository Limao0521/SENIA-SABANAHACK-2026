"""Prueba sin ventana de la app de control sobre el gemelo.

Prepara el robot, pone los brazos al frente, cierra las manos y mantiene W 4 s.
Emite medidas y un fallo de proceso si la ruta completa de la GUI no responde.
"""
import json
import math
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MUJOCO_GL", "egl")
import numpy as np
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication

from x2_ultra.app.main import MainWindow
from x2_ultra.control.poses import ARM_POSES
from x2_ultra.robot import joints as J
from x2_ultra.robot import omnihand as H
from x2_ultra.twin.client import C_URS, TwinClient

out = sys.argv[1] if len(sys.argv) > 1 else "/workspace/x2_ultra/logs/twin_gui.png"
app = QApplication([])
eng = TwinClient(scene="mesa")
win = MainWindow(eng)
win.isActiveWindow = lambda: True
win.show()
eng.start()


def pump(seconds, sample=None):
    t0 = time.monotonic()
    while time.monotonic() - t0 < seconds:
        app.processEvents()
        win.tick()
        if sample is not None:
            sample()
        time.sleep(0.016)


def key(k, press, text=""):
    t = QEvent.Type.KeyPress if press else QEvent.Type.KeyRelease
    win.handle_key(QKeyEvent(t, k, Qt.KeyboardModifier.NoModifier, text), press)


t0 = time.monotonic()
while not eng.urs and time.monotonic() - t0 < 300:
    pump(0.5)
report = {"urs": eng.urs, "modo": eng.info.state, "preparacion_s": round(time.monotonic() - t0, 1)}
key(Qt.Key.Key_2, True, "2")
key(Qt.Key.Key_G, True, "g")
pump(3.0)
qpos, info = eng.snapshot()
start = info.base_pos.copy()
heading = np.array([math.cos(info.yaw), math.sin(info.yaw)])
arm_idx = [eng.model.jnt_qposadr[eng.model.joint(n).id] for n in J.ARM_JOINTS]
hand_idx = [eng.model.jnt_qposadr[eng.model.joint(n).id] for n in H.HAND_JOINTS]
target = ARM_POSES["al_frente"].target(0.0)
hand_target = eng.hand_target.copy()
samples = {"min_altura_pelvis_m": float("inf"), "max_error_brazos_rad": 0.0,
           "max_error_manos_rad": 0.0, "min_rtf": float("inf")}


def sample():
    q, inf = eng.snapshot()
    samples["min_altura_pelvis_m"] = min(samples["min_altura_pelvis_m"], float(inf.base_pos[2]))
    samples["max_error_brazos_rad"] = max(samples["max_error_brazos_rad"],
                                          float(np.abs(q[arm_idx] - target).max()))
    samples["max_error_manos_rad"] = max(samples["max_error_manos_rad"],
                                         float(np.abs(q[hand_idx] - hand_target).max()))
    if inf.rtf > 0:
        samples["min_rtf"] = min(samples["min_rtf"], float(inf.rtf))


key(Qt.Key.Key_W, True, "w")
pump(4.0, sample)
end_command = eng.info.base_pos.copy()
key(Qt.Key.Key_W, False, "w")
pump(2.0)
qpos, info = eng.snapshot()
advance_command = float(np.dot(end_command[:2] - start[:2], heading))
report.update({
    "modo_final": info.state,
    "avance_durante_orden_m": round(advance_command, 3),
    "desplazamiento_residual_m": round(float(np.linalg.norm(info.base_pos[:2] - end_command[:2])), 3),
    "error_brazos_rad": round(float(np.abs(qpos[arm_idx] - target).max()), 3),
    "error_manos_rad": round(float(np.abs(qpos[hand_idx] - hand_target).max()), 3),
    "altura_pelvis_m": round(float(info.base_pos[2]), 3),
    "min_altura_pelvis_m": round(samples["min_altura_pelvis_m"], 3),
    "max_error_brazos_rad": round(samples["max_error_brazos_rad"], 3),
    "max_error_manos_rad": round(samples["max_error_manos_rad"], 3),
    "min_rtf": None if math.isinf(samples["min_rtf"]) else round(samples["min_rtf"], 3),
})
saved = win.grab().save(out)
report["captura"] = out if saved and os.path.getsize(out) > 0 else None
failures = []
if not report["urs"] or report["modo_final"] != C_URS:
    failures.append("la GUI no mantuvo URS")
if advance_command < 0.1:
    failures.append("W no produjo avance")
if samples["min_altura_pelvis_m"] <= 0.45:
    failures.append("el robot cayó durante la marcha")
if samples["max_error_brazos_rad"] > 0.1:
    failures.append("los brazos no siguieron el objetivo")
if samples["max_error_manos_rad"] > 0.05:
    failures.append("las manos no siguieron el objetivo")
if report["captura"] is None:
    failures.append("no se guardó la captura de la GUI")
report["aprobado"] = not failures
report["fallos"] = failures
print(json.dumps(report, ensure_ascii=False))
win.close()
eng.stop()
if failures:
    sys.exit(1)
