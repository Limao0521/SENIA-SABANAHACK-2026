#!/usr/bin/env python3
"""Chat interactivo contra la API (memoria + RAG), con métricas por turno.

Solo stdlib:
    python scripts/chat.py --api http://localhost:8000 --session demo

Comandos dentro del chat:
    /reset    borra la sesión (memoria)
    /summary  muestra/fuerza el resumen rodante de la sesión
    /stats    métricas recientes del servidor
    /norag    alterna RAG activado/desactivado
    /exit     salir
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request


def _req(url: str, payload: dict | None = None, method: str | None = None, timeout: float = 900.0):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    r = urllib.request.Request(url, data=data, method=method,
                               headers={"Content-Type": "application/json"} if data else {})
    return urllib.request.urlopen(r, timeout=timeout)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--session", default="chat")
    args = ap.parse_args()
    api = args.api.rstrip("/")
    session = args.session
    use_rag = True

    # esperar a que el modelo esté listo
    print(f"[chat] esperando al LLM en {api} ...")
    while True:
        try:
            _req(f"{api}/ready", timeout=5)
            break
        except (urllib.error.HTTPError, urllib.error.URLError, OSError):
            time.sleep(2)
    print(f"[chat] listo. Sesión '{session}'. /exit para salir.\n")

    while True:
        try:
            msg = input("tú> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not msg:
            continue
        if msg == "/exit":
            return 0
        try:
            if msg == "/reset":
                _req(f"{api}/sessions/{session}", method="DELETE")
                print("[chat] sesión borrada.\n")
                continue
            if msg == "/summary":
                with _req(f"{api}/sessions/{session}/summarize", {}, method="POST") as r:
                    print("[chat] resumen:", json.loads(r.read().decode()).get("summary") or "(vacío)", "\n")
                continue
            if msg == "/stats":
                with _req(f"{api}/metrics") as r:
                    m = json.loads(r.read().decode())
                print(f"[chat] TTFT medio {m.get('avg_ttft_ms')} ms · {m.get('avg_tok_s')} tok/s")
                for t in m.get("turns", [])[-5:]:
                    print("       ", json.dumps(t, ensure_ascii=False))
                print()
                continue
            if msg == "/norag":
                use_rag = not use_rag
                print(f"[chat] RAG {'activado' if use_rag else 'desactivado'}\n")
                continue

            payload = {"session_id": session, "message": msg, "stream": True, "use_rag": use_rag}
            print("nexo> ", end="", flush=True)
            t0 = time.monotonic()
            ttft = None
            with _req(f"{api}/chat", payload) as r:
                for raw in r:
                    line = raw.decode("utf-8", "replace").strip()
                    if not line.startswith("data:"):
                        continue
                    ev = json.loads(line[5:].strip())
                    if ev.get("type") == "delta":
                        if ttft is None:
                            ttft = time.monotonic() - t0
                        print(ev["text"], end="", flush=True)
                    elif ev.get("type") == "done":
                        print(f"\n  [{ev.get('ttft_ms')} ms TTFT"
                              + (f" · {ev['tok_s']} tok/s" if ev.get("tok_s") else "")
                              + (f" · {ev['cached_tokens']} cached" if ev.get("cached_tokens") else "")
                              + (f" · RAG {ev['rag_contexts']}" if ev.get("rag_contexts") else "")
                              + f" · {ev.get('total_s')} s]\n")
                    elif ev.get("type") == "error":
                        print(f"\n[chat] error: {ev.get('message')}\n")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")[:200]
            print(f"\n[chat] HTTP {exc.code}: {body}\n")
        except (urllib.error.URLError, OSError) as exc:
            print(f"\n[chat] sin conexión con la API: {exc}\n")


if __name__ == "__main__":
    sys.exit(main())
