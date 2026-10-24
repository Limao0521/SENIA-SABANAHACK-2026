"""Tests de las expresiones de la cara (hri_runtime/face.py) y sus ganchos en el runner.

Corren en el PC sin ROS ni aimdk_msgs: el cliente PlayEmoji es falso (grabadora) y el
backend real va en dry-run (``DryLink``), igual que en los tests del runner::

    PYTHONPATH=src python -m pytest tests/test_hri_face.py -q --noconftest
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT / "src"), str(ROOT)):          # x2_ultra (src/) y hriego_v2 (raíz)
    if p not in sys.path:
        sys.path.insert(0, p)

from x2_ultra.hri_runtime import face  # noqa: E402
from x2_ultra.hri_runtime import live_robot_actuado as A  # noqa: E402
from x2_ultra.hri_runtime.backends.aimdk import AimdkBackend  # noqa: E402


# --------------------------------------------------------------------------- clientes falsos
class Recorder:
    """Cliente falso: graba cada play(eid, mode, priority) y siempre contesta éxito."""

    def __init__(self):
        self.calls: list[tuple[int, int, int]] = []

    def play(self, eid, mode, priority):
        self.calls.append((int(eid), int(mode), int(priority)))
        return True, "ok"

    @property
    def ids(self) -> list[int]:
        return [c[0] for c in self.calls]


class Unavailable:
    """Cliente falso: la imagen no tiene el tipo o el servicio no existe."""

    def play(self, eid, mode, priority):
        raise face.FaceUnavailable("servicio /aimdk_5Fmsgs/srv/PlayEmoji no disponible (test)")


class Failing:
    """Cliente falso: siempre contesta success=False."""

    def __init__(self):
        self.calls: list[tuple[int, int, int]] = []

    def play(self, eid, mode, priority):
        self.calls.append((int(eid), int(mode), int(priority)))
        return False, "x"


def _fac(client=None, **kw) -> face.FaceExpressions:
    """FaceExpressions con cliente falso, sin rate limit y sin ruido en el log."""
    kw.setdefault("min_interval_s", 0.0)
    kw.setdefault("log", lambda m: None)
    return face.FaceExpressions(client=client, **kw)


# --------------------------------------------------------------------------- a) tablas
def test_expresiones_usan_ids_y_modos_del_firmware():
    for name, (eid, mode) in face.EXPRESIONES.items():
        assert eid in face.EMOJI_IDS, name
        assert mode in (face.MODE_ONCE, face.MODE_LOOP), name


def test_estado_hablando_y_emotes_apuntan_a_expresiones():
    for st, expr in face.ESTADO_HABLANDO.items():
        assert expr in face.EXPRESIONES, st
    for typ, expr in face.EMOTE_EXPRESION.items():
        assert expr in face.EXPRESIONES, typ


def test_gestos_del_contrato_con_expresion():
    from x2_ultra.hri_runtime.contract import GESTURES
    for typ in GESTURES:
        if typ == "NONE":
            continue                              # sin gesto: la cara no cambia (nunca se despacha)
        assert typ in face.EMOTE_EXPRESION, typ


def test_estados_de_la_fsm_que_hablan_estan_en_estado_hablando():
    from x2_ultra.hri_runtime.fsm import State
    for st in State:
        if st in (State.LLEGADA, State.CARA):
            continue                              # identificación: el robot todavía no habla
        assert st.value in face.ESTADO_HABLANDO, st.value


# --------------------------------------------------------------------------- b) from_env
def test_from_env_sin_variable_o_a_cero_queda_apagada():
    for env in ({}, {"HRI_EXPRESIONES": "0"}):
        fac = face.FaceExpressions.from_env("seco", log=lambda m: None, env=env)
        assert fac.enabled is False and fac._thread is None
        fac.show("hablando")                      # inerte: ni objetivo ni registro
        assert fac.sent == []


def test_from_env_seco_va_en_registro():
    fac = face.FaceExpressions.from_env("seco", log=lambda m: None, env={"HRI_EXPRESIONES": "1"})
    try:
        assert fac.enabled and fac.dry_run and fac.client is None
        fac.show("pensando", "turno")
        assert fac.wait_sent()
        assert fac.sent == [("pensando", 170, face.MODE_LOOP)]
    finally:
        fac.close(final=None)


def test_from_env_seco_con_pantalla_real_llama_al_cliente():
    rec = Recorder()
    fac = face.FaceExpressions.from_env(
        "seco", log=lambda m: None, client=rec, min_interval_s=0.0,
        env={"HRI_EXPRESIONES": "1", "HRI_EXPRESIONES_EN_SECO": "1"})
    try:
        assert fac.enabled and fac.dry_run is False
        fac.show("hablando")
        assert fac.wait_sent() and rec.ids == [90]
    finally:
        fac.close(final=None)


def test_from_env_lenta_no_va_en_registro():
    fac = face.FaceExpressions.from_env("lenta", log=lambda m: None, client=Recorder(),
                                        min_interval_s=0.0, env={"HRI_EXPRESIONES": "1"})
    try:
        assert fac.enabled and fac.dry_run is False
    finally:
        fac.close(final=None)


def test_from_env_prioridad_llega_al_cliente():
    rec = Recorder()
    fac = face.FaceExpressions.from_env(
        "lenta", log=lambda m: None, client=rec, min_interval_s=0.0,
        env={"HRI_EXPRESIONES": "1", "HRI_EXPRESIONES_PRIORIDAD": "15"})
    try:
        assert fac.priority == 15
        fac.show("atento")
        assert fac.wait_sent()
        assert rec.calls == [(11, face.MODE_LOOP, 15)]
    finally:
        fac.close(final=None)


# --------------------------------------------------------------------------- c) dedupe e IDs
def test_show_repetido_se_deduplica():
    rec = Recorder()
    fac = _fac(rec)
    try:
        fac.show("hablando")
        assert fac.wait_sent()
        fac.show("hablando")
        assert fac.wait_sent()
        assert rec.calls == [(90, face.MODE_LOOP, face.DEFAULT_PRIORITY)]
    finally:
        fac.close(final=None)


def test_ids_de_cada_gancho():
    assert face.emote_type(3001) == "GREETING_VIP"
    rec = Recorder()
    fac = _fac(rec)
    try:
        fac.thinking()
        assert fac.wait_sent()
        assert rec.calls[-1] == (170, face.MODE_LOOP, face.DEFAULT_PRIORITY)   # pensando, bucle
        fac.listening()
        assert fac.wait_sent()
        assert rec.ids[-1] == 1                                               # parpadeo
        fac.emote(motion_id=3001)                                             # GREETING_VIP → alegre
        assert fac.wait_sent()
        assert rec.ids[-1] == 100
        fac.emote(typ="SOCIAL_HEART")                                         # → encantado
        assert fac.wait_sent()
        assert rec.ids[-1] == 200
        fac.emote(typ="NEGATION_NORMAL")                                      # → disculpa
        assert fac.wait_sent()
        assert rec.ids[-1] == 120
        n = len(rec.calls)
        fac.emote(typ="UNKNOWN")                                              # gesto desconocido: nada
        assert fac.wait_sent()
        assert len(rec.calls) == n
    finally:
        fac.close(final=None)


# --------------------------------------------------------------------------- d) voz / FSM
def test_speech_start_usa_mood_y_si_no_el_estado():
    rec = Recorder()
    fac = _fac(rec, state_fn=lambda: "GREETING")
    try:
        fac.speech_start()                          # sin mood: ESTADO_HABLANDO[GREETING] = alegre
        assert fac.wait_sent() and rec.ids[-1] == 100
        fac.set_mood("disculpa")
        fac.speech_start()                          # el mood manda
        assert fac.wait_sent() and rec.ids[-1] == 120
    finally:
        fac.close(final=None)
    rec = Recorder()
    fac = _fac(rec, state_fn=lambda: "CONVERSATION")
    try:
        fac.speech_start()                          # hablando
        assert fac.wait_sent() and rec.ids[-1] == 90
    finally:
        fac.close(final=None)


def test_speech_end_en_end_vuelve_a_espera():
    rec = Recorder()
    fac = _fac(rec, state_fn=lambda: "END")
    try:
        fac.set_mood("alegre")
        fac.speech_end()
        assert fac.wait_sent()
        assert rec.ids[-1] == 10 and fac.mood is None
    finally:
        fac.close(final=None)


def test_listening_olvida_el_mood():
    rec = Recorder()
    fac = _fac(rec, state_fn=lambda: "CONVERSATION")
    try:
        fac.set_mood("alegre")
        fac.listening()
        assert fac.wait_sent()
        assert fac.mood is None and rec.ids[-1] == 1
        fac.speech_start()                          # sin mood: vuelve a mandar la FSM
        assert fac.wait_sent() and rec.ids[-1] == 90
    finally:
        fac.close(final=None)


# --------------------------------------------------------------------------- e) rate limit
def test_rate_limit_coalesce_y_gana_la_ultima():
    rec = Recorder()
    fac = face.FaceExpressions(client=rec, log=lambda m: None, min_interval_s=0.3)
    try:
        fac.show("espera")                          # 10
        fac.show("curioso")                         # 50: dentro del intervalo ⇒ coalesce
        fac.show("atento")                          # 11: gana la última
        assert fac.wait_sent(timeout=3.0)
        assert rec.ids and rec.ids[-1] == 11
        assert len(rec.calls) <= 2
    finally:
        fac.close(final=None)


# --------------------------------------------------------------------------- f) fallos
def test_face_unavailable_desactiva_con_log():
    lines: list[str] = []
    fac = face.FaceExpressions(client=Unavailable(), log=lines.append, min_interval_s=0.0)
    fac.show("hablando")
    assert fac.wait_sent()
    assert fac.enabled is False
    assert any("DESACTIVADAS" in l for l in lines)


def test_cinco_fallos_seguidos_sin_exito_desactivan():
    lines: list[str] = []
    cli = Failing()
    fac = face.FaceExpressions(client=cli, log=lines.append, min_interval_s=0.0)
    for _ in range(5):
        fac.show("hablando")
        assert fac.wait_sent()
    assert fac.enabled is False and fac.errors == 5 and fac.sent == []
    assert len(cli.calls) == 5                      # _last_id se reinicia: reintenta la misma
    assert any("DESACTIVADAS" in l for l in lines)


# --------------------------------------------------------------------------- g) dry-run
def test_dry_run_no_toca_el_cliente_y_registra():
    lines: list[str] = []
    fac = face.FaceExpressions(client=None, dry_run=True, log=lines.append, min_interval_s=0.0)
    try:
        fac.show("pensando", "turno")
        assert fac.wait_sent()
        assert fac.client is None
        assert fac.sent == [("pensando", 170, face.MODE_LOOP)]
        assert lines and all(l.startswith("[cara]") for l in lines)
        assert any("[registro]" in l for l in lines)
    finally:
        fac.close(final=None)


# --------------------------------------------------------------------------- h) runner actuado
# Fakes mínimos copiados de tests/test_hri_live_robot_actuado.py (sin importarlos: el archivo
# debe poder correr solo, --noconftest).
class _NullPlayer:
    def play(self, path):
        return {}

    def stop(self):
        pass


class _ToneTts:
    name, estimated = "tone", False

    def synthesize(self, sentence, voice):
        sr = 16000
        return np.full(int(0.1 * sr), 0.1, np.float32), sr, {"engine": "tone", "estimated": False}


def _args(**kw):
    ns = A.build_parser().parse_args(["--etapa", "seco", "--entrada", "stdin", "--sin-voz", "--log-dir", ""])
    ns.mic_canal = 0
    ns.identity_real = False
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def test_sesion_actuada_engancha_la_cara(tmp_path):
    rec = Recorder()
    fac = face.FaceExpressions(client=rec, log=lambda m: None, min_interval_s=0.0)
    be = AimdkBackend(A.make_config("seco", env={}), pump=False)
    it = iter(["adiós, gracias"])
    sess = None
    try:
        sess = A.ActuatedSession(_args(visit="STAFF"), backend=be, llm=None, tts=_ToneTts(),
                                 player=_NullPlayer(), text_source=lambda: next(it, None),
                                 logger=A.ActuatedTurnLogger(tmp_path, quiet=True), face=fac)
        assert sess.run() == 0
        assert sess.speech.on_play == fac.speech_start
        assert sess.speech.on_idle == fac.speech_end
        assert sess.sync.face is fac
        assert fac.wait_sent(timeout=3.0)
        assert 170 in rec.ids                       # pensando (admisión + turno de texto)
        assert {100, 90} & set(rec.ids)             # alegre/hablando al saludar y despedirse
    finally:
        if sess is not None:
            sess.stop()
        be.close()


def test_sesion_actuada_con_cara_apagada_no_engancha_nada(tmp_path):
    be = AimdkBackend(A.make_config("seco", env={}), pump=False)
    sess = None
    try:
        sess = A.ActuatedSession(_args(), backend=be, llm=None, tts=_ToneTts(),
                                 player=_NullPlayer(), text_source=lambda: None,
                                 logger=A.ActuatedTurnLogger(tmp_path, quiet=True),
                                 face=face.FaceExpressions(enabled=False))
        assert sess.speech.on_play is None and sess.speech.on_idle is None
        assert sess.sync.face is None
    finally:
        if sess is not None:
            sess.stop()
        be.close()


# --------------------------------------------------------------------------- i) identidad real
def test_cara_desconocida_curioso_y_disculpa_en_la_derivacion(tmp_path, monkeypatch):
    rec = Recorder()
    fac = face.FaceExpressions(client=rec, log=lambda m: None, min_interval_s=0.0)
    be = AimdkBackend(A.make_config("seco", env={}), pump=False)
    sess = None
    try:
        sess = A.ActuatedSession(_args(identity_real=True), backend=be, llm=None, tts=_ToneTts(),
                                 player=_NullPlayer(), text_source=lambda: None,
                                 logger=A.ActuatedTurnLogger(tmp_path, quiet=True), face=fac)
        # cara desconocida: la derivación a personal no necesita cámara ni QR
        monkeypatch.setattr(sess, "wait_for_visitor",
                            lambda *a, **k: ("f.jpg", None, {"status": "UNKNOWN", "top1_score": 0.2}))
        p = sess.admit_identity()
        assert p["status"] == "REJECTED" and p["reason"] == "NO_MIEMBRO"
        assert fac.wait_sent(timeout=3.0)
        assert 50 in rec.ids and 120 in rec.ids     # curioso al ver la cara; disculpa al hablar
        assert rec.ids.index(50) < rec.ids.index(120)
    finally:
        if sess is not None:
            sess.stop()
        be.close()
