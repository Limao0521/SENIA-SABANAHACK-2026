"""Cliente HTTP de streaming hacia llama-server (contenedor x2-llm).

Lógica adaptada de HRIEGO_gesture/opt/t1_schema_stream/bench.py (read-only):
streaming SSE sobre /v1/chat/completions con response_format json_schema strict,
métricas de latencia (first token / type known / first sentence / speech done).

NOTA DE RED (opt/t1_schema_stream/REPORT.md): con docker rootless + pasta el host
NO alcanza los puertos publicados; 127.0.0.1:8080 solo es reachable dentro del
netns de rootlesskit. Helper abajo: `nsenter_argv()` / `runner_note()`. Arranque:
  cd /home/luis/GitHub/x2_ultra/llm && \
    /home/luis/bin/docker compose -f docker-compose.yml -f compose.x86-cuda.yml up -d
Y el smoke desde el netns:
  PYTHONPATH=src nsenter -t $(python -c "...find_rootlesskit_pid...") -U -n --preserve-credentials \
    /home/luis/GitHub/x2_ultra-retos/.venv/bin/python -m x2_ultra.hri_runtime.llm_client --smoke

Endpoint configurable: env HRI_LLM_URL (default http://127.0.0.1:8080).
"""
from __future__ import annotations

import http.client
import json
import os
import re
import shutil
import subprocess
import time
from typing import Any, Callable, Optional
from urllib.parse import urlparse

from . import contract
from .personas import card_text  # noqa: F401  (re-export conveniente)

DEFAULT_URL = "http://127.0.0.1:8080"
TYPE_RE = re.compile(r'"type"\s*:\s*"([A-Za-z_]+)"')
SPEECH_RE = re.compile(r'"speech"\s*:\s*"')
SENT_END = ".!?…"


def llm_url() -> str:
    return os.environ.get("HRI_LLM_URL", DEFAULT_URL).rstrip("/")


# ---------------------------------------------------------------- netns helpers
def find_rootlesskit_pid() -> Optional[int]:
    """PID cuyo netns expone 127.0.0.1:8080 (docker rootless). OJO: es el HIJO de
    rootlesskit (comm "exe"), no el padre — nsenter sobre el padre da 'Invalid
    argument' (verificado en vivo; coincide con opt/t1_schema_stream/REPORT.md)."""
    try:
        # -x: nombre exacto (pgrep -f falsea con cualquier cmdline que mencione "rootlesskit")
        out = subprocess.run(["pgrep", "-x", "rootlesskit"], capture_output=True, text=True, timeout=5).stdout
        pids = [int(p) for p in out.split() if p.isdigit()]
        for pid in pids:
            kids = subprocess.run(["pgrep", "-P", str(pid)], capture_output=True, text=True, timeout=5).stdout
            child = [int(k) for k in kids.split() if k.isdigit()]
            if child:
                return child[0]
        return pids[0] if pids else None
    except (OSError, subprocess.SubprocessError):
        return None


def nsenter_argv(cmd: list[str], pid: Optional[int] = None) -> list[str]:
    """Envuelve `cmd` para correr dentro del netns de rootlesskit (ver REPORT.md)."""
    pid = pid or find_rootlesskit_pid()
    if pid is None:
        raise RuntimeError("rootlesskit no encontrado; ¿docker rootless activo?")
    return ["nsenter", "-t", str(pid), "-U", "-n", "--preserve-credentials", *cmd]


def runner_note() -> str:
    pid = find_rootlesskit_pid()
    py = shutil.which("python3") or "python3"
    return ("El puerto 8080 de x2-llm solo es alcanzable dentro del netns de rootlesskit "
            f"(docker rootless + pasta). Corre el cliente así:\n"
            f"  PYTHONPATH=src nsenter -t {pid or '<rootlesskit-pid>'} -U -n --preserve-credentials "
            f"{py} -m x2_ultra.hri_runtime.llm_client --smoke")


def scan_speech(buf: str) -> tuple[bool, bool]:
    """(first_sentence_end_seen, speech_closed) — igual que bench.py."""
    m = SPEECH_RE.search(buf)
    if not m:
        return False, False
    i, first, n = m.end(), False, len(buf)
    while i < n:
        c = buf[i]
        if c == "\\":
            i += 2
            continue
        if c == '"':
            return first, True
        if c in SENT_END:
            first = True
        i += 1
    return first, False


class LlamaStreamClient:
    """Cliente streaming mínimo (stdlib) contra llama-server OpenAI-compatible."""

    def __init__(self, url: Optional[str] = None, timeout: float = 120.0, token: Optional[str] = None):
        # http://host:puerto (local, como siempre) o https://host/prefijo (gateway de la nube,
        # p. ej. .../llm): el prefijo se antepone a /health y /v1/chat/completions.
        self.url = (url or llm_url()).rstrip("/")
        u = urlparse(self.url)
        self.https = u.scheme == "https"
        self.host = u.hostname or "127.0.0.1"
        self.port = u.port or (443 if self.https else 8080)
        self.prefix = (u.path or "").rstrip("/")
        self.timeout = timeout
        self.headers = {"Authorization": f"Bearer {token}"} if token else {}

    def _conn(self, timeout: float) -> http.client.HTTPConnection:
        if self.https:
            import ssl
            return http.client.HTTPSConnection(self.host, self.port, timeout=timeout,
                                               context=ssl.create_default_context())
        return http.client.HTTPConnection(self.host, self.port, timeout=timeout)

    def health(self) -> bool:
        try:
            conn = self._conn(3)
            conn.request("GET", self.prefix + "/health", headers=self.headers)
            ok = conn.getresponse().status == 200
            conn.close()
            return ok
        except OSError:
            return False

    def stream_chat(self, messages: list[dict], schema: dict, *, temperature: float = 0.2,
                    max_tokens: int = 320, seed: Optional[int] = None,
                    on_speech: Optional[Callable[[str], None]] = None,
                    on_speech_partial: Optional[Callable[[str], None]] = None) -> dict[str, Any]:
        """``on_speech(texto)`` (opcional) se llama en cuanto el campo speech se cierra en el
        stream, antes de que termine el JSON: permite pre-sintetizar el TTS (HRI_TTS_PREFETCH)."""
        payload = {
            "model": "local", "messages": messages, "max_tokens": max_tokens, "temperature": temperature,
            "chat_template_kwargs": {"enable_thinking": False},
            "stream": True, "stream_options": {"include_usage": True},
            "response_format": {"type": "json_schema",
                                "json_schema": {"name": "clubn_hri", "schema": schema, "strict": True}},
        }
        if seed is not None:
            payload["seed"] = seed
        conn = self._conn(self.timeout)
        t0 = time.perf_counter()
        conn.request("POST", self.prefix + "/v1/chat/completions", json.dumps(payload),
                     {"Content-Type": "application/json", **self.headers})
        resp = conn.getresponse()
        if resp.status != 200:
            body = resp.read()[:300]
            conn.close()
            raise RuntimeError(f"HTTP {resp.status}: {body!r}")
        buf = ""
        ts: dict[str, Optional[float]] = {"t_first_token": None, "t_type_known": None,
                                          "t_first_sentence": None, "t_speech_done": None}
        usage: dict = {}
        while True:
            line = resp.readline()
            if not line:
                break
            line = line.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            d = line[5:].strip()
            if d == "[DONE]":
                break
            j = json.loads(d)
            now = time.perf_counter() - t0
            if j.get("usage"):
                usage = j["usage"]
            for ch in j.get("choices") or []:
                delta = (ch.get("delta") or {}).get("content") or ""
                if not delta:
                    continue
                buf += delta
                if ts["t_first_token"] is None:
                    ts["t_first_token"] = now
                if ts["t_type_known"] is None and TYPE_RE.search(buf):
                    ts["t_type_known"] = now
                if on_speech_partial is not None and ts["t_speech_done"] is None:
                    _emit_speech(buf, on_speech_partial, partial=True)
                if ts["t_first_sentence"] is None or ts["t_speech_done"] is None:
                    fs, done = scan_speech(buf)
                    if fs and ts["t_first_sentence"] is None:
                        ts["t_first_sentence"] = now
                    if done and ts["t_speech_done"] is None:
                        ts["t_speech_done"] = now
                        if ts["t_first_sentence"] is None:
                            ts["t_first_sentence"] = now
                        if on_speech is not None:
                            _emit_speech(buf, on_speech)
        ts["t_total"] = time.perf_counter() - t0
        conn.close()
        pd = usage.get("prompt_tokens_details") or {}
        return {"raw": buf, "metrics": {**ts, "prompt_tokens": usage.get("prompt_tokens"),
                                        "completion_tokens": usage.get("completion_tokens"),
                                        "cached_tokens": pd.get("cached_tokens")}}


def _emit_speech(buf: str, cb: Callable[[str], None], partial: bool = False) -> None:
    """Extrae el valor de speech del JSON parcial y lo pasa al callback. ``partial``: también
    mientras speech sigue abierto (lo escrito hasta ahora), para pre-sintetizar la 1ª cláusula."""
    m = SPEECH_RE.search(buf)
    if not m:
        return
    i, n = m.end(), len(buf)
    while i < n:
        if buf[i] == "\\":
            i += 2
            continue
        if buf[i] == '"':
            break
        i += 1
    if i >= n and not partial:
        return
    raw = buf[m.end():min(i, n)]
    if partial and raw.endswith("\\") and not raw.endswith("\\\\"):
        raw = raw[:-1]                     # escape a medias al final del trozo
    try:
        cb(json.loads('"' + raw + '"'))
    except Exception:          # el prefetch nunca debe romper el turno
        pass


RETRY_TEMPERATURE = 0.7


class HriLLM:
    """Fachada de alto nivel usada por la FSM: respond(state, ...) → obj validado."""

    def __init__(self, client: Optional[LlamaStreamClient] = None, kb: Optional[dict] = None,
                 on_attempt: Optional[Callable[[dict], None]] = None):
        self.client = client or LlamaStreamClient()
        self.kb = kb or contract.load_kb()
        self.on_attempt = on_attempt
        self.on_speech: Optional[Callable[[str], None]] = None   # prefetch TTS (opt-in)
        self.on_speech_partial: Optional[Callable[[str], None]] = None   # 1ª cláusula en curso

    def respond(self, state: str, rank: str, person: Optional[dict], *, utterance: Optional[str] = None,
                alternatives: Optional[list[str]] = None, pending_area: Optional[str] = None,
                direction: Optional[str] = None, retry: bool = True, extra_context: Optional[str] = None) -> dict[str, Any]:
        """Una intervención del contrato B: {obj, errors, raw, metrics, attempts, retry_used}."""
        msgs = contract.build_messages(state, rank, person, self.kb, utterance=utterance,
                                       alternatives=alternatives, pending_area=pending_area,
                                       extra_context=extra_context)
        schema = contract.json_schema(state, rank, direction)

        def attempt(messages: list[dict], temperature: float) -> dict[str, Any]:
            kw = {"on_speech": self.on_speech} if self.on_speech is not None else {}
            if self.on_speech_partial is not None:
                kw["on_speech_partial"] = self.on_speech_partial
            res = self.client.stream_chat(messages, schema, temperature=temperature, **kw)
            obj, errors = None, []
            try:
                obj = json.loads(res["raw"])
                key_order = list(obj) if isinstance(obj, dict) else None
                errors = contract.validate(obj, state, rank, direction=direction)
            except json.JSONDecodeError as exc:
                errors = [f"json_error: {exc}"]
                key_order = None
            out = {**res, "obj": obj, "errors": errors, "key_order": key_order, "temperature": temperature}
            if self.on_attempt:
                self.on_attempt(out)
            return out

        first = attempt(msgs, 0.2)
        attempts = [first]
        if first["errors"] and isinstance(first["obj"], dict):
            fixed = contract.repair_trigger(first["obj"])
            if not contract.validate(fixed, state, rank, direction=direction):
                first = {**first, "obj": fixed, "errors": [], "repaired": list(first["errors"])}
                attempts = [first]
        if first["errors"] and retry and first["raw"]:
            tail = "Corrígela y devuelve solo el objeto del contrato completo, con los campos en orden: " + \
                ", ".join(contract.CONVERSATION_FIELDS if state == "CONVERSATION" else contract.BASE_FIELDS) + "."
            m2 = msgs + [{"role": "assistant", "content": first["raw"]},
                         {"role": "user", "content": "Tu respuesta anterior fue rechazada por el validador: "
                          + "; ".join(first["errors"]) + ". " + tail}]
            attempts.append(attempt(m2, RETRY_TEMPERATURE))
        final = dict(attempts[-1])
        final["attempts"] = attempts
        final["retry_used"] = len(attempts) > 1
        final["first_attempt_ok"] = not first["errors"]
        final["repaired"] = first.get("repaired", [])
        return final


def _smoke() -> None:
    llm = HriLLM()
    print("url:", llm_url(), "| health:", llm.client.health())
    person = {"name": "Laura", "rank": "VIP"}
    for state, kw in [("GREETING", {}),
                      ("CONVERSATION", {"utterance": "Buenas tardes, ¿a qué hora abre la biblioteca del club?"})]:
        t0 = time.perf_counter()
        r = llm.respond(state, person["rank"], person, **kw)
        m = r["metrics"]
        print(f"[{state}] wall={time.perf_counter()-t0:.2f}s type@{m['t_type_known']} total={m['t_total']:.2f}s "
              f"errors={r['errors']} retry={r['retry_used']}")
        print("  obj:", json.dumps(r["obj"], ensure_ascii=False))


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--smoke", action="store_true", help="smoke en vivo contra x2-llm")
    ap.add_argument("--note", action="store_true", help="imprimir la nota del runner nsenter")
    a = ap.parse_args()
    if a.note:
        print(runner_note())
    elif a.smoke:
        _smoke()
    else:
        ap.print_help()
