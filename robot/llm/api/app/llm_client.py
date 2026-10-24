"""Cliente del llama-server local (API compatible OpenAI, streaming SSE).

Adaptado del cliente probado en linksoul-local, optimizado para latencia:
  - Pool de conexiones HTTP keep-alive: cero handshakes TCP por turno.
  - Warmup: precarga el modelo y deja el system prompt en la cache de
    prefijos del servidor => TTFT mínimo en cada turno.
  - thinking desactivable (Qwen3): sin segundos de razonamiento antes del
    primer token.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Iterator

import httpx

log = logging.getLogger("llm.client")


class LocalLLM:
    def __init__(self, base_url: str = "http://127.0.0.1:8080/v1",
                 api_key: str = "not-needed", model: str = "local") -> None:
        self.model = model
        self._client = httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            # read largo: una generación completa puede tardar; connect corto
            # para fallar rápido si el servidor LLM no está levantado
            timeout=httpx.Timeout(connect=5.0, read=600.0, write=30.0, pool=10.0),
            limits=httpx.Limits(max_keepalive_connections=4, keepalive_expiry=600.0),
        )

    # ------------------------------------------------------------------ utils
    def health(self) -> bool:
        try:
            base = str(self._client.base_url).removesuffix("/v1").removesuffix("/")
            return httpx.get(f"{base}/health", timeout=5.0).status_code == 200
        except httpx.HTTPError:
            return False

    def wait_ready(self, timeout_s: float = 900.0, poll_s: float = 2.0) -> None:
        """Espera a que el servidor LLM haya cargado el modelo."""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if self.health():
                return
            time.sleep(poll_s)
        raise TimeoutError(f"Servidor LLM no disponible tras {timeout_s}s")

    def warmup(self, system_prompt: str) -> None:
        """Fuerza la carga del modelo y cachea el prefijo de la personalidad."""
        payload = self._payload(
            [{"role": "system", "content": system_prompt}, {"role": "user", "content": "Hola"}],
            max_tokens=1, temperature=0.0,
        )
        payload["stream"] = False
        try:
            t0 = time.monotonic()
            self._client.post("/chat/completions", json=payload).raise_for_status()
            log.info("warmup OK en %.2fs", time.monotonic() - t0)
        except httpx.HTTPError as exc:
            log.warning("warmup falló (no fatal): %s", exc)

    def _payload(self, messages: list[dict], max_tokens: int, temperature: float,
                 enable_thinking: bool = False) -> dict:
        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if not enable_thinking:
            # Qwen3: saltarse la fase thinking recorta segundos de latencia
            payload["chat_template_kwargs"] = {"enable_thinking": False}
        return payload

    # --------------------------------------------------------------- generar
    def chat_stream(self, messages: list[dict], max_tokens: int = 512,
                    temperature: float = 0.7,
                    enable_thinking: bool = False) -> Iterator[dict]:
        """Genera en streaming. Rinde {"delta": str} por token y, si el
        servidor lo reporta, un {"usage": dict} final con métricas."""
        payload = self._payload(messages, max_tokens, temperature, enable_thinking)
        payload["stream"] = True
        # llama-server solo reporta usage en el chunk final si se pide explícitamente
        payload["stream_options"] = {"include_usage": True}
        with self._client.stream("POST", "/chat/completions", json=payload) as resp:
            if resp.status_code != 200:
                body = resp.read().decode(errors="replace")[:500]
                raise RuntimeError(f"LLM HTTP {resp.status_code}: {body}")
            for line in resp.iter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if chunk.get("usage"):
                    yield {"usage": chunk["usage"]}
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                delta = (choices[0].get("delta") or {}).get("content")
                if delta:
                    yield {"delta": delta}

    def chat(self, messages: list[dict], max_tokens: int = 512,
             temperature: float = 0.2, enable_thinking: bool = False) -> str:
        """Variante sin streaming (resumen rodante, extracción de hechos)."""
        payload = self._payload(messages, max_tokens, temperature, enable_thinking)
        payload["stream"] = False
        resp = self._client.post("/chat/completions", json=payload)
        resp.raise_for_status()
        data = resp.json()
        return ((data.get("choices") or [{}])[0].get("message") or {}).get("content", "")

    def post_raw(self, path: str, body: bytes) -> httpx.Response:
        """Petición cruda al servidor (para el proxy OpenAI)."""
        return self._client.post(path, content=body,
                                 headers={"Content-Type": "application/json"})

    def get_raw(self, path: str) -> httpx.Response:
        return self._client.get(path)

    def stream_raw(self, path: str, body: bytes):
        """Context manager de streaming crudo (para el proxy OpenAI)."""
        return self._client.stream("POST", path, content=body,
                                   headers={"Content-Type": "application/json"})

    def close(self) -> None:
        self._client.close()
