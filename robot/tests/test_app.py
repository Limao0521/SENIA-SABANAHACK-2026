import pytest


@pytest.mark.slow
def test_app_runs_offscreen_and_handles_keys(tmp_path):
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QApplication

    from x2_ultra.app.main import MainWindow
    from x2_ultra.sim.engine import SimEngine

    app = QApplication.instance() or QApplication([])
    eng = SimEngine(scene="vacia", policy="agibot")
    win = MainWindow(eng)
    win.isActiveWindow = lambda: True
    win.show()

    def key(k, press=True, text=""):
        t = QEvent.Type.KeyPress if press else QEvent.Type.KeyRelease
        win.handle_key(QKeyEvent(t, k, Qt.KeyboardModifier.NoModifier, text), press)

    key(Qt.Key.Key_W)
    for _ in range(30):
        win.tick()
        eng.step_control()
    assert eng.cmd_target[0] > 0
    key(Qt.Key.Key_W, False)
    key(Qt.Key.Key_I)
    for _ in range(10):
        win.tick()
    key(Qt.Key.Key_I, False)
    assert "L" in win.ctl.ik_targets
    key(Qt.Key.Key_G, True, "g")
    assert abs(eng.hand_target[:10]).sum() > 0.3
    key(Qt.Key.Key_3, True, "3")
    assert eng.info.pose == "arriba"
    img = win.grab()
    assert img.save(str(tmp_path / "app.png"))
    win.close()


@pytest.mark.slow
def test_e_key_turns_right_with_default_policy():
    """Q/E giran al robot. E (derecha) es el lado que la red instalada no domina: pasa por el espejo."""
    import math

    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QApplication

    from x2_ultra import paths
    from x2_ultra.app.main import MainWindow
    from x2_ultra.sim.engine import SimEngine

    if not paths.default_policy().exists():
        pytest.skip("policy 'brazos libres' no instalada")
    app = QApplication.instance() or QApplication([])  # noqa: F841
    eng = SimEngine(scene="vacia")
    win = MainWindow(eng)
    win.isActiveWindow = lambda: True
    win.show()
    eng.run_for(1.0)
    win.handle_key(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_E, Qt.KeyboardModifier.NoModifier, "e"), True)
    turned, prev = 0.0, eng.info.yaw
    for _ in range(200):                                # 4 s con E pulsada
        win.tick()
        eng.step_control()
        turned += math.remainder(eng.info.yaw - prev, 2 * math.pi)
        prev = eng.info.yaw
    win.handle_key(QKeyEvent(QEvent.Type.KeyRelease, Qt.Key.Key_E, Qt.KeyboardModifier.NoModifier, "e"), False)
    assert eng.policy.mirrored
    assert turned < -2.5, f"giró {turned:.2f} rad en 4 s"
    win.close()
