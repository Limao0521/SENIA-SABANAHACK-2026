"""Ensamblado de prompts con disciplina de KV cache.

Orden del prefijo (debe quedar BYTE-IDÉNTICO entre turnos para que
llama-server reutilice la cache y el TTFT no se resienta):

    system(persona + guía de estilo [+ resumen rodante])
    → historial activo (append-only)
    → user([contexto RAG + hechos] envueltos junto a la pregunta)

El resumen solo cambia en un rollup (cada N turnos) y el contexto RAG va en el
último mensaje de usuario, así nada de lo anterior se invalida por turno.
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger("llm.prompts")

DEFAULT_PERSONA = """\
Eres el asistente local del robot humanoide AgiBot X2 Ultra (X2 Ultra-N, con
manos OmniHand) del equipo de investigación. Corres 100% en local: ningún dato
de la conversación sale de la máquina.

Conoces el proyecto: simulación MuJoCo, gemelo digital, locomoción RL y la
interfaz AimDK del robot real. Cuando te pregunten por capacidades, modos o
especificaciones, apóyate en el [contexto] recibido si lo hay."""

STYLE_GUIDE = """\
Guía de estilo:
- Responde SIEMPRE en el mismo idioma que use el usuario.
- Respuestas claras y concisas; sin markdown, tablas ni listas kilométricas.
- Si recibes [contexto], úsalo como fuente principal y di si no llega.
- No inventes datos técnicos; si no lo sabes, dilo."""


def load_persona(path: str) -> str:
    """Carga la personalidad de un archivo ( editable sin recompilar la imagen)."""
    try:
        if path and os.path.isfile(path):
            with open(path, encoding="utf-8") as fh:
                text = fh.read().strip()
            if text:
                log.info("persona cargada de %s (%d chars)", path, len(text))
                return text
    except OSError as exc:
        log.warning("no pude leer la persona de %s: %s", path, exc)
    return DEFAULT_PERSONA


def system_message(persona: str, summary: str = "") -> str:
    s = persona.strip() + "\n\n" + STYLE_GUIDE
    if summary and summary.strip():
        s += "\n\nResumen de la conversación anterior:\n" + summary.strip()
    return s


def build_user_message(text: str, contexts: list[dict] | None = None,
                       facts: list[dict] | None = None) -> str:
    """Inyecta el contexto RAG y los hechos de memoria DENTRO del último mensaje
    de usuario (nunca en el system prompt: rompería la KV cache del prefijo)."""
    blocks: list[str] = []
    if facts:
        blocks.append("Lo que recuerdas del usuario: " + " · ".join(f["content"] for f in facts))
    if contexts:
        blocks.extend(f"[doc: {c['source']}]\n{c['text']}" for c in contexts)
    if not blocks:
        return text
    return ("Usa este contexto para responder (ignóralo si no viene a cuento):\n\n"
            + "\n\n".join(blocks)
            + "\n\nPregunta del usuario: " + text)


SUMMARY_INSTRUCTION = """\
Resume la conversación siguiente en como mucho {max_words} palabras: conserva
nombres, cifras, decisiones y hechos relevantes; descarta saludos y relleno.
Responde SOLO con el resumen.{old}"""

FACT_INSTRUCTION = """\
Extrae del intercambio siguiente hechos DURADEROS sobre el usuario o su entorno
(nombre, preferencias, rol, fechas, decisiones, datos del robot). Responde con
un hecho por línea empezando por "- ", o solo "NINGUNO" si no hay ninguno.
No incluyas opinión ni small talk."""
