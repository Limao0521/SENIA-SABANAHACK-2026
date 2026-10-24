"""Servidor de voz para la VPS con GPU: ASR (faster-whisper) + TTS (Supertonic) por HTTP.

Corre en la nube detrás del gateway (Caddy, deploy/nube/), que ya exige el token; aun
así este proceso vuelve a comprobar ``Authorization: Bearer $HRI_NUBE_TOKEN`` (defensa
en profundidad: si alguien expone el puerto 9000 por error, sigue cerrado).

Endpoints::

    GET  /health                       → {"ok": true, "asr": bool, "tts": bool}
    POST /v1/asr   (PCM int16 LE mono; cabecera X-Sample-Rate)
                                        → {"ok", "text", "latency_s", "audio_dur_s"}
    POST /v1/tts   {"text", "preset", "velocity", "sr", "steps"}
                                        → PCM int16 LE mono; cabeceras X-Sample-Rate, X-Synth-S

Misma voz que a bordo (Supertonic, presets M1–M5 de personas.VOICE) para que un respaldo
local a mitad de visita no cambie el timbre. Arranque (lo hace el contenedor)::

    HRI_NUBE_TOKEN=... python -m x2_ultra.hri_runtime.nube_voz --host 0.0.0.0 --port 9000

Variables: HRI_ASR_MODEL / HRI_ASR_DEVICE / HRI_ASR_COMPUTE (asr/__init__.py; en GPU de
servidor conviene ``float16``), SUPERTONIC_MODEL_DIR / SUPERTONIC_THREADS / SUPERTONIC_AUTO_DOWNLOAD.
"""
from __future__ import annotations

import argparse
import hmac
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional

import numpy as np

MAX_AUDIO_BYTES = 16000 * 2 * 30          # 30 s de PCM16 a 16 kHz
MAX_TEXT_CHARS = 600


try:      # importar al cargar el módulo: en frío cuesta ~1,3 s y no debe caer en la 1ª petición
    from scipy.signal import resample_poly as _resample_poly
except ImportError:                                   # pragma: no cover
    _resample_poly = None


def resample(x: np.ndarray, sr_in: int, sr_out: int) -> np.ndarray:
    if sr_in == sr_out or len(x) == 0:
        return x.astype(np.float32)
    if _resample_poly is not None:
        from math import gcd
        g = gcd(int(sr_in), int(sr_out))
        return _resample_poly(x, sr_out // g, sr_in // g).astype(np.float32)
    else:
        n = int(round(len(x) * sr_out / sr_in))
        return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)


class Motores:
    """Carga perezosa (o al arrancar con --calentar) de ASR y TTS; un lock por motor."""

    def __init__(self, asr=None, tts=None):
        self._asr, self._tts = asr, tts
        self._lk_asr, self._lk_tts = threading.Lock(), threading.Lock()

    def asr(self):
        with self._lk_asr:
            if self._asr is None:
                from .asr import Asr
                self._asr = Asr()
            return self._asr

    def tts(self, steps: Optional[int] = None):
        if self._tts is None:
            with self._lk_tts:
                if self._tts is None:
                    from .tts import SupertonicTts
                    self._tts = SupertonicTts(
                        model_dir=os.environ.get("SUPERTONIC_MODEL_DIR"),
                        auto_download=bool(os.environ.get("SUPERTONIC_AUTO_DOWNLOAD")),
                        total_steps=int(steps or os.environ.get("SUPERTONIC_STEPS", 8)),
                        threads=int(os.environ.get("SUPERTONIC_THREADS", 4)))
        return self._tts

    def sintetizar(self, text: str, voice: dict, steps: Optional[int] = None):
        """Síntesis serializada (un robot por servidor); ``steps`` por petición si el motor
        lo admite (SupertonicTts._total_steps)."""
        eng = self.tts(steps)
        with self._lk_tts:
            prev = getattr(eng, "_total_steps", None)
            if steps and prev is not None:
                eng._total_steps = int(steps)
            try:
                return eng.synthesize(text, voice)
            finally:
                if prev is not None:
                    eng._total_steps = prev

    def estado(self) -> dict:
        return {"asr": self._asr is not None, "tts": self._tts is not None}


def make_handler(motores: Motores, tok: str):
    class Handler(BaseHTTPRequestHandler):
        server_version = "x2-nube-voz/1"
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt: str, *args: Any) -> None:      # una línea por petición
            sys.stderr.write(f"[voz] {self.address_string()} {fmt % args}\n")

        # ---------------------------------------------------------- utilidades
        def _auth(self) -> bool:
            if not tok:
                return True
            got = self.headers.get("Authorization", "")
            if hmac.compare_digest(got.encode(), f"Bearer {tok}".encode()):
                return True
            self._json(401, {"ok": False, "error": "token inválido"})
            return False

        def _json(self, status: int, obj: dict) -> None:
            body = json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _body(self, limit: int) -> Optional[bytes]:
            n = int(self.headers.get("Content-Length") or 0)
            if n <= 0 or n > limit:
                self._json(413 if n > limit else 400, {"ok": False, "error": f"cuerpo de {n} bytes"})
                return None
            return self.rfile.read(n)

        # ---------------------------------------------------------- rutas
        def do_GET(self) -> None:
            if not self._auth():
                return
            if self.path.rstrip("/") == "/health":
                self._json(200, {"ok": True, **motores.estado()})
            else:
                self._json(404, {"ok": False, "error": "ruta"})

        def do_POST(self) -> None:
            if not self._auth():
                return
            try:
                if self.path == "/v1/asr":
                    self._asr()
                elif self.path == "/v1/tts":
                    self._tts()
                else:
                    self._json(404, {"ok": False, "error": "ruta"})
            except Exception as exc:                         # nunca tumbar el servidor
                self._json(500, {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:300]})

        def _asr(self) -> None:
            raw = self._body(MAX_AUDIO_BYTES * 3)
            if raw is None:
                return
            sr = int(self.headers.get("X-Sample-Rate") or 16000)
            pcm = np.frombuffer(raw[: len(raw) // 2 * 2], dtype="<i2").astype(np.float32) / 32768.0
            out = motores.asr().transcribe(pcm, sr)
            out.pop("t_start", None)
            out.pop("t_end", None)
            self._json(200, {"ok": True, **out})

        def _tts(self) -> None:
            raw = self._body(16384)
            if raw is None:
                return
            req = json.loads(raw)
            text = str(req.get("text") or "").strip()[:MAX_TEXT_CHARS]
            if not text:
                self._json(400, {"ok": False, "error": "text vacío"})
                return
            t0 = time.perf_counter()
            voice = {"preset": str(req.get("preset", "M2")), "velocity": float(req.get("velocity", 1.0))}
            pcm, sr, _meta = motores.sintetizar(text, voice, req.get("steps"))
            sr_out = int(req.get("sr") or sr)
            pcm = resample(np.asarray(pcm, dtype=np.float32), sr, sr_out)
            body = (np.clip(pcm, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
            self.send_response(200)
            self.send_header("Content-Type", "audio/L16")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Sample-Rate", str(sr_out))
            self.send_header("X-Synth-S", f"{time.perf_counter() - t0:.4f}")
            self.end_headers()
            self.wfile.write(body)

    return Handler


def make_server(host: str, port: int, motores: Optional[Motores] = None,
                tok: Optional[str] = None) -> ThreadingHTTPServer:
    tok = os.environ.get("HRI_NUBE_TOKEN", "") if tok is None else tok
    srv = ThreadingHTTPServer((host, port), make_handler(motores or Motores(), tok))
    srv.daemon_threads = True
    return srv


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=9000)
    ap.add_argument("--calentar", action="store_true", help="carga ASR y TTS antes de escuchar")
    ap.add_argument("--sin-token", action="store_true", help="solo pruebas locales: no exige token")
    a = ap.parse_args(argv)
    tok = os.environ.get("HRI_NUBE_TOKEN", "")
    if not tok and not a.sin_token:
        print("ERROR: falta HRI_NUBE_TOKEN (o --sin-token para pruebas locales)", file=sys.stderr)
        return 2
    motores = Motores()
    if a.calentar:
        t0 = time.perf_counter()
        motores.asr()
        motores.tts().synthesize("Hola, bienvenido.", {"preset": "M2", "velocity": 1.0})
        print(f"[voz] motores calientes en {time.perf_counter() - t0:.1f} s", flush=True)
    srv = make_server(a.host, a.port, motores, tok)
    print(f"[voz] escuchando en {a.host}:{a.port}", flush=True)
    srv.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
