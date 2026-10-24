"""Inferencia en la nube con respaldo local automático (LLM, ASR y TTS).

Todo es opt-in por entorno; sin ``HRI_NUBE_URL`` nada cambia y el robot usa la pila
a bordo exactamente como antes. Con la variable definida, cada etapa listada en
``HRI_NUBE_ETAPAS`` intenta primero la nube y, si falla o tarda más que su timeout,
cae a la ruta local en el mismo turno. Tras un fallo la etapa queda en local durante
``HRI_NUBE_ENFRIAMIENTO_S`` (cortocircuito) para no pagar el timeout en cada turno.

Variables (ver docs/reto-2/INFERENCIA_NUBE.md):
    HRI_NUBE_URL            base del gateway, p. ej. http://10.0.1.50:8443 (relé del PC)
                            o https://nube.ejemplo.co (vacío ⇒ todo local)
    HRI_NUBE_TOKEN          token Bearer del gateway (solo por entorno; nunca en el repo)
    HRI_NUBE_ETAPAS         subconjunto de "llm,asr,tts" (defecto: las tres)
    HRI_NUBE_LLM_URL        override del endpoint LLM (defecto: $HRI_NUBE_URL/llm)
    HRI_NUBE_LLM_TTFT_S     espera máxima al primer byte del LLM remoto (defecto 3.0)
    HRI_NUBE_LLM_LECTURA_S  silencio máximo entre trozos del stream remoto (defecto 3.0)
    HRI_NUBE_ASR_TIMEOUT_S  timeout total de una transcripción remota (defecto 4.0)
    HRI_NUBE_TTS_TIMEOUT_S  timeout total de una frase TTS remota (defecto 3.0)
    HRI_NUBE_TTS_SR         frecuencia pedida al TTS remoto (defecto HRI_PLAYBACK_SR o 16000)
    HRI_NUBE_TTS_STEPS      pasos de difusión del Supertonic remoto (defecto 8: calidad plena)
    HRI_NUBE_ENFRIAMIENTO_S segundos en local tras un fallo antes de reintentar (defecto 30)
    HRI_NUBE_SONDA_S        periodo de la sonda de salud en segundo plano (defecto 5; 0 = off)
"""
from __future__ import annotations

import http.client
import json
import os
import ssl
import threading
import time
from typing import Any, Callable, Optional
from urllib.parse import urlparse

import numpy as np

ETAPAS = ("llm", "asr", "tts")


# ------------------------------------------------------------------ configuración
def _f(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return float(default)


def nube_url() -> str:
    return os.environ.get("HRI_NUBE_URL", "").strip().rstrip("/")


def etapas_activas() -> set[str]:
    """Etapas a descargar en la nube; vacío si no hay HRI_NUBE_URL."""
    if not nube_url():
        return set()
    raw = os.environ.get("HRI_NUBE_ETAPAS", ",".join(ETAPAS))
    return {e.strip().lower() for e in raw.split(",") if e.strip().lower() in ETAPAS}


def activa(etapa: str) -> bool:
    return etapa in etapas_activas()


def token() -> str:
    return os.environ.get("HRI_NUBE_TOKEN", "").strip()


def auth_headers(tok: Optional[str] = None) -> dict[str, str]:
    tok = token() if tok is None else tok
    return {"Authorization": f"Bearer {tok}"} if tok else {}


class Endpoint:
    """URL parseada (http/https, host, puerto, prefijo de ruta) + conexión stdlib."""

    def __init__(self, url: str):
        u = urlparse(url)
        self.url = url.rstrip("/")
        self.https = u.scheme == "https"
        self.host = u.hostname or "127.0.0.1"
        self.port = u.port or (443 if self.https else 80)
        self.prefix = (u.path or "").rstrip("/")

    def conn(self, timeout: float) -> http.client.HTTPConnection:
        if self.https:
            return http.client.HTTPSConnection(self.host, self.port, timeout=timeout,
                                               context=ssl.create_default_context())
        return http.client.HTTPConnection(self.host, self.port, timeout=timeout)

    def path(self, p: str) -> str:
        return self.prefix + p


# ------------------------------------------------------------------ cortocircuito
class Cortocircuito:
    """Tras un fallo, la etapa queda en local ``enfriamiento_s``; luego se reintenta."""

    def __init__(self, enfriamiento_s: Optional[float] = None, reloj: Callable[[], float] = time.monotonic):
        self.enfriamiento_s = _f("HRI_NUBE_ENFRIAMIENTO_S", 30.0) if enfriamiento_s is None else enfriamiento_s
        self.reloj = reloj
        self._abierto_hasta = 0.0
        self.fallos = 0
        self.ultimo_motivo = ""
        self._lock = threading.Lock()

    def permite(self) -> bool:
        return self.reloj() >= self._abierto_hasta

    def fallo(self, motivo: str) -> None:
        with self._lock:
            self.fallos += 1
            self.ultimo_motivo = motivo
            self._abierto_hasta = self.reloj() + self.enfriamiento_s

    def exito(self) -> None:
        with self._lock:
            self._abierto_hasta = 0.0

    def vigilar(self, chequeo: Callable[[], bool], periodo_s: Optional[float] = None,
                log: Callable[[str], None] = print) -> Optional[threading.Thread]:
        """Sonda de salud en segundo plano (HRI_NUBE_SONDA_S, defecto 5 s; 0 = sin sonda):
        si la nube cae ENTRE turnos, el siguiente turno ya va a local sin pagar el timeout;
        cuando vuelve, se reabre sin esperar el enfriamiento."""
        periodo = _f("HRI_NUBE_SONDA_S", 5.0) if periodo_s is None else periodo_s
        if periodo <= 0:
            return None

        def bucle() -> None:
            sano_prev = True
            while True:
                time.sleep(periodo)
                try:
                    sano = bool(chequeo())
                except Exception:
                    sano = False
                if not sano:
                    self.fallo("sonda de salud")
                elif not sano_prev:
                    self.exito()
                if sano != sano_prev:
                    log(f"[nube] sonda: {'nube de vuelta' if sano else 'nube caída; uso local'}")
                sano_prev = sano

        hilo = threading.Thread(target=bucle, name="nube-sonda", daemon=True)
        hilo.start()
        return hilo


def _log_default(msg: str) -> None:
    print(msg, flush=True)


# ------------------------------------------------------------------ LLM
class LlmConRespaldo:
    """Misma interfaz que ``LlamaStreamClient`` (health/stream_chat): nube primero con
    timeout de primer token, y si falla, el llama-server local en el mismo turno."""

    def __init__(self, remoto, local, breaker: Optional[Cortocircuito] = None,
                 log: Callable[[str], None] = _log_default):
        self.remoto, self.local = remoto, local
        self.breaker = breaker or Cortocircuito()
        self.log = log
        self.timeout = getattr(local, "timeout", 120.0)

    def health(self) -> bool:
        return bool(self.local.health() or self.remoto.health())

    def stream_chat(self, messages: list[dict], schema: dict, **kw) -> dict[str, Any]:
        motivo = ""
        if self.breaker.permite():
            t0 = time.perf_counter()
            try:
                res = self.remoto.stream_chat(messages, schema, **kw)
                if not res.get("raw"):
                    raise RuntimeError("respuesta vacía")
                self.breaker.exito()
                res["metrics"] = {**res.get("metrics", {}), "backend": "nube"}
                return res
            except Exception as exc:                      # red caída, 401, timeout, 5xx…
                motivo = f"{type(exc).__name__}: {exc}"[:200]
                self.breaker.fallo(motivo)
                self.log(f"[nube] LLM remoto falló tras {time.perf_counter() - t0:.2f} s ({motivo}); "
                         f"respaldo local {self.breaker.enfriamiento_s:.0f} s")
        else:
            motivo = "cortocircuito"
        res = self.local.stream_chat(messages, schema, **kw)
        res["metrics"] = {**res.get("metrics", {}), "backend": "local", "fallback_motivo": motivo}
        return res


def clientes_llm(client) -> list[tuple[str, Any]]:
    """Clientes a calentar: [("", client)] en local; nube y respaldo local si hay nube."""
    if isinstance(client, LlmConRespaldo):
        return [("nube ", client.remoto), ("local ", client.local)]
    return [("", client)]


def make_llm_client(log: Callable[[str], None] = _log_default):
    """Cliente LLM según entorno: local (como siempre) o nube+respaldo local."""
    from .llm_client import LlamaStreamClient
    local = LlamaStreamClient()
    if not activa("llm"):
        return local
    url = os.environ.get("HRI_NUBE_LLM_URL", "").strip() or nube_url() + "/llm"
    remoto = LlamaStreamClient(url=url, timeout=max(_f("HRI_NUBE_LLM_TTFT_S", 3.0),
                                                     _f("HRI_NUBE_LLM_LECTURA_S", 3.0)),
                               token=token())
    log(f"[nube] LLM: {url} (TTFT ≤ {_f('HRI_NUBE_LLM_TTFT_S', 3.0):.1f} s) con respaldo local {local.url}")
    breaker = Cortocircuito()
    breaker.vigilar(remoto.health, log=log)
    return LlmConRespaldo(remoto, local, breaker=breaker, log=log)


# ------------------------------------------------------------------ ASR
class AsrRemoto:
    """POST {base}/voz/v1/asr con PCM int16 LE mono; devuelve el dict de ``Asr.transcribe``."""

    def __init__(self, base: Optional[str] = None, timeout: Optional[float] = None,
                 tok: Optional[str] = None):
        self.ep = Endpoint((base or nube_url()) + "/voz")
        self.timeout = _f("HRI_NUBE_ASR_TIMEOUT_S", 4.0) if timeout is None else timeout
        self.tok = token() if tok is None else tok

    def transcribe(self, pcm, sr: int = 16000) -> dict:
        x = np.clip(np.asarray(pcm, dtype=np.float32).reshape(-1), -1.0, 1.0)
        body = (x * 32767.0).astype("<i2").tobytes()
        t0 = time.perf_counter()
        conn = self.ep.conn(self.timeout)
        try:
            conn.request("POST", self.ep.path("/v1/asr"), body,
                         {"Content-Type": "application/octet-stream", "X-Sample-Rate": str(int(sr)),
                          **auth_headers(self.tok)})
            resp = conn.getresponse()
            data = resp.read()
            if resp.status != 200:
                raise RuntimeError(f"HTTP {resp.status}: {data[:200]!r}")
            out = json.loads(data)
        finally:
            conn.close()
        if not out.get("ok", True):
            raise RuntimeError(f"ASR remoto: {out.get('error')}")
        out["latency_s"] = time.perf_counter() - t0       # incluye red (lo que ve el turno)
        out["backend"] = "nube"
        return out


class AsrConRespaldo:
    """Envuelve el ``AsrClient`` local (socket del contenedor x2-asr)."""

    def __init__(self, remoto: AsrRemoto, local, breaker: Optional[Cortocircuito] = None,
                 log: Callable[[str], None] = _log_default):
        self.remoto, self.local = remoto, local
        self.breaker = breaker or Cortocircuito()
        self.log = log

    def transcribe(self, wav_path_or_pcm, sr: int = 16000) -> dict:
        if self.breaker.permite() and not isinstance(wav_path_or_pcm, (str, os.PathLike)):
            try:
                out = self.remoto.transcribe(wav_path_or_pcm, sr)
                self.breaker.exito()
                return out
            except Exception as exc:
                motivo = f"{type(exc).__name__}: {exc}"[:200]
                self.breaker.fallo(motivo)
                self.log(f"[nube] ASR remoto falló ({motivo}); respaldo local")
        return self.local.transcribe(wav_path_or_pcm, sr)

    def _rpc(self, msg: dict) -> dict:                   # ping/stop van al worker local
        return self.local._rpc(msg)

    def stop(self, *a, **k) -> None:
        stop = getattr(self.local, "stop", None)
        if stop:
            stop(*a, **k)


def envolver_asr(local, log: Callable[[str], None] = _log_default):
    if local is None or not activa("asr") or isinstance(local, AsrConRespaldo):
        return local
    log(f"[nube] ASR: {nube_url()}/voz (timeout {_f('HRI_NUBE_ASR_TIMEOUT_S', 4.0):.1f} s) con respaldo local")
    breaker = Cortocircuito()
    breaker.vigilar(salud_voz, log=log)
    return AsrConRespaldo(AsrRemoto(), local, breaker=breaker, log=log)


# ------------------------------------------------------------------ TTS
class TtsRemoto:
    """POST {base}/voz/v1/tts → PCM int16 LE mono (cabecera X-Sample-Rate)."""

    name = "supertonic-nube"
    estimated = False

    def __init__(self, base: Optional[str] = None, timeout: Optional[float] = None,
                 tok: Optional[str] = None, sr: Optional[int] = None, steps: Optional[int] = None):
        self.ep = Endpoint((base or nube_url()) + "/voz")
        self.timeout = _f("HRI_NUBE_TTS_TIMEOUT_S", 3.0) if timeout is None else timeout
        self.tok = token() if tok is None else tok
        self.sr = int(sr or os.environ.get("HRI_NUBE_TTS_SR") or os.environ.get("HRI_PLAYBACK_SR") or 16000)
        self.steps = int(steps or os.environ.get("HRI_NUBE_TTS_STEPS") or 8)

    def synthesize(self, sentence: str, voice: dict) -> tuple[np.ndarray, int, dict]:
        t0 = time.perf_counter()
        payload = {"text": sentence, "preset": str(voice.get("preset", "M2")),
                   "velocity": float(voice.get("velocity", 1.0)), "sr": self.sr, "steps": self.steps}
        conn = self.ep.conn(self.timeout)
        try:
            conn.request("POST", self.ep.path("/v1/tts"), json.dumps(payload),
                         {"Content-Type": "application/json", **auth_headers(self.tok)})
            resp = conn.getresponse()
            data = resp.read()
            if resp.status != 200:
                raise RuntimeError(f"HTTP {resp.status}: {data[:200]!r}")
            sr = int(resp.getheader("X-Sample-Rate") or self.sr)
            synth_srv = resp.getheader("X-Synth-S")
        finally:
            conn.close()
        if len(data) < 2:
            raise RuntimeError("audio vacío")
        pcm = np.frombuffer(data[: len(data) // 2 * 2], dtype="<i2").astype(np.float32) / 32768.0
        return pcm, sr, {"engine": self.name, "dur_s": len(pcm) / sr, "estimated": False,
                         "synth_s": time.perf_counter() - t0,
                         "synth_srv_s": float(synth_srv) if synth_srv else None,
                         "voice": payload["preset"], "velocity": payload["velocity"], "backend": "nube"}


class TtsConRespaldo:
    """Misma interfaz que ``TtsBase``; la voz (preset Supertonic) es la misma en ambos lados,
    así un respaldo a mitad de visita no cambia el timbre."""

    estimated = False

    def __init__(self, remoto: TtsRemoto, local, breaker: Optional[Cortocircuito] = None,
                 log: Callable[[str], None] = _log_default):
        self.remoto, self.local = remoto, local
        self.breaker = breaker or Cortocircuito()
        self.log = log
        self.name = f"{getattr(local, 'name', 'local')}+nube"
        self.estimated = bool(getattr(local, "estimated", False))

    def synthesize(self, sentence: str, voice: dict):
        if self.breaker.permite():
            try:
                out = self.remoto.synthesize(sentence, voice)
                self.breaker.exito()
                return out
            except Exception as exc:
                motivo = f"{type(exc).__name__}: {exc}"[:200]
                self.breaker.fallo(motivo)
                self.log(f"[nube] TTS remoto falló ({motivo}); respaldo local")
        return self.local.synthesize(sentence, voice)


def envolver_tts(local, log: Callable[[str], None] = _log_default):
    if local is None or not activa("tts") or isinstance(local, TtsConRespaldo):
        return local
    log(f"[nube] TTS: {nube_url()}/voz (timeout {_f('HRI_NUBE_TTS_TIMEOUT_S', 3.0):.1f} s) con respaldo local")
    breaker = Cortocircuito()
    breaker.vigilar(salud_voz, log=log)
    return TtsConRespaldo(TtsRemoto(), local, breaker=breaker, log=log)


# ------------------------------------------------------------------ diagnóstico
def salud_voz(timeout: float = 2.0) -> bool:
    """GET $HRI_NUBE_URL/voz/health con el token → True si responde 200."""
    ep = Endpoint(nube_url() + "/voz")
    try:
        conn = ep.conn(timeout)
        conn.request("GET", ep.path("/health"), headers=auth_headers())
        ok = conn.getresponse().status == 200
        conn.close()
        return ok
    except Exception:
        return False


def sondear(timeout: float = 3.0) -> dict:
    """GET /llm/health y /voz/health con el token; mide RTT. Para scripts y arranque."""
    base = nube_url()
    out: dict[str, Any] = {"url": base, "etapas": sorted(etapas_activas())}
    if not base:
        out["error"] = "HRI_NUBE_URL vacío"
        return out
    for nombre, p in (("llm", "/llm/health"), ("voz", "/voz/health")):
        ep = Endpoint(base)
        t0 = time.perf_counter()
        try:
            conn = ep.conn(timeout)
            conn.request("GET", ep.path(p), headers=auth_headers())
            r = conn.getresponse()
            body = r.read()[:300]
            conn.close()
            out[nombre] = {"status": r.status, "rtt_ms": round((time.perf_counter() - t0) * 1000, 1),
                           "body": body.decode("utf-8", "replace")}
        except Exception as exc:
            out[nombre] = {"error": f"{type(exc).__name__}: {exc}"}
    return out


if __name__ == "__main__":
    print(json.dumps(sondear(), ensure_ascii=False, indent=2))
