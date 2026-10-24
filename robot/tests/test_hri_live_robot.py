"""Tests del runner en vivo sin actuación (hri_runtime/live_robot.py).

Corren en el PC sin ROS (rclpy/aimdk_msgs no se importan; los mensajes son objetos
falsos) y sin mujoco: el archivo es independiente de tests/conftest.py::

    PYTHONPATH=src python -m pytest tests/test_hri_live_robot.py -q --noconftest
"""
from __future__ import annotations

import queue
import sys
import threading
import types
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT / "src"), str(ROOT)):          # x2_ultra (src/) y hriego_v2 (raíz)
    if p not in sys.path:
        sys.path.insert(0, p)

from x2_ultra.hri_runtime import live_robot as L  # noqa: E402

SR = L.SR


# --------------------------------------------------------------------------- utilidades
@pytest.fixture
def guard():
    """Instala el import guard y lo retira al terminar (no contamina otros tests)."""
    saved = {m: sys.modules.pop(m) for m in list(sys.modules) if L._is_blocked(m)}
    L.uninstall_import_guard()
    g = L.install_import_guard()
    try:
        yield g
    finally:
        L.uninstall_import_guard()
        sys.modules.update(saved)


def _noise(n, db, seed=0):
    rng = np.random.default_rng(seed)
    x = rng.standard_normal(n).astype(np.float32)
    x /= np.sqrt(np.mean(x ** 2))
    return x * (10 ** (db / 20.0))


def _speech_like(dur_s, db=-20.0, sr=SR):
    t = np.arange(int(dur_s * sr)) / sr
    env = 0.6 + 0.4 * np.sin(2 * np.pi * 3.0 * t) ** 2          # modulación silábica
    x = (np.sin(2 * np.pi * 220 * t) + 0.5 * np.sin(2 * np.pi * 660 * t)) * env
    x = x / np.sqrt(np.mean(x ** 2)) * (10 ** (db / 20.0))
    return x.astype(np.float32)


def _feed_chunks(vad, x, chunk=480):
    out = []
    for i in range(0, len(x), chunk):
        out += vad.feed(x[i:i + chunk])
    return out


# --------------------------------------------------------------------------- seguridad
def test_preflight_aborta_con_x2_real():
    with pytest.raises(SystemExit) as e:
        L.safety_preflight([], {"X2_REAL": "robot"})
    assert e.value.code == 3


def test_preflight_aborta_con_x2_real_aunque_sea_cero():
    with pytest.raises(SystemExit):
        L.safety_preflight([], {"X2_REAL": "0"})


@pytest.mark.parametrize("argv", [["--arm"], ["--visit", "VIP", "--arm"], ["--arm=1"]])
def test_preflight_aborta_con_arm(argv):
    with pytest.raises(SystemExit) as e:
        L.safety_preflight(argv, {})
    assert e.value.code == 3


@pytest.mark.parametrize("key", ["HRI_NO_ACTUATION", "HRIEGO_NO_ACTUATION"])
def test_preflight_exige_no_actuation_1(key):
    with pytest.raises(SystemExit):
        L.safety_preflight([], {key: "0"})


def test_preflight_fija_los_flags_por_defecto():
    env: dict = {}
    L.safety_preflight(["--visit", "VIP"], env)
    assert env["HRI_NO_ACTUATION"] == "1" and env["HRIEGO_NO_ACTUATION"] == "1"


def test_main_aborta_con_arm_antes_de_importar_ros(monkeypatch):
    monkeypatch.delenv("X2_REAL", raising=False)
    sys.modules.pop("rclpy", None)
    with pytest.raises(SystemExit) as e:
        L.main(["--arm"])
    assert e.value.code == 3
    assert "rclpy" not in sys.modules


def test_main_aborta_con_x2_real(monkeypatch):
    monkeypatch.setenv("X2_REAL", "robot")
    with pytest.raises(SystemExit) as e:
        L.main(["--entrada", "stdin"])
    assert e.value.code == 3


@pytest.mark.parametrize("mod", [
    "x2_ultra.real",
    "x2_ultra.real.control",
    "x2_ultra.hri_runtime.backends.aimdk",
    "x2_ultra.hri_runtime.backends.twin",
    "x2_ultra.hri_runtime.backends.show",
    "x2_ultra.hri_runtime.motion_online",
    "x2_ultra.twin.ros_hal",
    "x2_ultra.show.engine",
    "mujoco",
])
def test_import_guard_bloquea_actuacion(guard, mod):
    import importlib
    with pytest.raises(L.ActuationImportBlocked):
        importlib.import_module(mod)
    assert mod not in sys.modules
    assert any(mod == a or mod.startswith(a + ".") for a in guard.attempts)


def test_import_guard_deja_pasar_lo_conversacional(guard):
    import importlib
    for mod in ("x2_ultra.hri_runtime.fsm", "x2_ultra.hri_runtime.app",
                "x2_ultra.hri_runtime.llm_client", "x2_ultra.hri_runtime.tts",
                "x2_ultra.hri_runtime.asr.client", "x2_ultra.hri_runtime.backends.base",
                "hriego_v2.robot_io"):
        importlib.import_module(mod)
    assert not guard.attempts


def test_import_guard_aborta_si_ya_estaba_cargado():
    L.uninstall_import_guard()
    fake = types.ModuleType("x2_ultra.real")
    sys.modules["x2_ultra.real"] = fake
    try:
        with pytest.raises(SystemExit):
            L.install_import_guard()
    finally:
        sys.modules.pop("x2_ultra.real", None)
        L.uninstall_import_guard()


def test_nodo_ros_con_lista_blanca_rechaza_actuacion(monkeypatch):
    from hriego_v2 import robot_io
    monkeypatch.setenv("HRIEGO_NO_ACTUATION", "1")

    class FakeNode:
        def create_publisher(self, *a, **k):
            return "pub"

        def create_client(self, *a, **k):
            return "cli"

    node = FakeNode()
    with pytest.raises(SystemExit):              # sin lista blanca ⇒ aborto
        L.assert_node_guarded(node)
    robot_io._guard_node(node)
    L.assert_node_guarded(node)
    for topic in ("/mc/upper_body_command", "/aima/mc/locomotion/velocity", "/aima/mc/body_pose"):
        with pytest.raises(robot_io.ActuationBlocked):
            node.create_publisher(object, topic, 10)
    for srv in ("/aimdk_5Fmsgs/srv/SetMcAction", "/aimdk_5Fmsgs/srv/SetMcInputSource",
                "/aimdk_5Fmsgs/srv/SetMcPresetMotion"):
        with pytest.raises(robot_io.ActuationBlocked):
            node.create_client(object, srv)
    assert node.create_publisher(object, "/aima/hal/audio/playback", 10) == "pub"


# --------------------------------------------------------------------------- audio
def test_extract_mono_canal_0_de_6_entrelazados():
    n = 480
    frames = np.zeros((n, 6), dtype="<i2")
    sig = (np.arange(n) % 100 * 100).astype("<i2")
    frames[:, 0] = sig
    frames[:, 1] = 1234
    frames[:, 4:] = -999                         # referencia
    mono = L.extract_mono(frames.tobytes(), 6, "s16le", 0)
    assert mono.dtype == np.float32 and mono.shape == (n,)
    np.testing.assert_allclose(mono, sig / 32768.0, atol=1e-7)
    np.testing.assert_allclose(L.extract_mono(frames.tobytes(), 6, "s16le", 1), 1234 / 32768.0)


def test_extract_mono_media_de_microfonos_ignora_referencia():
    frames = np.zeros((10, 6), dtype="<i2")
    frames[:, :4] = [100, 200, 300, 400]
    frames[:, 4:] = 30000
    mono = L.extract_mono(frames.tobytes(), 6, "s16le", "mean", mic_channels=4)
    np.testing.assert_allclose(mono, 250 / 32768.0, atol=1e-7)


def test_extract_mono_float32_array_y_bytes_sobrantes():
    import array
    x = np.array([[0.5, -0.5], [0.25, 0.1]], dtype="<f4")
    raw = array.array("B", x.tobytes() + b"\x01")          # como rclpy uint8[] + 1 byte suelto
    np.testing.assert_allclose(L.extract_mono(raw, 2, "f32le", 0), [0.5, 0.25])
    with pytest.raises(ValueError):
        L.extract_mono(x.tobytes(), 2, "f32le", 5)
    with pytest.raises(ValueError):
        L.extract_mono(x.tobytes(), 2, "mulaw", 0)


@pytest.mark.parametrize("sr_in", [48000, 44100, 22050, 8000])
def test_resample_conserva_duracion_y_frecuencia(sr_in):
    f = 440.0
    t = np.arange(int(sr_in * 1.0)) / sr_in
    x = np.sin(2 * np.pi * f * t).astype(np.float32)
    y = L.resample(x, sr_in, SR)
    assert abs(len(y) - SR) <= 2
    spec = np.abs(np.fft.rfft(y * np.hanning(len(y))))
    peak = np.argmax(spec) * SR / len(y)
    assert abs(peak - f) < 3.0
    assert L.resample(x, sr_in, sr_in) is not x


def test_write_wav_s16(tmp_path):
    import wave
    p = L.write_wav_s16(tmp_path / "a.wav", np.array([0.0, 1.5, -1.5, 0.5], np.float32), 16000)
    with wave.open(str(p)) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()) == (1, 2, 16000, 4)
        data = np.frombuffer(w.readframes(4), "<i2")
    assert list(data[:3]) == [0, 32767, -32767]


# --------------------------------------------------------------------------- VAD
def test_vad_una_utterance_entre_silencios():
    vad = L.EnergyVad()
    x = np.concatenate([_noise(SR, -60), _speech_like(1.5), _noise(int(1.2 * SR), -60, seed=1)])
    utts = _feed_chunks(vad, x)
    assert len(utts) == 1
    assert 1.4 <= utts[0]["dur_s"] <= 2.2          # 1,5 s + pre-roll + cola recortada
    assert not vad.in_speech


def test_vad_ignora_click_corto_y_ruido():
    vad = L.EnergyVad()
    x = np.concatenate([_noise(SR, -55), _speech_like(0.1, -15), _noise(2 * SR, -55, seed=2)])
    assert _feed_chunks(vad, x) == []


def test_vad_dos_frases_separadas_por_pausa():
    vad = L.EnergyVad(end_silence_ms=600)
    x = np.concatenate([_noise(SR, -60), _speech_like(1.0), _noise(SR, -60, seed=3),
                        _speech_like(0.8), _noise(SR, -60, seed=4)])
    assert len(_feed_chunks(vad, x)) == 2


def test_vad_pausa_corta_no_parte_la_frase():
    vad = L.EnergyVad(end_silence_ms=700)
    x = np.concatenate([_noise(SR, -60), _speech_like(0.8), _noise(int(0.3 * SR), -60, seed=5),
                        _speech_like(0.8), _noise(SR, -60, seed=6)])
    utts = _feed_chunks(vad, x)
    assert len(utts) == 1 and utts[0]["dur_s"] > 1.8


def test_vad_corta_en_duracion_maxima():
    vad = L.EnergyVad(max_utt_s=2.0)
    x = np.concatenate([_noise(SR, -60), _speech_like(5.0)])
    utts = _feed_chunks(vad, x)
    assert len(utts) >= 1 and utts[0]["dur_s"] <= 2.1


def test_vad_se_adapta_a_ruido_de_fondo_alto():
    vad = L.EnergyVad()
    x = np.concatenate([_noise(2 * SR, -38), _speech_like(1.2, -12),
                        _noise(int(1.5 * SR), -38, seed=7)])
    assert len(_feed_chunks(vad, x)) == 1


# --------------------------------------------------------------------------- pipeline de micrófono
def _capture_msg(mono_f32, sr=16000, channels=6, mic=4, ref=2, fmt="s16le"):
    frames = np.zeros((len(mono_f32), channels), dtype="<i2")
    frames[:, 0] = np.clip(mono_f32 * 32767, -32768, 32767).astype("<i2")
    info = SimpleNamespace(channels=channels, sample_rate=sr, size=frames.nbytes,
                           sample_format=fmt, coding_format="pcm")
    return SimpleNamespace(mic_channels=mic, ref_channels=ref, info=info,
                           data=SimpleNamespace(data=frames.tobytes()), pkg_name="hal")


def test_mic_pipeline_semiduplex_y_formato():
    q: queue.Queue = queue.Queue()
    logs: list[str] = []
    pipe = L.MicPipeline(L.EnergyVad(), q, channel=0, log=logs.append)
    x = np.concatenate([_noise(SR, -60), _speech_like(1.2), _noise(int(1.2 * SR), -60, seed=8)])
    for i in range(0, len(x), 480):                 # cerrado: el robot "habla"
        pipe.on_msg(_capture_msg(x[i:i + 480]))
    assert q.empty() and pipe.dropped > 0
    assert pipe.format_seen["channels"] == 6 and pipe.format_seen["sample_rate"] == 16000
    pipe.resume()
    for i in range(0, len(x), 480):
        pipe.on_msg(_capture_msg(x[i:i + 480]))
    assert q.qsize() == 1 and pipe.errors == 0
    assert any("formato detectado" in m for m in logs)


def test_mic_pipeline_remuestrea_48k():
    q: queue.Queue = queue.Queue()
    pipe = L.MicPipeline(L.EnergyVad(), q, channel=0, log=lambda *_: None)
    pipe.resume()
    sr = 48000
    x = np.concatenate([_noise(sr, -60), resample_up(_speech_like(1.2), sr),
                        _noise(int(1.2 * sr), -60, seed=9)])
    for i in range(0, len(x), 1440):
        pipe.on_msg(_capture_msg(x[i:i + 1440], sr=sr))
    utt = q.get_nowait()
    assert 1.1 <= utt["dur_s"] <= 2.2


def resample_up(x, sr):
    return np.interp(np.arange(int(len(x) * sr / SR)) * SR / sr, np.arange(len(x)), x).astype(np.float32)


# --------------------------------------------------------------------------- app en vivo
class _RecPlayer:
    def __init__(self):
        self.files: list[tuple[int, int]] = []

    def play(self, path):
        import wave
        with wave.open(str(path)) as w:
            self.files.append((w.getframerate(), w.getnframes()))
        return {"player": "rec"}

    def stop(self):
        pass


class _ToneTts:
    name, estimated = "tone", False

    def synthesize(self, sentence, voice):
        sr = 44100
        return np.full(int(0.2 * sr), 0.1, np.float32), sr, {"engine": "tone", "estimated": False}


def _args(**kw):
    ns = L.build_parser().parse_args(["--entrada", "stdin", "--sin-voz", "--log-dir", ""])
    ns.mic_canal = 0
    ns.identity_real = False
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def test_sesion_en_vivo_solo_nullbackend_y_sin_modulos_de_actuacion(guard, tmp_path):
    from x2_ultra.hri_runtime.app import NullBackend
    lines = iter(["Dame información acerca del gimnasio.", "Quiero ir a la biblioteca", "sí",
                  "adiós, gracias"])
    player = _RecPlayer()
    sess = L.LiveSession(_args(visit="VIP"), llm=None, tts=_ToneTts(), player=player,
                         text_source=lambda: next(lines, None),
                         logger=L.TurnLogger(tmp_path, quiet=True))
    assert sess.run() == 0
    assert type(sess.app.backend_raw) is NullBackend
    assert sess.app.motion is None and sess.app.engine is None and sess.app.realtime
    assert sess.app.fsm.state.value == "END"
    for mod in ("x2_ultra.hri_runtime.backends.aimdk", "x2_ultra.hri_runtime.backends.twin",
                "x2_ultra.hri_runtime.backends.show", "x2_ultra.real", "mujoco", "rclpy"):
        assert mod not in sys.modules, mod
    assert not guard.attempts
    # la voz pasó por el reproductor a 16 kHz (remuestreada desde 44,1 kHz)
    assert player.files and all(sr == 16000 for sr, _ in player.files)
    # intenciones motoras: registradas, nunca ejecutadas
    import json
    recs = [json.loads(x) for x in next(tmp_path.glob("live_*.jsonl")).read_text("utf-8").splitlines()]
    motors = [m for r in recs for m in r["motor_intents_not_executed"]]
    assert motors and all(m["ejecutado"] is False for m in motors)
    assert any(m["act"] == "WALK_WITH" for m in motors)


def test_sesion_no_instancia_backends_reales(guard, monkeypatch):
    """Aunque alguien intentara construir un backend real, el guard lo impide."""
    import importlib
    for mod in ("x2_ultra.hri_runtime.backends.aimdk", "x2_ultra.hri_runtime.backends.twin",
                "x2_ultra.hri_runtime.backends.show"):
        with pytest.raises(ImportError):
            importlib.import_module(mod)
    app = L.build_app(llm=None, tts=_ToneTts(), sink=None, conversation_timeout_s=30,
                      identity_real=False, person=L.make_person("GUEST"))
    from x2_ultra.hri_runtime.app import NullBackend
    assert type(app.backend_raw) is NullBackend


def test_app_por_defecto_sigue_con_reloj_virtual():
    """El hook es opt-in: sin audio_sink/realtime el app se comporta como en wavtest."""
    from x2_ultra.hri_runtime.app import ClubHriApp, NullBackend
    from x2_ultra.hri_runtime.tts import NullTts
    app = ClubHriApp(NullBackend(), tts=NullTts())
    assert app.realtime is False and app.audio_sink is None
    app.admit(qr_image={"person_id": "P004", "name": "Mario", "rank": "GUEST"})
    assert app._clock() > 1.0                          # avanzó el reloj virtual con el habla


def test_app_realtime_entrega_frases_al_sink():
    from x2_ultra.hri_runtime.app import ClubHriApp, NullBackend
    got = []
    app = ClubHriApp(NullBackend(), tts=_ToneTts(), realtime=True,
                     audio_sink=lambda pcm, sr, meta: got.append((len(pcm), sr, meta["text"])),
                     confirm_fn=lambda *a, **k: {"person_id": "P004", "name": "Mario", "rank": "GUEST"})
    app.admit()
    assert got and got[0][1] == 44100
    assert app._clock() < 5.0                          # reloj de pared, no simulado


def test_speech_player_espera_reproduccion(tmp_path):
    class SlowPlayer(_RecPlayer):
        def play(self, path):
            super().play(path)
            threading.Event().wait(0.05)

    sp = L.SpeechPlayer(SlowPlayer(), playback_sr=16000, tmp_dir=tmp_path, log=lambda *_: None)
    sp(np.zeros(4410, np.float32), 44100, {"gap_s": 0.15})
    sp(np.zeros(4410, np.float32), 44100, {})
    assert sp.speaking
    assert sp.wait_idle(5.0) and not sp.speaking
    assert [n for _, n in sp.player.files] == [1600 + 2400, 1600]
    assert not list(tmp_path.glob("*.wav"))            # temporales borrados
    sp.stop()


def test_live_robot_no_importa_dependencias_pesadas():
    src = (ROOT / "src/x2_ultra/hri_runtime/live_robot.py").read_text(encoding="utf-8")
    for bad in ("import mujoco", "import torch", "import faster_whisper", "from faster_whisper",
                "from scipy", "import scipy",
                "backends.aimdk import", "backends.twin import", "backends.show import"):
        assert bad not in src, bad


# --------------------------------------------------------------------------- ROS falso
def test_suscriptor_de_microfono_con_rclpy_falso(monkeypatch):
    """RosMicSubscriber crea su nodo vía robot_io._ros_node (nombre hri_club_*, lista blanca
    aplicada), solo se suscribe y no crea publicadores ni clientes."""
    from hriego_v2 import robot_io

    created: list = []

    class FakeNode:
        def __init__(self, name):
            self.name, self.subs, self.pubs, self.clients = name, [], [], []

        def create_subscription(self, typ, topic, cb, qos):
            self.subs.append((typ, topic, cb, qos))
            return ("sub", topic)

        def create_publisher(self, typ, topic, *a, **k):
            self.pubs.append(topic)

        def create_client(self, typ, name, *a, **k):
            self.clients.append(name)

        def destroy_subscription(self, sub):
            pass

        def destroy_node(self):
            pass

    class FakeExecutor:
        def __init__(self, num_threads=2):
            self.nodes = []

        def spin(self):
            pass

        def add_node(self, n):
            self.nodes.append(n)

        def remove_node(self, n):
            self.nodes.remove(n)

        def shutdown(self, timeout_sec=None):
            pass

    rclpy = types.ModuleType("rclpy")
    rclpy.ok = lambda: True
    rclpy.init = lambda: None
    rclpy.create_node = lambda name: created.append(FakeNode(name)) or created[-1]
    executors = types.ModuleType("rclpy.executors")
    executors.MultiThreadedExecutor = FakeExecutor
    qos = types.ModuleType("rclpy.qos")
    qos.QoSProfile = lambda **kw: kw
    qos.QoSReliabilityPolicy = SimpleNamespace(RELIABLE="reliable", BEST_EFFORT="best_effort")
    msg = types.ModuleType("aimdk_msgs.msg")
    msg.AudioCapture = type("AudioCapture", (), {})
    pkg = types.ModuleType("aimdk_msgs")
    pkg.msg = msg
    for name, mod in (("rclpy", rclpy), ("rclpy.executors", executors), ("rclpy.qos", qos),
                      ("aimdk_msgs", pkg), ("aimdk_msgs.msg", msg)):
        monkeypatch.setitem(sys.modules, name, mod)
    monkeypatch.setattr(robot_io, "_ros_executor", None)
    monkeypatch.setattr(robot_io, "_ros_thread", None)
    monkeypatch.setattr(robot_io.atexit, "register", lambda f: f)
    monkeypatch.setenv("HRIEGO_NO_ACTUATION", "1")

    pipe = L.MicPipeline(L.EnergyVad(), queue.Queue(), log=lambda *_: None)
    sub = L.RosMicSubscriber(pipe, L.MIC_TOPIC, "best_effort")
    node = created[-1]
    assert node.name.startswith("hri_club_mic_")
    assert node.subs[0][1] == "/aima/hal/audio/capture"
    assert node.subs[0][3]["reliability"] == "best_effort"
    assert node.pubs == [] and node.clients == []
    with pytest.raises(robot_io.ActuationBlocked):
        node.create_publisher(object, "/aima/mc/locomotion/velocity", 10)
    # el callback registrado es el del pipeline
    node.subs[0][2](_capture_msg(np.zeros(480, np.float32)))
    assert pipe.msgs == 1
    sub.close()
    robot_io._ros_executor.thread = None
