"""Store SQLite: sesiones, historial, corte de resumen, hechos y chunks."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api"))

from app.memory import Store  # noqa: E402


def make_store(tmp_path):
    return Store(str(tmp_path / "test.db"))


def test_sesiones_y_historial(tmp_path):
    s = make_store(tmp_path)
    s.add_message("a", "user", "hola")
    s.add_message("a", "assistant", "qué tal")
    h = s.history("a")
    assert [m["role"] for m in h] == ["user", "assistant"]
    assert h[0]["content"] == "hola"
    assert s.count_active("a") == 2
    assert s.get_session("a") is not None
    assert s.get_session("nope") is None
    assert len(s.list_sessions()) == 1


def test_resumen_acorta_el_historial_activo(tmp_path):
    s = make_store(tmp_path)
    for i in range(6):
        s.add_message("a", "user" if i % 2 == 0 else "assistant", f"m{i}")
    ids = [m["id"] for m in s.history("a")]
    s.set_summary("a", "resumen de m0..m3", ids[3])
    assert s.get_summary("a") == "resumen de m0..m3"
    h = s.history("a")
    assert [m["content"] for m in h] == ["m4", "m5"]
    assert s.count_active("a") == 2
    # el corte nunca retrocede
    s.set_summary("a", "otro", ids[1])
    assert [m["content"] for m in s.history("a")] == ["m4", "m5"]


def test_borrar_sesion(tmp_path):
    s = make_store(tmp_path)
    s.add_message("a", "user", "x")
    s.add_message("b", "user", "y")
    assert s.delete_session("a") is True
    assert s.delete_session("a") is False
    assert s.history("a") == []
    assert len(s.history("b")) == 1


def test_hechos(tmp_path):
    s = make_store(tmp_path)
    assert s.add_fact("El usuario se llama Juan", b"\x01\x02") is True
    assert s.add_fact("El usuario se llama Juan", b"\x01\x02") is False  # único
    facts = s.all_facts()
    assert len(facts) == 1 and facts[0]["embedding"] == b"\x01\x02"
    assert s.delete_facts() == 1
    assert s.all_facts() == []


def test_chunks_idempotentes_y_versionado(tmp_path):
    s = make_store(tmp_path)
    v0 = s.data_version
    cid = s.insert_chunk("doc.md", 0, "texto", "hash1")
    assert cid is not None
    assert s.insert_chunk("doc.md", 0, "texto", "hash1") is None  # hash único
    assert s.chunk_count() == 1
    assert s.data_version > v0
    s.set_chunk_embedding(cid, b"\x00\x01")
    assert s.chunks_missing_embedding() == []
    assert [c["source"] for c in s.chunk_sources()] == ["doc.md"]
    assert s.delete_chunks() == 1
    assert s.chunk_count() == 0
