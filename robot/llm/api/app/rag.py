"""RAG ligero: chunking por párrafos, embeddings ONNX (fastembed) y búsqueda
por similitud coseno con numpy (fuerza bruta sobre SQLite).

Sin servicios externos ni torch: con unos pocos miles de chunks la búsqueda
tarda <5 ms y todo cabe en el mismo contenedor y en la misma BD que la memoria.

Embeddings: minishlab/potion-multilingual-128M por defecto (256 dims,
multilingüe, truncado a 512 tokens, sin prefijos). Si se configura un modelo
de la familia e5 (p. ej. intfloat/multilingual-e5-large) se añaden
automáticamente sus prefijos obligatorios "query:"/"passage:". El modelo ONNX
se descarga la primera vez hacia FASTEMBED_CACHE_PATH (/data/fastembed,
volumen persistente). Si el embedder no está disponible (sin red, ONNX no
instalable...), el RAG se desactiva solo y el chat sigue funcionando sin
contexto.

Regla de latencia: el contexto recuperado se inyecta SIEMPRE en el último
mensaje de usuario (ver prompts.build_user_message), nunca tras el system
prompt — así el prefijo system+resumen+historial queda byte-idéntico entre
turnos y llama-server reutiliza su KV cache (TTFT mínimo con contexto amplio).
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import threading

import numpy as np

log = logging.getLogger("llm.rag")

MAX_FILE_BYTES = 2_000_000
SKIP_DIRS = {".git", "__pycache__", "media", "node_modules", ".venv"}


class Embedder:
    """Envoltorio perezoso de fastembed con normalización.

    Los prefijos "query:"/"passage:" solo se aplican a modelos de la familia
    e5 (que los requieren); el resto (potion, MiniLM, jina...) van tal cual.
    """

    def __init__(self, model_name: str, cache_dir: str) -> None:
        self.model_name = model_name
        self.cache_dir = cache_dir
        self._use_prefix = "e5" in model_name.lower()
        self._m = None
        self.error: str | None = None
        self._initializing = False
        self._lock = threading.Lock()

    def _ensure(self) -> bool:
        """Inicialización BLOQUEANTE (puede descargar ~500 MB de ONNX).

        Solo debe llamarse desde el hilo de arranque o endpoints explícitos
        de ingesta — NUNCA desde /health o /chat: si la descarga se estanca,
        retendría el lock y bloquearía toda la API (usa ``ensure()``/``state()``).
        """
        with self._lock:
            if self._m is None and self.error is None:
                self._initializing = True
                try:
                    os.makedirs(self.cache_dir, exist_ok=True)
                    from fastembed import TextEmbedding

                    self._m = TextEmbedding(self.model_name, cache_dir=self.cache_dir)
                    log.info("embedder listo: %s (cache: %s)", self.model_name, self.cache_dir)
                except Exception as exc:  # noqa: BLE001
                    self.error = f"{type(exc).__name__}: {exc}"
                    log.warning("embedder no disponible (%s); RAG desactivado", self.error)
                finally:
                    self._initializing = False
        return self._m is not None

    def ensure(self) -> bool:
        """Alias público de la inicialización bloqueante (hilo de arranque)."""
        return self._ensure()

    @property
    def available(self) -> bool:
        """NO bloqueante: True solo si el modelo YA está cargado.

        Mientras se descarga/carga, el RAG se degrada a sin-contexto en vez de
        bloquear el turno de chat.
        """
        return self._m is not None

    def state(self) -> str:
        """Estado legible y NO bloqueante para /health y /rag/stats."""
        if self._m is not None:
            return "ok"
        if self.error:
            return f"embedder no disponible ({self.error})"
        if self._initializing:
            return "inicializando (descargando/cargando modelo ONNX)"
        return "pendiente de inicialización"

    def _embed(self, texts: list[str]) -> np.ndarray:
        if not self._ensure() or self._m is None:
            raise RuntimeError(self.error or "embedder no disponible")
        with self._lock:
            vecs = list(self._m.embed(texts))
        mat = np.asarray(vecs, dtype=np.float32)
        return self._norm(mat)

    def passages(self, texts: list[str]) -> np.ndarray:
        if self._use_prefix:
            texts = ["passage: " + t for t in texts]
        return self._embed(texts)

    def query(self, text: str) -> np.ndarray:
        if self._use_prefix:
            text = "query: " + text
        return self._embed([text])[0]

    @staticmethod
    def _norm(m: np.ndarray) -> np.ndarray:
        n = np.linalg.norm(m, axis=-1, keepdims=True)
        n[n == 0] = 1.0
        return m / n


def chunk_text(text: str, size: int = 600, overlap: int = 120) -> list[str]:
    """Trocea texto respetando párrafos; los párrafos gigantes se cortan por
    frases con ``overlap`` caracteres de solape entre chunks."""
    text = re.sub(r"[ \t]+\n", "\n", text.strip())
    text = re.sub(r"\n{3,}", "\n\n", text)
    out: list[str] = []
    cur = ""

    def flush() -> None:
        nonlocal cur
        if cur.strip():
            out.append(cur.strip())
        cur = ""

    def hard_split(para: str) -> None:
        start, n = 0, len(para)
        while start < n:
            end = min(start + size, n)
            if end < n:
                cut = max(para.rfind(". ", start, end), para.rfind("\n", start, end),
                          para.rfind("? ", start, end), para.rfind("! ", start, end))
                if cut > start + size // 2:
                    end = cut + 1
            piece = para[start:end].strip()
            if piece:
                out.append(piece)
            if end >= n:
                break
            start = max(end - overlap, start + 1)

    for para in text.split("\n\n"):
        para = para.strip()
        if not para:
            continue
        if len(para) > size:
            flush()
            hard_split(para)
            cur = para[-overlap:] if overlap else ""
            continue
        if cur and len(cur) + 2 + len(para) > size:
            tail = cur[-overlap:] if overlap else ""
            flush()
            cur = (tail + "\n\n" + para) if tail else para
        else:
            cur = (cur + "\n\n" + para) if cur else para
    flush()
    # sin vacíos ni duplicados consecutivos (texto repetitivo + solape)
    return [c for i, c in enumerate(out) if c and (i == 0 or c != out[i - 1])]


class RagIndex:
    def __init__(self, store, embedder, chunk_chars: int = 600, chunk_overlap: int = 120,
                 top_k: int = 3, min_score: float = 0.40, enabled: bool = True) -> None:
        self.store = store
        self.embedder = embedder
        self.chunk_chars = chunk_chars
        self.chunk_overlap = chunk_overlap
        self.top_k = top_k
        self.min_score = min_score
        self.enabled = enabled
        self._lock = threading.Lock()
        self._chunks_cache: dict | None = None
        self._chunks_version = -1
        self._facts_cache: dict | None = None
        self._facts_version = -1

    @property
    def available(self) -> bool:
        return self.enabled and self.embedder.available

    # --- ingesta -----------------------------------------------------------------
    def ingest_dir(self, root: str) -> dict:
        """Indexa *.md y *.txt de ``root`` (recursivo). Idempotente por hash."""
        files: list[str] = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
            for f in sorted(filenames):
                p = os.path.join(dirpath, f)
                if (f.lower().endswith((".md", ".txt")) and os.path.isfile(p)
                        and os.path.getsize(p) <= MAX_FILE_BYTES):
                    files.append(p)
        new = 0
        for p in files:
            try:
                with open(p, encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
            except OSError as exc:
                log.warning("no pude leer %s: %s", p, exc)
                continue
            new += self.ingest_text(os.path.relpath(p, root), text)
        self.reembed_missing()
        return {"files": len(files), "chunks_new": new, "chunks_total": self.store.chunk_count()}

    def ingest_text(self, source: str, text: str) -> int:
        """Indexa un texto; devuelve cuántos chunks nuevos añadió."""
        pending: list[tuple[int, str]] = []
        for i, chunk in enumerate(chunk_text(text, self.chunk_chars, self.chunk_overlap)):
            h = hashlib.sha256(f"{source}\x00{i}\x00{chunk}".encode("utf-8")).hexdigest()
            cid = self.store.insert_chunk(source, i, chunk, h)
            if cid is not None:
                pending.append((cid, chunk))
        if pending and self.available:
            self._embed_pending(pending)
        return len(pending)

    def reembed_missing(self) -> int:
        """Embeddea chunks que quedaron sin vector (ingesta sin embedder).

        Solo se llama desde el hilo de arranque; la inicialización del
        embedder (posible descarga) ocurre ahí, nunca en un endpoint.
        """
        missing = self.store.chunks_missing_embedding()
        if not missing:
            return 0
        if not self.embedder.ensure():
            return 0
        self._embed_pending([(m["id"], m["text"]) for m in missing])
        return len(missing)

    def _embed_pending(self, pending: list[tuple[int, str]], batch: int = 32) -> None:
        for i in range(0, len(pending), batch):
            part = pending[i:i + batch]
            try:
                mat = self.embedder.passages([t for _, t in part])
            except Exception as exc:  # noqa: BLE001
                log.warning("embeddings fallaron (%s); se reintentarán después", exc)
                return
            for (cid, _), vec in zip(part, mat):
                self.store.set_chunk_embedding(cid, vec.astype(np.float32).tobytes())

    # --- búsqueda ------------------------------------------------------------------
    def _chunks_matrix(self) -> dict | None:
        with self._lock:
            v = self.store.data_version
            if self._chunks_cache is None or self._chunks_version != v:
                rows = [r for r in self.store.all_chunks() if r["embedding"]]
                if rows:
                    mat = np.stack([np.frombuffer(r["embedding"], dtype=np.float32) for r in rows])
                else:
                    mat = np.zeros((0, 0), dtype=np.float32)
                self._chunks_cache = {
                    "sources": [r["source"] for r in rows],
                    "texts": [r["text"] for r in rows],
                    "mat": mat,
                }
                self._chunks_version = v
            return self._chunks_cache

    def _facts_matrix(self) -> dict | None:
        with self._lock:
            v = self.store.data_version
            if self._facts_cache is None or self._facts_version != v:
                rows = [r for r in self.store.all_facts() if r["embedding"]]
                if rows:
                    mat = np.stack([np.frombuffer(r["embedding"], dtype=np.float32) for r in rows])
                else:
                    mat = np.zeros((0, 0), dtype=np.float32)
                self._facts_cache = {"texts": [r["content"] for r in rows], "mat": mat}
                self._facts_version = v
            return self._facts_cache

    def search(self, query: str, k: int | None = None, min_score: float | None = None) -> list[dict]:
        if not self.available:
            return []
        data = self._chunks_matrix()
        if data is None or data["mat"].size == 0:
            return []
        k = k if k is not None else self.top_k
        ms = self.min_score if min_score is None else min_score
        q = self.embedder.query(query)
        sims = data["mat"] @ q
        order = np.argsort(-sims)[:k]
        return [
            {"source": data["sources"][i], "text": data["texts"][i], "score": round(float(sims[i]), 4)}
            for i in order
            if float(sims[i]) >= ms
        ]

    def search_facts(self, query: str, k: int | None = None, min_score: float | None = None) -> list[dict]:
        if not self.available:
            return []
        data = self._facts_matrix()
        if data is None or data["mat"].size == 0:
            return []
        k = k if k is not None else 2
        ms = self.min_score if min_score is None else min_score
        q = self.embedder.query(query)
        sims = data["mat"] @ q
        order = np.argsort(-sims)[:k]
        return [
            {"content": data["texts"][i], "score": round(float(sims[i]), 4)}
            for i in order
            if float(sims[i]) >= ms
        ]

    # --- gestión -------------------------------------------------------------------
    def stats(self) -> dict:
        return {
            "enabled": self.enabled,
            "embedder": self.embedder.state() if self.enabled else "deshabilitado",
            "embed_model": self.embedder.model_name,
            "chunks": self.store.chunk_count(),
            "chunks_embedded": self.store.chunk_count() - len(self.store.chunks_missing_embedding()),
            "facts": len(self.store.all_facts()),
            "sources": self.store.chunk_sources(),
            "top_k": self.top_k,
            "min_score": self.min_score,
        }

    def clear(self) -> int:
        with self._lock:
            self._chunks_cache = None
        return self.store.delete_chunks()
