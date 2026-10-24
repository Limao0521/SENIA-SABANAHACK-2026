#!/usr/bin/env python3
"""Ingesta de corpus en el RAG de la API. Solo stdlib.

Dos modos:
  1) Ruta DENTRO del contenedor (por defecto; compose ya monta ../docs en
     /corpus/docs):
        python scripts/ingest.py --path /corpus/docs
  2) Carpeta LOCAL (este PC): sube archivo a archivo vía /rag/ingest_text
     (útil cuando la API corre en otra máquina, p. ej. el robot):
        python scripts/ingest.py --local-dir ../docs --api http://10.0.1.41:8000
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request


def _post(url: str, payload: dict) -> dict:
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--path", default=None, help="directorio dentro del contenedor")
    ap.add_argument("--local-dir", default=None, help="directorio en este PC")
    args = ap.parse_args()
    api = args.api.rstrip("/")

    if args.local_dir:
        root = os.path.abspath(args.local_dir)
        if not os.path.isdir(root):
            sys.exit(f"no existe: {root}")
        total_new = files = 0
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in
                           ("media", "__pycache__", "node_modules")]
            for f in sorted(filenames):
                if not f.lower().endswith((".md", ".txt")):
                    continue
                p = os.path.join(dirpath, f)
                if os.path.getsize(p) > 2_000_000:
                    continue
                with open(p, encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
                res = _post(f"{api}/rag/ingest_text",
                            {"source": os.path.relpath(p, root), "text": text})
                total_new += res.get("chunks_new", 0)
                files += 1
                print(f"  {os.path.relpath(p, root)}: +{res.get('chunks_new', 0)} chunks")
        print(f"OK: {files} archivos, {total_new} chunks nuevos")
    else:
        path = args.path or "/corpus/docs"
        res = _post(f"{api}/rag/ingest", {"path": path})
        print(f"OK: {json.dumps(res, ensure_ascii=False)}")

    with urllib.request.urlopen(f"{api}/rag/stats", timeout=30) as r:
        print("stats:", json.dumps(json.loads(r.read().decode()), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except urllib.error.HTTPError as exc:
        sys.exit(f"HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:300]}")
