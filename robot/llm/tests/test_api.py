"""API completa con LLM y embedder falsos: /chat (SSE), memoria, rollup, RAG.

Sin Docker ni GPU: FakeLLM simula el streaming de llama-server y FakeEmbedder
da vectores deterministas. La lógica probada aquí es la misma que corre en el
contenedor.
"""
import hashlib
import json
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api"))

from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.memory import Store  # noqa: E402
from app.rag import RagIndex  # noqa: E402

PERSONA = "Eres Nexo, asistente del robot X2 Ultra."


class FakeLLM:
    """Streaming falso con métricas, y chat() para tareas de fondo."""

    def __init__(self, deltas=("¡Hola! ", "Soy Nexo.")):
        self.deltas = list(deltas)
        self.calls: list[list[dict]] = []      # mensajes de cada chat_stream
        self.chat_calls: list[list[dict]] = []  # mensajes de cada chat (resúmenes/hechos)
        self.warmups = 0

    def health(self):
        return True

    def wait_ready(self, timeout_s=1, poll_s=0.01):
        return None

    def warmup(self, system_prompt):
        self.warmups += 1

    def chat_stream(self, messages, max_tokens=512, temperature=0.7, enable_thinking=False):
        self.calls.append(messages)
        for d in self.deltas:
            yield {"delta": d}
        yield {"usage": {"prompt_tokens": 42, "completion_tokens": len(self.deltas),
                         "prompt_tokens_details": {"cached_tokens": 40}}}

    def chat(self, messages, max_tokens=512, temperature=0.2, enable_thinking=False):
        self.chat_calls.append(messages)
        return "RESUMEN FALSO de la conversación."

    def close(self):
        pass


class FakeEmbedder:
    model_name = "fake-embed"
    error = None

    @property
    def available(self):
        return True

    def ensure(self):
        return True

    def state(self):
        return "ok"

    def _vec(self, text):
        h = hashlib.md5(text.encode("utf-8")).digest()
        v = np.frombuffer(h[:16], dtype=np.uint8).astype(np.float32)
        return v / np.linalg.norm(v)

    def passages(self, texts):
        return np.stack([self._vec(t) for t in texts])

    def query(self, text):
        return self._vec(text)


def build(tmp_path, start_background=True, **kw):
    defaults = dict(
        db_path=str(tmp_path / "agent.db"),
        persona_file="",                      # → persona por defecto; la pasamos explícita
        rag_autoingest=False,
        rag_min_score=-1.0,                   # el embedder falso no garantiza scores altos
        embed_cache_dir=str(tmp_path / "fastembed"),
        fact_extract=False,
    )
    defaults.update(kw)
    settings = Settings(**defaults)
    store = Store(settings.db_path)
    llm = FakeLLM()
    rag = RagIndex(store, FakeEmbedder(), top_k=settings.rag_top_k,
                   min_score=settings.rag_min_score, enabled=settings.rag_enabled)
    app = create_app(settings=settings, store=store, llm=llm, persona=PERSONA,
                     rag=rag, start_background=start_background)
    return app, store, llm, settings


def parse_sse(text):
    events = []
    for line in text.splitlines():
        if line.startswith("data:"):
            events.append(json.loads(line[5:].strip()))
    return events


@pytest.fixture()
def client(tmp_path):
    app, store, llm, settings = build(tmp_path)
    with TestClient(app) as c:
        # espera a que el hilo de arranque marque el LLM como listo
        for _ in range(100):
            if c.get("/health").json()["status"] == "ready":
                break
            time.sleep(0.05)
        yield c, store, llm, settings


def test_health_ready(client):
    c, *_ = client
    h = c.get("/health").json()
    assert h["status"] == "ready"
    assert h["llm"] == "ready"
    assert h["rag"] == "ok"
    assert c.get("/ready").status_code == 200


def test_chat_no_listo_devuelve_503(tmp_path):
    app, *_ = build(tmp_path, start_background=False)
    with TestClient(app) as c:
        assert c.get("/health").json()["status"] == "starting"
        assert c.get("/ready").status_code == 503
        r = c.post("/chat", json={"message": "hola"})
        assert r.status_code == 503
        assert r.json()["error"] == "llm-no-listo"


def test_chat_stream_memoria_y_metricas(client):
    c, store, llm, _ = client
    r = c.post("/chat", json={"session_id": "s1", "message": "¿Quién eres?"})
    assert r.status_code == 200
    events = parse_sse(r.text)
    deltas = [e["text"] for e in events if e.get("type") == "delta"]
    done = [e for e in events if e.get("type") == "done"]
    assert "".join(deltas) == "¡Hola! Soy Nexo."
    assert done and done[0]["ttft_ms"] >= 0 and done[0]["cached_tokens"] == 40
    assert done[0]["tok_s"] > 0

    # memoria persistida
    h = store.history("s1")
    assert [(m["role"], m["content"]) for m in h] == [
        ("user", "¿Quién eres?"), ("assistant", "¡Hola! Soy Nexo.")]

    # segundo turno: el LLM recibe system + historial + nuevo user
    c.post("/chat", json={"session_id": "s1", "message": "¿Y qué haces?"})
    msgs = llm.calls[-1]
    assert msgs[0]["role"] == "system" and PERSONA in msgs[0]["content"]
    assert [m["role"] for m in msgs[1:]] == ["user", "assistant", "user"]
    assert msgs[-1]["content"] == "¿Y qué haces?"  # sin RAG → mensaje sin envoltorio

    # el prefijo system es byte-idéntico entre turnos (KV cache)
    assert llm.calls[-1][0]["content"] == llm.calls[-2][0]["content"]


def test_mensaje_vacio_400(client):
    c, *_ = client
    assert c.post("/chat", json={"message": "   "}).status_code == 400


def test_rag_inyecta_contexto_en_el_ultimo_user(client):
    c, store, llm, _ = client
    texto = ("# AimDK\n\nUPPERBODY_REMOTE_SPLIT (URS) es el modo en el que el PC "
             "controla los brazos del robot mientras el MC oficial mantiene la "
             "locomoción activa.\n\n# Otra cosa\n\nLa batería mínima es del 30%.")
    r = c.post("/rag/ingest_text", json={"source": "aimdk.md", "text": texto})
    # el texto es corto: cabe en 1 chunk (empaquetado correcto, no un bug)
    assert r.status_code == 200 and r.json()["chunks_new"] >= 1

    r = c.post("/chat", json={"session_id": "s2", "message": "¿Qué es URS?", "use_rag": True})
    done = [e for e in parse_sse(r.text) if e.get("type") == "done"][0]
    assert done["rag_contexts"] >= 1

    last_user = llm.calls[-1][-1]["content"]
    assert "Pregunta del usuario: ¿Qué es URS?" in last_user
    assert "[doc: aimdk.md]" in last_user
    # el contexto NO toca el system prompt (disciplina de KV cache)
    assert "UPPERBODY_REMOTE_SPLIT" not in llm.calls[-1][0]["content"]

    # el historial guarda el mensaje ORIGINAL, no el envoltorio con contexto
    assert store.history("s2")[0]["content"] == "¿Qué es URS?"

    # use_rag=False → sin contexto
    c.post("/chat", json={"session_id": "s3", "message": "¿Qué es URS?", "use_rag": False})
    assert llm.calls[-1][-1]["content"] == "¿Qué es URS?"


def test_rollup_resumen_rodante(tmp_path):
    app, store, llm, settings = build(tmp_path, history_max_messages=4, history_keep_messages=2)
    with TestClient(app) as c:
        for _ in range(100):
            if c.get("/health").json()["status"] == "ready":
                break
            time.sleep(0.05)
        for i in range(3):  # 6 mensajes > history_max(4) tras el 3er turno
            c.post("/chat", json={"session_id": "r1", "message": f"turno {i}"})
        # el worker de fondo comprime los 4 más antiguos y deja 2 activos
        summary = ""
        for _ in range(100):
            summary = store.get_summary("r1")
            if summary:
                break
            time.sleep(0.05)
        assert summary == "RESUMEN FALSO de la conversación."
        assert store.count_active("r1") == 2
        assert llm.chat_calls  # se usó chat() para resumir
        assert llm.warmups >= 2  # re-warmup con el nuevo prefijo
        # el siguiente turno lleva el resumen en el system message
        c.post("/chat", json={"session_id": "r1", "message": "sigue"})
        assert summary in llm.calls[-1][0]["content"]


def test_chat_sin_stream(client):
    c, *_ = client
    r = c.post("/chat", json={"session_id": "ns", "message": "hola", "stream": False})
    assert r.status_code == 200
    body = r.json()
    assert body["reply"] == "¡Hola! Soy Nexo."
    assert body["ttft_ms"] >= 0 and body["session_id"] == "ns"


def test_sesiones_endpoints(client):
    c, *_ = client
    c.post("/chat", json={"session_id": "x1", "message": "hola", "stream": False})
    lst = c.get("/sessions").json()["sessions"]
    assert any(s["id"] == "x1" for s in lst)
    s = c.get("/sessions/x1").json()
    assert s["active_messages"] == 2 and len(s["messages"]) == 2
    assert c.get("/sessions/nope").status_code == 404
    assert c.delete("/sessions/x1").json()["deleted"] is True
    assert c.get("/sessions/x1").status_code == 404


def test_summarize_forzado(client):
    c, *_ = client
    c.post("/chat", json={"session_id": "f1", "message": "hola", "stream": False})
    r = c.post("/sessions/f1/summarize")
    assert r.status_code == 200
    # con solo 2 mensajes no hay nada que comprimir
    assert r.json()["summary"] == ""


def test_facts_endpoints(client):
    c, *_ = client
    assert c.post("/facts", json={"content": "El robot se llama X2 Ultra"}).json()["added"] is True
    facts = c.get("/facts").json()["facts"]
    assert len(facts) == 1 and facts[0]["embedded"] is True
    assert c.delete("/facts").json()["deleted"] == 1


def test_rag_stats_search_clear(client):
    c, *_ = client
    c.post("/rag/ingest_text", json={"source": "d.md", "text": "El MC oficial maneja la locomoción."})
    st = c.get("/rag/stats").json()
    assert st["chunks"] >= 1 and st["embedder"] == "ok"
    res = c.get("/rag/search", params={"q": "locomoción"})
    assert res.status_code == 200 and len(res.json()["results"]) >= 1
    assert c.delete("/rag").json()["deleted_chunks"] >= 1
    assert c.get("/rag/stats").json()["chunks"] == 0


def test_rag_ingest_dir_inexistente(client):
    c, *_ = client
    assert c.post("/rag/ingest", json={"path": "/no/existe"}).status_code == 404


def test_metrics(client):
    c, *_ = client
    c.post("/chat", json={"session_id": "m1", "message": "hola", "stream": False})
    m = c.get("/metrics").json()
    assert m["avg_ttft_ms"] is not None and len(m["turns"]) == 1


class StuckEmbedder(FakeEmbedder):
    """Embedder 'descargando' para siempre: available=False y ensure() bloquea.

    Reproduce el fallo real observado: si la API consultara el embedder de
    forma bloqueante, una descarga estancada colgaría /health y /chat hasta
    agotar el pool de hilos de uvicorn.
    """
    model_name = "stuck-embed"

    def __init__(self):
        self.release = threading.Event()

    @property
    def available(self):
        return False

    def ensure(self):
        self.release.wait(timeout=30)     # simula descarga colgada
        return False

    def state(self):
        return "inicializando (descargando/cargando modelo ONNX)"

    def passages(self, texts):
        raise RuntimeError("embedder no listo")

    def query(self, text):
        raise RuntimeError("embedder no listo")


def test_embedder_inicializando_no_bloquea_api(tmp_path):
    """Con el embedder 'descargando' en el hilo de arranque, /health y /chat
    deben responder igual (el chat degrada a sin-RAG en vez de colgarse)."""
    settings = Settings(db_path=str(tmp_path / "agent.db"), persona_file="",
                        rag_autoingest=False, rag_min_score=-1.0,
                        embed_cache_dir=str(tmp_path / "fastembed"))
    store = Store(settings.db_path)
    # chunk sin embedding → el arranque intenta reembed_missing → ensure() bloquea
    assert store.insert_chunk("doc.md", 0, "texto sin vector", "hash-de-test") is not None
    stuck = StuckEmbedder()
    rag = RagIndex(store, stuck, top_k=settings.rag_top_k,
                   min_score=settings.rag_min_score, enabled=True)
    app = create_app(settings=settings, store=store, llm=FakeLLM(),
                     persona=PERSONA, rag=rag, start_background=True)
    try:
        with TestClient(app) as c:
            # el LLM llega a ready A PESAR de la descarga colgada
            for _ in range(100):
                if c.get("/health").json()["status"] == "ready":
                    break
                time.sleep(0.05)
            h = c.get("/health").json()
            assert h["status"] == "ready" and h["llm"] == "ready"
            assert "inicializando" in h["rag"]          # estado, no bloqueo
            # /chat funciona sin contexto RAG
            r = c.post("/chat", json={"session_id": "s", "message": "hola"})
            assert r.status_code == 200
            done = [e for e in parse_sse(r.text) if e.get("type") == "done"][0]
            assert done["rag_contexts"] == 0
            # los endpoints explícitos de RAG responden 503 rápido (no cuelgan)
            assert c.get("/rag/search", params={"q": "x"}).status_code == 503
            st = c.get("/rag/stats").json()
            assert st["chunks"] == 1 and st["chunks_embedded"] == 0
    finally:
        stuck.release.set()          # libera el hilo de arranque
