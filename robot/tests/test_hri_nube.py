"""Pruebas de la inferencia en la nube con respaldo local (reto 2).

Cubren, sin ROS, GPU, whisper, supertonic ni red externa (solo 127.0.0.1):
- nube.py: etapas_activas/activa, Cortocircuito, LlmConRespaldo, clientes_llm,
  make_llm_client, AsrRemoto/AsrConRespaldo, TtsRemoto/TtsConRespaldo, envolver_*.
- nube_voz.py: servidor HTTP con Motores falsos (ASR/TTS inyectados).
- llm_client.py: LlamaStreamClient con prefijo de ruta, token Bearer y on_speech
  sobre SSE partido en trozos; HriLLM reenvía on_speech solo si está definido.
- tts.py: TtsPrefetch (acierto, fallos de clave y fallo del hilo).
- contract.py: build_messages con HRI_PROMPT_PREFIJO_ESTATICO (0/1).

Ejecución:
    $env:PYTHONPATH="src;."; python -m pytest tests/test_hri_nube.py -q --noconftest
"""
from __future__ import annotations

import contextlib
import http.client
import json
import os
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

# Autocontenido: si no viene PYTHONPATH="src;.", añade la raíz del paquete
# (inocuo cuando ya está en sys.path).
_SRC = str(Path(__file__).resolve().parents[1] / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from x2_ultra.hri_runtime import contract, nube, nube_voz, tts
from x2_ultra.hri_runtime.llm_client import HriLLM, LlamaStreamClient

# --------------------------------------------------------------------- entorno
_VARS_ENTORNO = (
    "HRI_NUBE_URL", "HRI_NUBE_TOKEN", "HRI_NUBE_ETAPAS", "HRI_NUBE_ENFRIAMIENTO_S",
    "HRI_NUBE_LLM_URL", "HRI_NUBE_LLM_TTFT_S", "HRI_NUBE_LLM_LECTURA_S",
    "HRI_NUBE_ASR_TIMEOUT_S", "HRI_NUBE_TTS_TIMEOUT_S", "HRI_NUBE_TTS_SR",
    "HRI_NUBE_TTS_STEPS", "HRI_PROMPT_PREFIJO_ESTATICO", "HRI_LLM_URL",
    "HRI_PLAYBACK_SR",
)


@pytest.fixture(autouse=True)
def _entorno_limpio(monkeypatch):
    """Borra las variables de entorno que alteran el comportamiento bajo prueba."""
    for var in _VARS_ENTORNO:
        monkeypatch.delenv(var, raising=False)


def _log_nulo(msg: str) -> None:
    """Log que no imprime nada (las pruebas no deben ensuciar la salida)."""


# --------------------------------------------------------------------- utilidades
@contextlib.contextmanager
def _servicio(srv):
    """Arranca un ThreadingHTTPServer en un hilo demonio y lo apaga al terminar."""
    hilo = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05},
                            daemon=True)
    hilo.start()
    try:
        yield srv
    finally:
        srv.shutdown()
        srv.server_close()


def _puerto_libre() -> int:
    """Puerto que nadie escucha: enlaza, lee el número y cierra (conexión rehusada)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    puerto = s.getsockname()[1]
    s.close()
    return puerto


# --------------------------------------------------------------------- falsos LLM
class _RemotoLlmFalso:
    """Cliente LLM remoto falso: falla con ``exc`` o devuelve ``raw``."""

    def __init__(self, raw="{}", exc=None, sano=True):
        self.raw, self.exc, self.sano = raw, exc, sano
        self.llamadas = 0
        self.timeout = 5.0

    def health(self) -> bool:
        return bool(self.sano)

    def stream_chat(self, messages, schema, **kw):
        self.llamadas += 1
        if self.exc is not None:
            raise self.exc
        return {"raw": self.raw, "metrics": {}}


class _LocalLlmFalso:
    """llama-server local falso: siempre responde rápido y cuenta llamadas."""

    def __init__(self, raw='{"type":"NONE","speech":"ok local","trigger_text":""}',
                 sano=True):
        self.raw, self.sano = raw, sano
        self.llamadas = 0
        self.timeout = 5.0

    def health(self) -> bool:
        return bool(self.sano)

    def stream_chat(self, messages, schema, **kw):
        self.llamadas += 1
        return {"raw": self.raw, "metrics": {"t_total": 0.01}}


# --------------------------------------------------------------------- falsos ASR/TTS
class _AsrLocalFalso:
    """AsrClient local falso (registra lo que recibe)."""

    def __init__(self):
        self.entradas = []

    def transcribe(self, wav_path_or_pcm, sr: int = 16000) -> dict:
        self.entradas.append((wav_path_or_pcm, sr))
        return {"text": "texto local", "latency_s": 0.0}

    def _rpc(self, msg: dict) -> dict:
        return {"ok": True, "eco": msg}

    def stop(self, *a, **k) -> None:
        pass


class _TtsLocalFalso:
    """Motor TTS local falso."""

    name = "null-falso"
    estimated = True

    def __init__(self):
        self.llamadas = 0

    def synthesize(self, sentence: str, voice: dict):
        self.llamadas += 1
        return np.zeros(100, dtype=np.float32), 16000, {"engine": self.name}


class _RemotoContador:
    """Envuelve un remoto real (AsrRemoto/TtsRemoto) y cuenta los intentos."""

    def __init__(self, interno):
        self.interno = interno
        self.llamadas = 0

    def transcribe(self, pcm, sr: int = 16000) -> dict:
        self.llamadas += 1
        return self.interno.transcribe(pcm, sr)

    def synthesize(self, sentence: str, voice: dict):
        self.llamadas += 1
        return self.interno.synthesize(sentence, voice)


class _MotorPrefetchFalso:
    """Motor interno para TtsPrefetch: duerme 0.05 s y cuenta llamadas."""

    name = "falso"
    estimated = False

    def __init__(self, fallar_primera: bool = False):
        self.llamadas = 0
        self.fallar_primera = fallar_primera

    def synthesize(self, sentence: str, voice: dict):
        self.llamadas += 1
        if self.fallar_primera and self.llamadas == 1:
            raise RuntimeError("fallo simulado del hilo")
        time.sleep(0.05)
        return np.zeros(1600, dtype=np.float32), 16000, {"engine": self.name,
                                                         "texto": sentence}


# --------------------------------------------------------------------- servidor LLM falso (SSE)
def _servidor_llama_falso(token: str, fragmentos=(), retraso_s: float = 0.0):
    """ThreadingHTTPServer que imita llama-server bajo el prefijo /llm con Bearer.

    Si ``retraso_s`` > 0, duerme antes de responder (para probar timeouts).
    """
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format, *args):    # noqa: A002 — firma de la clase base
            pass

        def _autenticar(self) -> bool:
            if self.headers.get("Authorization") == f"Bearer {token}":
                return True
            self._json(401, {"ok": False, "error": "token inválido"})
            return False

        def _json(self, status: int, obj: dict) -> None:
            body = json.dumps(obj).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if not self._autenticar():
                return
            if self.path.rstrip("/") == "/llm/health":
                self._json(200, {"status": "ok"})
            else:
                self._json(404, {"ok": False, "error": "ruta"})

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            if n > 0:
                self.rfile.read(n)
            if not self._autenticar():
                return
            if self.path != "/llm/v1/chat/completions":
                self._json(404, {"ok": False, "error": "ruta"})
                return
            if retraso_s > 0:
                time.sleep(retraso_s)
            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Connection", "close")
                self.end_headers()
                for frag in fragmentos:
                    evento = ("data: " + json.dumps(
                        {"choices": [{"delta": {"content": frag}}]}, ensure_ascii=False)
                        + "\n\n")
                    self.wfile.write(evento.encode("utf-8"))
                    self.wfile.flush()
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
            except OSError:
                pass    # el cliente pudo cerrar antes (timeout): no molesta
            self.close_connection = True

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    srv.daemon_threads = True
    return srv


# --------------------------------------------------------------------- servidor de voz falso
class _AsrNubeFalso:
    """Motor ASR del servidor de voz: texto fijo y registro de lo recibido."""

    def __init__(self):
        self.muestras = 0
        self.sr = 0

    def transcribe(self, pcm, sr: int) -> dict:
        self.muestras = int(len(pcm))
        self.sr = int(sr)
        return {"text": "hola", "latency_s": 0.01, "t_start": 0, "t_end": 0}


class _TtsNubeFalso:
    """Motor TTS del servidor de voz: 0.5 s de tono constante a 44100 Hz."""

    def synthesize(self, text: str, voice: dict):
        pcm = (np.ones(int(44100 * 0.5), dtype=np.float32) * 0.1)
        return pcm, 44100, {}


@pytest.fixture()
def servidor_voz():
    """Servidor nube_voz en 127.0.0.1:0 con motores falsos y token «abc»."""
    asr, motor_tts = _AsrNubeFalso(), _TtsNubeFalso()
    motores = nube_voz.Motores(asr=asr, tts=motor_tts)
    srv = nube_voz.make_server("127.0.0.1", 0, motores, tok="abc")
    with _servicio(srv):
        yield SimpleNamespace(srv=srv, asr=asr, tts=motor_tts,
                              puerto=srv.server_address[1],
                              base=f"http://127.0.0.1:{srv.server_address[1]}")


def _asr_remoto_directo(base: str, tok: str, timeout: float = 2.0) -> nube.AsrRemoto:
    """AsrRemoto contra la raíz del servidor (sin el prefijo /voz del gateway)."""
    r = nube.AsrRemoto(base=base, timeout=timeout, tok=tok)
    r.ep.prefix = ""
    return r


def _tts_remoto_directo(base: str, tok: str, timeout: float = 2.0,
                        sr: int = 16000) -> nube.TtsRemoto:
    t = nube.TtsRemoto(base=base, timeout=timeout, tok=tok, sr=sr)
    t.ep.prefix = ""
    return t


# ===================================================================== 1) sin nube
def test_sin_nube_etapas_vacias():
    """Sin HRI_NUBE_URL no hay ninguna etapa descargada a la nube."""
    assert nube.etapas_activas() == set()
    assert nube.activa("llm") is False
    assert nube.activa("asr") is False
    assert nube.activa("tts") is False


def test_sin_nube_envolturas_devuelven_el_mismo_objeto():
    """Sin nube, envolver_tts/envolver_asr no envuelven nada (identidad)."""
    motor = _TtsLocalFalso()
    asr = _AsrLocalFalso()
    assert nube.envolver_tts(motor, log=_log_nulo) is motor
    assert nube.envolver_asr(asr, log=_log_nulo) is asr


def test_sin_nube_make_llm_client_local():
    """Sin nube, make_llm_client devuelve un LlamaStreamClient pelado."""
    c = nube.make_llm_client(log=_log_nulo)
    assert isinstance(c, LlamaStreamClient)
    assert not isinstance(c, nube.LlmConRespaldo)


# ===================================================================== 2) etapas
def test_etapas_subconjunto_explicito(monkeypatch):
    """HRI_NUBE_ETAPAS restringe qué etapas usan la nube."""
    monkeypatch.setenv("HRI_NUBE_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("HRI_NUBE_ETAPAS", "tts,llm")
    assert nube.etapas_activas() == {"tts", "llm"}
    assert nube.activa("tts") is True
    assert nube.activa("llm") is True
    assert nube.activa("asr") is False


def test_etapas_defecto_todas(monkeypatch):
    """Sin HRI_NUBE_ETAPAS pero con URL, las tres etapas van a la nube."""
    monkeypatch.setenv("HRI_NUBE_URL", "http://127.0.0.1:9")
    assert nube.etapas_activas() == {"llm", "asr", "tts"}


def test_etapas_nombres_invalidos_ignorados(monkeypatch):
    """Nombres inválidos se descartan; mayúsculas y espacios se toleran."""
    monkeypatch.setenv("HRI_NUBE_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("HRI_NUBE_ETAPAS", " TTS , inventada , LLM ")
    assert nube.etapas_activas() == {"tts", "llm"}


def test_envolturas_activas_con_nube(monkeypatch):
    """Con nube activa, envolver_tts/envolver_asr sí envuelven (y son idempotentes)."""
    monkeypatch.setenv("HRI_NUBE_URL", "http://127.0.0.1:9")
    motor, asr = _TtsLocalFalso(), _AsrLocalFalso()
    env_t = nube.envolver_tts(motor, log=_log_nulo)
    assert isinstance(env_t, nube.TtsConRespaldo)
    assert env_t.local is motor
    assert nube.envolver_tts(env_t, log=_log_nulo) is env_t
    env_a = nube.envolver_asr(asr, log=_log_nulo)
    assert isinstance(env_a, nube.AsrConRespaldo)
    assert env_a.local is asr
    assert nube.envolver_asr(env_a, log=_log_nulo) is env_a


# ===================================================================== 3) cortocircuito
def test_cortocircuito_abre_y_cierra_por_reloj():
    """fallo() abre el cortocircuito hasta que pasa enfriamiento_s (reloj falso)."""
    t = [0.0]
    c = nube.Cortocircuito(enfriamiento_s=10.0, reloj=lambda: t[0])
    assert c.permite() is True
    c.fallo("caída de red")
    assert c.permite() is False
    t[0] = 9.99
    assert c.permite() is False
    t[0] = 10.0
    assert c.permite() is True
    assert c.fallos == 1
    assert c.ultimo_motivo == "caída de red"


def test_cortocircuito_exito_cierra_inmediato():
    """exito() cierra el cortocircuito aunque no haya pasado el enfriamiento."""
    t = [0.0]
    c = nube.Cortocircuito(enfriamiento_s=1000.0, reloj=lambda: t[0])
    c.fallo("x")
    assert c.permite() is False
    c.exito()
    assert c.permite() is True


# ===================================================================== 4) LlmConRespaldo
def test_llm_respaldo_cae_a_local_por_fallo_remoto():
    """Si el remoto lanza ConnectionRefusedError, responde el local con su motivo."""
    remoto = _RemotoLlmFalso(exc=ConnectionRefusedError("sin ruta"))
    local = _LocalLlmFalso()
    w = nube.LlmConRespaldo(remoto, local,
                            breaker=nube.Cortocircuito(enfriamiento_s=30.0),
                            log=_log_nulo)
    res = w.stream_chat([{"role": "user", "content": "hola"}], {})
    assert res["raw"] == local.raw
    assert res["metrics"]["backend"] == "local"
    assert res["metrics"]["fallback_motivo"]
    assert "ConnectionRefusedError" in res["metrics"]["fallback_motivo"]
    assert local.llamadas == 1


def test_llm_respaldo_no_reintenta_dentro_del_enfriamiento():
    """Dentro del enfriamiento el remoto no se toca: cortocircuito directo al local."""
    remoto = _RemotoLlmFalso(exc=ConnectionRefusedError("sin ruta"))
    local = _LocalLlmFalso()
    w = nube.LlmConRespaldo(remoto, local,
                            breaker=nube.Cortocircuito(enfriamiento_s=30.0),
                            log=_log_nulo)
    w.stream_chat([], {})
    assert remoto.llamadas == 1
    res = w.stream_chat([], {})
    assert remoto.llamadas == 1              # no se reintentó
    assert local.llamadas == 2
    assert res["metrics"]["backend"] == "local"
    assert res["metrics"]["fallback_motivo"] == "cortocircuito"


def test_llm_respaldo_remoto_ok():
    """Con el remoto sano, responde la nube y el local no se llama."""
    remoto = _RemotoLlmFalso(raw='{"type":"NONE"}')
    local = _LocalLlmFalso()
    w = nube.LlmConRespaldo(remoto, local,
                            breaker=nube.Cortocircuito(enfriamiento_s=30.0),
                            log=_log_nulo)
    res = w.stream_chat([], {})
    assert res["raw"] == '{"type":"NONE"}'
    assert res["metrics"]["backend"] == "nube"
    assert local.llamadas == 0
    assert w.breaker.permite() is True


def test_llm_respaldo_respuesta_vacia_cuenta_como_fallo():
    """Un remoto que devuelve raw vacío cuenta como fallo y abre el cortocircuito."""
    remoto = _RemotoLlmFalso(raw="")
    local = _LocalLlmFalso()
    w = nube.LlmConRespaldo(remoto, local,
                            breaker=nube.Cortocircuito(enfriamiento_s=30.0),
                            log=_log_nulo)
    res = w.stream_chat([], {})
    assert res["metrics"]["backend"] == "local"
    assert "respuesta vacía" in res["metrics"]["fallback_motivo"]
    assert w.breaker.fallos == 1
    assert w.breaker.permite() is False


def test_llm_respaldo_health():
    """health() es True si cualquiera de los dos lados está sano."""
    assert nube.LlmConRespaldo(_RemotoLlmFalso(sano=False), _LocalLlmFalso(sano=True),
                               log=_log_nulo).health() is True
    assert nube.LlmConRespaldo(_RemotoLlmFalso(sano=True), _LocalLlmFalso(sano=False),
                               log=_log_nulo).health() is True
    assert nube.LlmConRespaldo(_RemotoLlmFalso(sano=False), _LocalLlmFalso(sano=False),
                               log=_log_nulo).health() is False


# ===================================================================== 5) clientes_llm
def test_clientes_llm():
    """Cliente simple → una entrada; LlmConRespaldo → nube primero y local después."""
    simple = _LocalLlmFalso()
    assert nube.clientes_llm(simple) == [("", simple)]
    remoto, local = _RemotoLlmFalso(), _LocalLlmFalso()
    w = nube.LlmConRespaldo(remoto, local, log=_log_nulo)
    clientes = nube.clientes_llm(w)
    assert len(clientes) == 2
    assert clientes[0][1] is remoto
    assert clientes[1][1] is local
    assert clientes[0][0].strip() == "nube"
    assert clientes[1][0].strip() == "local"


# ===================================================================== 6) LlamaStreamClient real contra SSE falso
_OBJ_SSE = {"type": "NONE", "speech": 'Hola, "Mario". Bienvenido.', "trigger_text": ""}


def test_llama_stream_prefijo_token_y_on_speech():
    """SSE partido en trozos bajo /llm con Bearer: health, raw y on_speech una vez."""
    crudo = json.dumps(_OBJ_SSE, ensure_ascii=False)
    fragmentos = [crudo[i:i + 7] for i in range(0, len(crudo), 7)]
    srv = _servidor_llama_falso("t0k", fragmentos)
    with _servicio(srv):
        cli = LlamaStreamClient(url=f"http://127.0.0.1:{srv.server_address[1]}/llm",
                                timeout=3.0, token="t0k")
        assert cli.health() is True
        dichos = []
        res = cli.stream_chat([{"role": "user", "content": "hola"}], {},
                              on_speech=dichos.append)
        assert json.loads(res["raw"]) == _OBJ_SSE
        assert dichos == ['Hola, "Mario". Bienvenido.']
        assert res["metrics"]["t_speech_done"] is not None


def test_llama_stream_token_invalido():
    """Con token equivocado: health() False y stream_chat lanza RuntimeError 401."""
    srv = _servidor_llama_falso("t0k", [])
    with _servicio(srv):
        cli = LlamaStreamClient(url=f"http://127.0.0.1:{srv.server_address[1]}/llm",
                                timeout=3.0, token="malo")
        assert cli.health() is False
        with pytest.raises(RuntimeError) as ei:
            cli.stream_chat([{"role": "user", "content": "hola"}], {})
        assert "401" in str(ei.value)


# ===================================================================== 7) LLM: respaldo por timeout
def test_llm_respaldo_timeout_cae_a_local():
    """Remoto lento (2 s) con timeout 0.3 s → el local responde mucho antes de 2 s."""
    srv = _servidor_llama_falso("t0k", [], retraso_s=2.0)
    with _servicio(srv):
        remoto = LlamaStreamClient(url=f"http://127.0.0.1:{srv.server_address[1]}/llm",
                                   timeout=0.3, token="t0k")
        local = _LocalLlmFalso()
        logs = []
        w = nube.LlmConRespaldo(remoto, local,
                                breaker=nube.Cortocircuito(enfriamiento_s=30.0),
                                log=logs.append)
        t0 = time.perf_counter()
        res = w.stream_chat([], {})
        transcurrido = time.perf_counter() - t0
    assert transcurrido < 1.5
    assert res["raw"] == local.raw
    assert res["metrics"]["backend"] == "local"
    assert res["metrics"]["fallback_motivo"]
    assert any("LLM remoto falló" in m for m in logs)


# ===================================================================== 8) servidor de voz + remotos
def test_servidor_voz_asr_remoto(servidor_voz):
    """AsrRemoto contra el servidor falso: texto, backend, sr y muestras correctos."""
    r = _asr_remoto_directo(servidor_voz.base, tok="abc")
    t = np.arange(16000, dtype=np.float32) / 16000.0
    pcm = (0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)   # 1 s de seno
    out = r.transcribe(pcm, 16000)
    assert out["text"] == "hola"
    assert out["backend"] == "nube"
    assert out["ok"] is True
    assert servidor_voz.asr.sr == 16000
    assert servidor_voz.asr.muestras == 16000
    assert "t_start" not in out and "t_end" not in out
    assert out["latency_s"] > 0


def test_servidor_voz_tts_remoto_remuestrea(servidor_voz):
    """TtsRemoto recibe float32 a 16000 Hz remuestreado desde 44100 Hz (~8000 muestras)."""
    t = _tts_remoto_directo(servidor_voz.base, tok="abc", sr=16000)
    pcm, sr, meta = t.synthesize("Hola, bienvenido.", {"preset": "M2", "velocity": 1.0})
    assert pcm.dtype == np.float32
    assert sr == 16000
    assert abs(len(pcm) - 8000) <= 0.02 * 8000
    assert meta["backend"] == "nube"
    assert meta["engine"] == "supertonic-nube"
    assert meta["synth_srv_s"] is not None


def test_servidor_voz_token_invalido_y_health(servidor_voz):
    """Token malo → RuntimeError con «401»; GET /health con token → 200 y ok True."""
    malo = _asr_remoto_directo(servidor_voz.base, tok="equivocado")
    with pytest.raises(RuntimeError, match="401"):
        malo.transcribe(np.zeros(160, dtype=np.float32), 16000)
    conn = http.client.HTTPConnection("127.0.0.1", servidor_voz.puerto, timeout=2.0)
    try:
        conn.request("GET", "/health", headers={"Authorization": "Bearer abc"})
        resp = conn.getresponse()
        assert resp.status == 200
        datos = json.loads(resp.read())
        assert datos["ok"] is True
    finally:
        conn.close()


# ===================================================================== 9) respaldo ASR/TTS con puerto cerrado
def test_asr_respaldo_puerto_cerrado_abre_cortocircuito():
    """Remoto inalcanzable → local; el cortocircuito evita reintentos inmediatos."""
    remoto = _RemotoContador(
        nube.AsrRemoto(base=f"http://127.0.0.1:{_puerto_libre()}", timeout=0.3, tok="x"))
    local = _AsrLocalFalso()
    breaker = nube.Cortocircuito(enfriamiento_s=30.0)
    w = nube.AsrConRespaldo(remoto, local, breaker=breaker, log=_log_nulo)
    out = w.transcribe(np.zeros(160, dtype=np.float32), 16000)
    assert out["text"] == "texto local"
    assert remoto.llamadas == 1
    assert breaker.permite() is False
    out2 = w.transcribe(np.zeros(160, dtype=np.float32), 16000)
    assert out2["text"] == "texto local"
    assert remoto.llamadas == 1              # no se reintentó dentro del enfriamiento
    assert len(local.entradas) == 2


def test_asr_respaldo_ruta_str_y_rpc_local():
    """Una ruta (str) va directa al local sin tocar la nube; _rpc delega en el local."""
    remoto = _RemotoContador(
        nube.AsrRemoto(base=f"http://127.0.0.1:{_puerto_libre()}", timeout=0.3, tok="x"))
    local = _AsrLocalFalso()
    w = nube.AsrConRespaldo(remoto, local, breaker=nube.Cortocircuito(enfriamiento_s=30.0),
                            log=_log_nulo)
    out = w.transcribe("audio.wav", 16000)
    assert out["text"] == "texto local"
    assert remoto.llamadas == 0
    assert local.entradas[-1][0] == "audio.wav"
    assert w._rpc({"cmd": "ping"}) == {"ok": True, "eco": {"cmd": "ping"}}


def test_tts_respaldo_puerto_cerrado_abre_cortocircuito():
    """TTS remoto caído → motor local; segunda llamada sin reintentar la nube."""
    remoto = _RemotoContador(
        nube.TtsRemoto(base=f"http://127.0.0.1:{_puerto_libre()}", timeout=0.3, tok="x"))
    local = _TtsLocalFalso()
    breaker = nube.Cortocircuito(enfriamiento_s=30.0)
    w = nube.TtsConRespaldo(remoto, local, breaker=breaker, log=_log_nulo)
    pcm, sr, meta = w.synthesize("Hola.", {"preset": "M2", "velocity": 1.0})
    assert meta["engine"] == "null-falso"
    assert remoto.llamadas == 1
    assert breaker.permite() is False
    w.synthesize("Otra frase.", {"preset": "M2", "velocity": 1.0})
    assert remoto.llamadas == 1
    assert local.llamadas == 2
    assert w.name == "null-falso+nube"
    assert w.estimated is True


# ===================================================================== 10) TtsPrefetch
def test_tts_prefetch_acierto_y_fallos():
    """Acierto con misma clave (prefetch=True, hits=1); fallo si cambia texto o velocity."""
    motor = _MotorPrefetchFalso()
    p = tts.TtsPrefetch(motor)
    voz = {"preset": "M2", "velocity": 1.0}
    p.prefetch("Hola, Mario.", voz)
    pcm, sr, meta = p.synthesize("Hola, Mario.", dict(voz))
    assert meta.get("prefetch") is True
    assert motor.llamadas == 1
    assert p.hits == 1
    # texto distinto → sin prefetch, síntesis normal
    _, _, meta = p.synthesize("Otra frase diferente.", dict(voz))
    assert motor.llamadas == 2
    assert "prefetch" not in meta
    # velocity distinta → fallo de caché aunque el texto coincida
    p.prefetch("Hola, Mario.", {"preset": "M2", "velocity": 0.9})
    fut_vel = p._cache[("Hola, Mario.", "M2", 0.9)]
    _, _, meta = p.synthesize("Hola, Mario.", dict(voz))
    assert "prefetch" not in meta
    assert p.hits == 1
    fut_vel.result()                 # type: ignore[attr-defined] — es un Future
    # la pre-síntesis huérfana (0.9) terminó:
    assert motor.llamadas == 4       # 2 previas + huérfana del prefetch + directa


def test_tts_prefetch_fallo_en_hilo_no_rompe():
    """Si la síntesis previa falla en el hilo, synthesize vuelve a llamar al motor."""
    motor = _MotorPrefetchFalso(fallar_primera=True)
    p = tts.TtsPrefetch(motor)
    voz = {"preset": "M2", "velocity": 1.0}
    p.prefetch("Hola.", voz)
    pcm, sr, meta = p.synthesize("Hola.", dict(voz))
    assert motor.llamadas == 2               # 1ª en el hilo (falla), 2ª directa
    assert "prefetch" not in meta
    assert p.hits == 0
    assert len(pcm) == 1600 and sr == 16000


def test_null_tts_silencio():
    """NullTts devuelve silencio estimado (motor de respaldo sin instalación)."""
    n = tts.NullTts()
    pcm, sr, meta = n.synthesize("Hola, bienvenido al club.", {"preset": "M2"})
    assert sr == 16000
    assert np.all(pcm == 0)
    assert meta["estimated"] is True
    assert meta["engine"] == "null"


# ===================================================================== 11) contract.build_messages
_KB = None


def _kb() -> dict:
    """KB cargada una sola vez (el JSON no cambia entre pruebas)."""
    global _KB
    if _KB is None:
        _KB = contract.load_kb()
    return _KB


def test_build_messages_defecto_igual_a_cero(monkeypatch):
    """Por defecto (variable ausente) el prompt es idéntico a PREFIJO_ESTATICO=0."""
    persona = {"name": "Laura", "rank": "VIP"}
    ut = "¿A qué hora abre la biblioteca del club?"
    por_defecto = contract.build_messages("CONVERSATION", "VIP", persona, _kb(), utterance=ut)
    monkeypatch.setenv("HRI_PROMPT_PREFIJO_ESTATICO", "0")
    con_cero = contract.build_messages("CONVERSATION", "VIP", persona, _kb(), utterance=ut)
    assert por_defecto == con_cero
    assert len(por_defecto) == 1 and por_defecto[0]["role"] == "system"


def test_build_messages_prefijo_estatico_contenido(monkeypatch):
    """Con PREFIJO_ESTATICO=1: KB primero, nombre, instrucción del turno y campos."""
    monkeypatch.setenv("HRI_PROMPT_PREFIJO_ESTATICO", "1")
    persona = {"name": "Laura", "rank": "VIP"}
    utterance = "¿A qué hora abre la biblioteca del club?"
    msgs = contract.build_messages("CONVERSATION", "VIP", persona, _kb(),
                                   utterance=utterance)
    # system (KB → tarjeta → nombre → campos) + user (instrucción del estado con la frase)
    assert [m["role"] for m in msgs] == ["system", "user"]
    contenido = msgs[0]["content"]
    assert contenido.startswith(contract.kb_text(_kb(), None))
    assert "Laura" in contenido
    assert "type, speech, trigger_text, intent, area" in contenido
    assert utterance in msgs[1]["content"]


def test_build_messages_prefijo_comun_mas_largo(monkeypatch):
    """Con =1, dos estados distintos comparten un prefijo más largo (caché de prefijo)."""
    persona = {"name": "Laura", "rank": "VIP"}

    def contenido(estado: str, bandera: str) -> str:
        monkeypatch.setenv("HRI_PROMPT_PREFIJO_ESTATICO", bandera)
        ut = "¿A qué hora abre la biblioteca?" if estado == "CONVERSATION" else None
        return contract.build_messages(estado, "VIP", persona, _kb(), utterance=ut)[0]["content"]

    pref0 = os.path.commonprefix([contenido("CONVERSATION", "0"), contenido("FAREWELL", "0")])
    pref1 = os.path.commonprefix([contenido("CONVERSATION", "1"), contenido("FAREWELL", "1")])
    assert len(pref1) > len(pref0)


# ===================================================================== 12) HriLLM y on_speech
_GREETING_VALIDO = {
    "type": "GREETING_GUEST",
    "speech": "Bienvenido a Club N, Mario; es un gusto recibirle hoy en nuestra comunidad.",
    "trigger_text": "Bienvenido a Club",
}


class _ClienteSinOnSpeech:
    """stream_chat SIN parámetro on_speech: exige que HriLLM no lo reenvíe si es None."""

    def __init__(self, raw: str):
        self.raw = raw
        self.llamadas = 0

    def stream_chat(self, messages, schema, temperature=0.2):
        self.llamadas += 1
        return {"raw": self.raw, "metrics": {"t_total": 0.01}}


class _ClienteConKwargs:
    """stream_chat con **kw: registra lo que HriLLM reenvía."""

    def __init__(self, raw: str):
        self.raw = raw
        self.ultimo_kw = {}

    def stream_chat(self, messages, schema, **kw):
        self.ultimo_kw = kw
        return {"raw": self.raw, "metrics": {"t_total": 0.01}}


def test_hrillm_funciona_sin_on_speech():
    """Con on_speech=None, HriLLM llama al cliente sin pasarle on_speech (sin TypeError)."""
    assert _GREETING_VALIDO["type"] in contract.allowed_types("GREETING", "GUEST")
    crudo = json.dumps(_GREETING_VALIDO, ensure_ascii=False)
    cliente = _ClienteSinOnSpeech(crudo)
    llm = HriLLM(client=cliente, kb=_kb())
    assert llm.on_speech is None
    r = llm.respond("GREETING", "GUEST", {"name": "Mario", "rank": "GUEST"})
    assert cliente.llamadas == 1
    assert r["obj"] is not None
    assert r["errors"] == []
    assert r["retry_used"] is False
    assert r["obj"]["type"] == "GREETING_GUEST"


def test_hrillm_reenvia_on_speech_cuando_existe():
    """Con on_speech definido, HriLLM lo reenvía tal cual a stream_chat."""
    crudo = json.dumps(_GREETING_VALIDO, ensure_ascii=False)
    cliente = _ClienteConKwargs(crudo)
    llm = HriLLM(client=cliente, kb=_kb())
    recibidos = []
    llm.on_speech = recibidos.append
    r = llm.respond("GREETING", "GUEST", {"name": "Mario", "rank": "GUEST"})
    assert cliente.ultimo_kw.get("on_speech") is llm.on_speech
    assert r["obj"] is not None
