"""Configuración de la API, todo por variables de entorno (ver .env.example)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _s(name: str, default: str) -> str:
    return os.getenv(name, default)


def _i(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


def _f(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


def _b(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
    # --- LLM (llama-server, mismo contenedor) ---
    llm_base_url: str = field(default_factory=lambda: _s("LLM_BASE_URL", "http://127.0.0.1:8080/v1"))
    llm_api_key: str = field(default_factory=lambda: _s("LLM_API_KEY", "not-needed"))
    llm_model: str = field(default_factory=lambda: _s("LLM_MODEL", "local"))
    llm_wait_timeout: float = field(default_factory=lambda: _f("LLM_WAIT_TIMEOUT", 900.0))

    # --- Generación ---
    max_tokens: int = field(default_factory=lambda: _i("MAX_TOKENS", 512))
    temperature: float = field(default_factory=lambda: _f("TEMPERATURE", 0.7))
    enable_thinking: bool = field(default_factory=lambda: _b("ENABLE_THINKING", False))

    # --- Memoria ---
    db_path: str = field(default_factory=lambda: _s("DB_PATH", "/data/agent.db"))
    persona_file: str = field(default_factory=lambda: _s("PERSONA_FILE", "/persona/persona.md"))
    history_max_messages: int = field(default_factory=lambda: _i("HISTORY_MAX_MESSAGES", 24))
    history_keep_messages: int = field(default_factory=lambda: _i("HISTORY_KEEP_MESSAGES", 12))
    summary_max_words: int = field(default_factory=lambda: _i("SUMMARY_MAX_WORDS", 200))
    fact_extract: bool = field(default_factory=lambda: _b("FACT_EXTRACT", False))
    fact_top_k: int = field(default_factory=lambda: _i("FACT_TOP_K", 2))

    # --- RAG ---
    rag_enabled: bool = field(default_factory=lambda: _b("RAG_ENABLED", True))
    rag_autoingest: bool = field(default_factory=lambda: _b("RAG_AUTOINGEST", True))
    rag_corpus_dir: str = field(default_factory=lambda: _s("RAG_CORPUS_DIR", "/corpus"))
    rag_top_k: int = field(default_factory=lambda: _i("RAG_TOP_K", 3))
    rag_min_score: float = field(default_factory=lambda: _f("RAG_MIN_SCORE", 0.40))
    rag_chunk_chars: int = field(default_factory=lambda: _i("RAG_CHUNK_CHARS", 600))
    rag_chunk_overlap: int = field(default_factory=lambda: _i("RAG_CHUNK_OVERLAP", 120))
    embed_model: str = field(default_factory=lambda: _s("EMBED_MODEL", "minishlab/potion-multilingual-128M"))
    embed_cache_dir: str = field(default_factory=lambda: _s("FASTEMBED_CACHE_PATH", "/data/fastembed"))

    # --- Varios ---
    metrics_history: int = field(default_factory=lambda: _i("METRICS_HISTORY", 50))
