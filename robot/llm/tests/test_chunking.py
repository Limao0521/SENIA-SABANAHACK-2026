"""Chunking del RAG: tamaños, solape y cobertura del texto."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api"))

from app.rag import chunk_text  # noqa: E402


def test_parrafos_cortos_se_empaquetan():
    text = "\n\n".join(f"Párrafo número {i} con algo de contenido." for i in range(10))
    chunks = chunk_text(text, size=300, overlap=60)
    assert chunks
    for c in chunks:
        assert len(c) <= 300 + 60 + 2
    # todo párrafo original aparece en algún chunk (cobertura)
    joined = "\n".join(chunks)
    for i in range(10):
        assert f"Párrafo número {i} " in joined or f"Párrafo número {i}" in joined


def test_parrafo_gigante_se_corta_con_solape():
    para = ("Una frase sobre el robot X2 Ultra y su locomoción. " * 60).strip()
    assert len(para) > 2000
    chunks = chunk_text(para, size=600, overlap=120)
    assert len(chunks) >= 3
    for c in chunks:
        assert len(c) <= 600 + 5
    # el principio y el final del párrafo están cubiertos
    assert para[:40] in chunks[0]
    assert para[-40:] in chunks[-1]


def test_texto_vacio_y_ruido():
    assert chunk_text("") == []
    assert chunk_text("   \n\n  \n ") == []


def test_chunks_no_vacios_y_sin_megasolapes():
    text = ("Título\n\n" + ("Contenido denso sobre AimDK, URS y el MC oficial del robot. " * 5) + "\n\n") * 8
    chunks = chunk_text(text, size=400, overlap=80)
    assert all(c.strip() for c in chunks)
    # no hay chunks idénticos consecutivos
    for a, b in zip(chunks, chunks[1:]):
        assert a != b
