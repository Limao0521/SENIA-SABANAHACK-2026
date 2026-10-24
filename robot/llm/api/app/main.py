"""API de inferencia local con memoria + RAG sobre llama-server (mismo contenedor).

Endpoints principales:
  POST /chat                 conversación con memoria de sesión + RAG (SSE)
  POST /v1/chat/completions  proxy crudo compatible OpenAI (sin memoria/RAG)
  GET  /health · /ready      estado (200 siempre que la API vive; /ready 503
                             hasta que el modelo está cargado)
  GET  /metrics              TTFT/tok-s de los últimos turnos
  GET/DELETE /sessions…      gestión de memoria
  POST /rag/ingest · GET /rag/search · GET /rag/stats · DELETE /rag
  GET/POST/DELETE /facts     memoria de largo plazo

Arranque: uvicorn --factory app.main:create_app  (lo hace server/run.sh).
"""
from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
from collections import deque
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel

from . import prompts
from .config import Settings
from .llm_client import LocalLLM
from .memory import Store
from .rag import Embedder, RagIndex

log = logging.getLogger("llm.api")


# --------------------------------------------------------------------- estado
class AppState:
    """Dependencias compartidas + hilos de arranque y tareas de fondo."""

    def __init__(self, settings: Settings, store: Store, rag: RagIndex,
                 llm: LocalLLM, persona: str) -> None:
        self.settings = settings
        self.store = store
        self.rag = rag
        self.llm = llm
        self.persona = persona
        self.llm_ready = threading.Event()
        self.llm_error: str | None = None
        self.metrics: deque = deque(maxlen=settings.metrics_history)
        self.tasks: queue.Queue = queue.Queue(maxsize=64)
        self._threads: list[threading.Thread] = []

    # --- hilos ---
    def start_threads(self) -> None:
        t1 = threading.Thread(target=self._startup, name="llm-startup", daemon=True)
        t2 = threading.Thread(target=self._work, name="llm-worker", daemon=True)
        self._threads = [t1, t2]
        t1.start()
        t2.start()

    def _startup(self) -> None:
        try:
            self.llm.wait_ready(self.settings.llm_wait_timeout)
            self.llm.warmup(prompts.system_message(self.persona))
            self.llm_ready.set()
            log.info("LLM listo y caliente (%s)", self.settings.llm_base_url)
        except Exception as exc:  # noqa: BLE001
            self.llm_error = f"{type(exc).__name__}: {exc}"
            log.error("LLM no disponible: %s", self.llm_error)
        # Auto-ingesta del corpus montado (independiente del LLM; el embedder
        # puede tardar la primera vez porque descarga el modelo ONNX)
        s = self.settings
        try:
            if s.rag_enabled and s.rag_autoingest and self.store.chunk_count() == 0 \
                    and os.path.isdir(s.rag_corpus_dir):
                res = self.rag.ingest_dir(s.rag_corpus_dir)
                log.info("auto-ingesta de %s: %s", s.rag_corpus_dir, res)
            if s.rag_enabled:
                # Carga el embedder en este hilo (la 1ª vez descarga el ONNX;
                # después usa la caché de /data). available es NO bloqueante,
                # así que si no se inicializara aquí, el RAG no arrancaría nunca.
                self.rag.embedder.ensure()
                # Completa vectores de chunks que quedaron sin embedder en un
                # arranque anterior (no-op si no falta ninguno)
                n = self.rag.reembed_missing()
                if n:
                    log.info("embeddings pendientes completados: %d chunks", n)
        except Exception:  # noqa: BLE001
            log.exception("auto-ingesta/re-embedding falló (no fatal)")

    def _work(self) -> None:
        while True:
            fn = self.tasks.get()
            if fn is None:
                break
            try:
                fn()
            except Exception:  # noqa: BLE001
                log.exception("tarea de fondo falló")

    def submit(self, fn) -> None:
        try:
            self.tasks.put_nowait(fn)
        except queue.Full:
            log.warning("cola de tareas llena; descarto una tarea de fondo")

    def shutdown(self) -> None:
        self.submit(None)
        try:
            self.llm.close()
        except Exception:  # noqa: BLE001
            pass


# ------------------------------------------------------- tareas de segundo plano
def rollup(st: AppState, sid: str) -> None:
    """Resumen rodante: comprime los mensajes más antiguos de la sesión en el
    resumen anclado tras el system prompt (ver app/memory.py)."""
    s = st.settings
    msgs = st.store.history(sid)
    if len(msgs) <= s.history_max_messages:
        return
    compress = msgs[:len(msgs) - s.history_keep_messages]
    if not compress:
        return
    upto = compress[-1]["id"]
    transcript = "\n".join(f"{m['role']}: {m['content']}" for m in compress)
    old = st.store.get_summary(sid)
    old_block = f"\n\nFusiónalo con el resumen anterior:\n{old}" if old else ""
    instruction = prompts.SUMMARY_INSTRUCTION.format(max_words=s.summary_max_words, old=old_block)
    text = st.llm.chat(
        [{"role": "system", "content": instruction}, {"role": "user", "content": transcript}],
        max_tokens=int(s.summary_max_words * 2.5), temperature=0.2,
        enable_thinking=s.enable_thinking,
    ).strip()
    if text:
        st.store.set_summary(sid, text, upto)
        # El system prompt cambió: re-calienta la KV cache con el nuevo prefijo
        st.llm.warmup(prompts.system_message(st.persona, text))
        log.info("rollup sesión=%s: %d mensajes comprimidos", sid, len(compress))


def extract_facts(st: AppState, sid: str, user_text: str, reply: str) -> None:
    """Memoria de largo plazo: hechos duraderos del intercambio, con embedding."""
    if not st.settings.fact_extract:
        return
    exchange = f"usuario: {user_text}\nasistente: {reply}"
    text = st.llm.chat(
        [{"role": "system", "content": prompts.FACT_INSTRUCTION},
         {"role": "user", "content": exchange}],
        max_tokens=200, temperature=0.0, enable_thinking=st.settings.enable_thinking,
    )
    added = 0
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("-"):
            continue
        fact = line.lstrip("- ").strip()
        if not fact or fact.upper() == "NINGUNO":
            continue
        emb = None
        if st.rag.available:
            try:
                emb = st.rag.embedder.passages([fact])[0].tobytes()
            except Exception:  # noqa: BLE001
                log.exception("embedding de hecho falló")
        if st.store.add_fact(fact, emb):
            added += 1
    if added:
        log.info("sesión=%s: %d hechos nuevos en memoria de largo plazo", sid, added)


# ------------------------------------------------------------------- modelos
class ChatRequest(BaseModel):
    session_id: str = "default"
    message: str
    stream: bool = True
    max_tokens: Optional[int] = None
    temperature: Optional[float] = None
    use_rag: bool = True


class IngestRequest(BaseModel):
    path: str


class IngestTextRequest(BaseModel):
    source: str
    text: str


class FactRequest(BaseModel):
    content: str


# ----------------------------------------------------------------------- app
def create_app(settings: Optional[Settings] = None, store: Optional[Store] = None,
               llm: Optional[LocalLLM] = None, persona: Optional[str] = None,
               rag: Optional[RagIndex] = None, start_background: bool = True) -> FastAPI:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = settings or Settings()
    store = store or Store(settings.db_path)
    llm = llm or LocalLLM(settings.llm_base_url, settings.llm_api_key, settings.llm_model)
    persona = persona if persona is not None else prompts.load_persona(settings.persona_file)
    if rag is None:
        embedder = Embedder(settings.embed_model, settings.embed_cache_dir)
        rag = RagIndex(store, embedder, chunk_chars=settings.rag_chunk_chars,
                       chunk_overlap=settings.rag_chunk_overlap, top_k=settings.rag_top_k,
                       min_score=settings.rag_min_score, enabled=settings.rag_enabled)
    st = AppState(settings, store, rag, llm, persona)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if start_background:
            st.start_threads()
        yield
        st.shutdown()

    app = FastAPI(title="x2-ultra-llm", version="1.0.0", lifespan=lifespan)
    app.state.st = st

    # --- preparación del turno (común a stream y no-stream) ------------------
    def prepare(sid: str, message: str, use_rag: bool):
        contexts = facts = []
        if use_rag and st.rag.available:
            try:
                contexts = st.rag.search(message)
                facts = st.rag.search_facts(message, k=settings.fact_top_k)
            except Exception:  # noqa: BLE001
                log.exception("retrieval falló; sigo sin contexto")
        system = prompts.system_message(st.persona, st.store.get_summary(sid))
        messages = [{"role": "system", "content": system}]
        messages += [{"role": m["role"], "content": m["content"]} for m in st.store.history(sid)]
        messages.append({"role": "user", "content": prompts.build_user_message(message, contexts, facts)})
        return messages, contexts, facts

    def persist_and_schedule(sid: str, user_text: str, reply: str) -> None:
        st.store.add_message(sid, "user", user_text)
        if reply:
            st.store.add_message(sid, "assistant", reply)
        st.submit(lambda: rollup(st, sid))
        if reply and settings.fact_extract:
            st.submit(lambda: extract_facts(st, sid, user_text, reply))

    # --- chat ------------------------------------------------------------------
    @app.post("/chat")
    def chat(req: ChatRequest):
        message = req.message.strip()
        if not message:
            return JSONResponse({"error": "mensaje vacío"}, status_code=400)
        if not st.llm_ready.is_set():
            return JSONResponse(
                {"error": "llm-no-listo", "detail": st.llm_error or "cargando modelo"},
                status_code=503)
        sid = req.session_id
        messages, contexts, facts = prepare(sid, message, req.use_rag)
        max_tokens = req.max_tokens or settings.max_tokens
        temperature = req.temperature if req.temperature is not None else settings.temperature

        if not req.stream:
            t0 = time.monotonic()
            ttft = None
            parts: list[str] = []
            usage: dict = {}
            try:
                for ev in llm.chat_stream(messages, max_tokens, temperature, settings.enable_thinking):
                    if "delta" in ev:
                        if ttft is None:
                            ttft = time.monotonic() - t0
                        parts.append(ev["delta"])
                    elif "usage" in ev:
                        usage = ev["usage"]
            except Exception as exc:  # noqa: BLE001
                log.exception("error generando respuesta")
                return JSONResponse({"error": str(exc)[:300]}, status_code=502)
            total = time.monotonic() - t0
            reply = "".join(parts).strip()
            persist_and_schedule(sid, message, reply)
            done = _turn_metrics(st, sid, ttft or total, total, usage, contexts, facts)
            return JSONResponse({"reply": reply, **done})

        def gen():
            t0 = time.monotonic()
            ttft = None
            parts: list[str] = []
            usage: dict = {}
            try:
                for ev in llm.chat_stream(messages, max_tokens, temperature, settings.enable_thinking):
                    if "delta" in ev:
                        if ttft is None:
                            ttft = time.monotonic() - t0
                        parts.append(ev["delta"])
                        yield _sse({"type": "delta", "text": ev["delta"]})
                    elif "usage" in ev:
                        usage = ev["usage"]
            except Exception as exc:  # noqa: BLE001
                log.exception("error generando respuesta")
                yield _sse({"type": "error", "message": str(exc)[:300]})
            total = time.monotonic() - t0
            reply = "".join(parts).strip()
            persist_and_schedule(sid, message, reply)
            yield _sse({"type": "done", **_turn_metrics(st, sid, ttft or total, total,
                                                        usage, contexts, facts)})

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    # --- proxy OpenAI (crudo, sin memoria ni RAG) ---------------------------------
    @app.post("/v1/chat/completions")
    async def proxy_chat(request: Request):
        body = await request.body()
        try:
            stream = bool(json.loads(body or b"{}").get("stream"))
        except Exception:  # noqa: BLE001
            stream = False
        if stream:
            def gen():
                try:
                    with llm.stream_raw("/chat/completions", body) as resp:
                        for chunk in resp.iter_raw():
                            yield chunk
                except Exception as exc:  # noqa: BLE001
                    yield _sse({"error": str(exc)[:300]}).encode()
            return StreamingResponse(gen(), media_type="text/event-stream")
        try:
            resp = llm.post_raw("/chat/completions", body)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": str(exc)[:300]}, status_code=502)
        return Response(content=resp.content, status_code=resp.status_code,
                        media_type=resp.headers.get("content-type", "application/json"))

    @app.get("/v1/models")
    def proxy_models():
        try:
            resp = llm.get_raw("/models")
            return Response(content=resp.content, status_code=resp.status_code,
                            media_type="application/json")
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": str(exc)[:200]}, status_code=502)

    # --- estado ------------------------------------------------------------------
    @app.get("/health")
    def health():
        return {
            "status": "ready" if st.llm_ready.is_set() else "starting",
            "llm": "ready" if st.llm_ready.is_set() else (st.llm_error or "cargando"),
            # state() es NO bloqueante: /health jamás espera al embedder
            "rag": "deshabilitado" if not st.rag.enabled else st.rag.embedder.state(),
            "chunks": st.store.chunk_count(),
            "sessions": len(st.store.list_sessions()),
            "model_path": os.getenv("MODEL_PATH"),
            "ctx_size": os.getenv("CTX_SIZE"),
        }

    @app.get("/ready")
    def ready():
        if st.llm_ready.is_set():
            return {"ready": True}
        return JSONResponse({"ready": False, "detail": st.llm_error or "cargando modelo"},
                            status_code=503)

    @app.get("/metrics")
    def metrics():
        turns = list(st.metrics)
        ttfts = [t["ttft_ms"] for t in turns if t.get("ttft_ms") is not None]
        toks = [t["tok_s"] for t in turns if t.get("tok_s") is not None]
        return {
            "turns": turns[-20:],
            "avg_ttft_ms": round(sum(ttfts) / len(ttfts), 1) if ttfts else None,
            "avg_tok_s": round(sum(toks) / len(toks), 1) if toks else None,
        }

    # --- memoria -------------------------------------------------------------------
    @app.get("/sessions")
    def sessions():
        return {"sessions": st.store.list_sessions()}

    @app.get("/sessions/{sid}")
    def session(sid: str):
        s = st.store.get_session(sid)
        if s is None:
            return JSONResponse({"error": "sesión no encontrada"}, status_code=404)
        s["messages"] = st.store.history(sid)
        return s

    @app.delete("/sessions/{sid}")
    def delete_session(sid: str):
        return {"deleted": st.store.delete_session(sid)}

    @app.post("/sessions/{sid}/summarize")
    def summarize(sid: str):
        if st.store.get_session(sid) is None:
            return JSONResponse({"error": "sesión no encontrada"}, status_code=404)
        rollup(st, sid)
        return {"summary": st.store.get_summary(sid)}

    @app.get("/facts")
    def facts():
        return {"facts": [{"id": f["id"], "content": f["content"],
                           "embedded": f["embedding"] is not None} for f in st.store.all_facts()]}

    @app.post("/facts")
    def add_fact(req: FactRequest):
        content = req.content.strip()
        if not content:
            return JSONResponse({"error": "contenido vacío"}, status_code=400)
        emb = None
        if st.rag.available:
            try:
                emb = st.rag.embedder.passages([content])[0].tobytes()
            except Exception:  # noqa: BLE001
                log.exception("embedding de hecho falló")
        return {"added": st.store.add_fact(content, emb)}

    @app.delete("/facts")
    def delete_facts():
        return {"deleted": st.store.delete_facts()}

    # --- RAG -----------------------------------------------------------------------
    @app.post("/rag/ingest")
    def rag_ingest(req: IngestRequest):
        if not os.path.isdir(req.path):
            return JSONResponse({"error": f"directorio no encontrado: {req.path}"}, status_code=404)
        if not st.rag.available:
            return JSONResponse({"error": "embedder no disponible",
                                 "detail": st.rag.embedder.state()}, status_code=503)
        return st.rag.ingest_dir(req.path)

    @app.post("/rag/ingest_text")
    def rag_ingest_text(req: IngestTextRequest):
        if not req.text.strip():
            return JSONResponse({"error": "texto vacío"}, status_code=400)
        if not st.rag.available:
            return JSONResponse({"error": "embedder no disponible",
                                 "detail": st.rag.embedder.state()}, status_code=503)
        return {"chunks_new": st.rag.ingest_text(req.source, req.text),
                "chunks_total": st.store.chunk_count()}

    @app.get("/rag/search")
    def rag_search(q: str, k: int | None = None, min_score: float | None = None):
        if not st.rag.available:
            return JSONResponse({"error": "embedder no disponible",
                                 "detail": st.rag.embedder.state()}, status_code=503)
        return {"results": st.rag.search(q, k=k, min_score=min_score)}

    @app.get("/rag/stats")
    def rag_stats():
        return st.rag.stats()

    @app.delete("/rag")
    def rag_clear():
        return {"deleted_chunks": st.rag.clear()}

    return app


# --------------------------------------------------------------------- helpers
def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


def _turn_metrics(st: AppState, sid: str, ttft: float, total: float, usage: dict,
                  contexts: list, facts: list) -> dict:
    completion = usage.get("completion_tokens")
    cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens")
    m = {
        "session_id": sid,
        "ttft_ms": round(ttft * 1000),
        "total_s": round(total, 3),
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": completion,
        "cached_tokens": cached,
        "rag_contexts": len(contexts),
        "rag_facts": len(facts),
    }
    if completion and total > ttft:
        m["tok_s"] = round((completion - 1) / max(total - ttft, 1e-6), 1)
    st.metrics.append(m)
    return m
