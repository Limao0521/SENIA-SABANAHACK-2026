#!/usr/bin/env python3
"""Sonda de la API /chat: TTFT, tok/s, reutilización de KV cache y RAG.

Solo stdlib → funciona en cualquier lado (PC Windows, robot, dentro o fuera
del contenedor):
    python scripts/probe.py --api http://localhost:8000
    python scripts/probe.py --rag-pregunta "¿Qué es UPPERBODY_REMOTE_SPLIT?"
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request


def _post_json(url: str, payload: dict, timeout: float = 900.0):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    return urllib.request.urlopen(req, timeout=timeout)


def _get_json(url: str, timeout: float = 10.0):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def wait_ready(api: str, timeout_s: float) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            _get_json(f"{api}/ready", timeout=5)
            print(f"[probe] API lista en {api}")
            return
        except (urllib.error.HTTPError, urllib.error.URLError, OSError):
            time.sleep(2)
    sys.exit(f"[probe] la API no estuvo lista en {timeout_s}s ({api}/ready)")


def chat_turn(api: str, session: str, message: str, label: str) -> dict:
    t0 = time.monotonic()
    ttft = None
    parts: list[str] = []
    done = None
    with _post_json(f"{api}/chat", {"session_id": session, "message": message,
                                    "stream": True}) as r:
        for raw in r:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            ev = json.loads(line[5:].strip())
            if ev.get("type") == "delta":
                if ttft is None:
                    ttft = time.monotonic() - t0
                parts.append(ev["text"])
            elif ev.get("type") == "done":
                done = ev
            elif ev.get("type") == "error":
                sys.exit(f"[probe] error del LLM: {ev.get('message')}")
    wall = time.monotonic() - t0
    reply = "".join(parts).strip()
    done = done or {}
    print(f"\n=== {label} ===")
    print(f"  Pregunta : {message}")
    print(f"  Respuesta: {reply[:200]}{'…' if len(reply) > 200 else ''}")
    print(f"  TTFT     : {done.get('ttft_ms', round(ttft * 1000) if ttft else '?')} ms")
    if done.get("tok_s"):
        print(f"  Velocidad: {done['tok_s']} tok/s ({done.get('completion_tokens')} tokens)")
    if done.get("cached_tokens"):
        print(f"  KV cache : {done['cached_tokens']} tokens de prompt reutilizados")
    print(f"  RAG      : {done.get('rag_contexts', 0)} chunks, {done.get('rag_facts', 0)} hechos")
    print(f"  Total    : {done.get('total_s', round(wall, 3))} s")
    return done


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--session", default="probe")
    ap.add_argument("--rag-pregunta", default=None,
                    help="pregunta sobre el corpus (docs del repo) para probar el RAG")
    ap.add_argument("--timeout", type=float, default=900.0)
    args = ap.parse_args()
    api = args.api.rstrip("/")

    wait_ready(api, args.timeout)
    h = _get_json(f"{api}/health")
    print(f"[probe] health: {json.dumps(h, ensure_ascii=False)}")

    chat_turn(api, args.session, "¡Hola! Preséntate en una frase.", "Turno 1 (tras warmup)")
    chat_turn(api, args.session, "¿Qué te acabo de pedir exactamente?", "Turno 2 (memoria)")
    if args.rag_pregunta:
        chat_turn(api, args.session, args.rag_pregunta, "Turno 3 (RAG)")

    m = _get_json(f"{api}/metrics")
    print(f"\n[probe] medias: TTFT {m.get('avg_ttft_ms')} ms · {m.get('avg_tok_s')} tok/s")
    print("[probe] OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
