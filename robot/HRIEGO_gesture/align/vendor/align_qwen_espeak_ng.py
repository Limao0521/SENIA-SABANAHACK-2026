#!/usr/bin/env python3
"""[VENDORED, EXTRACTED] Only make_ctc_text_view / map_anchor_for_e7 / prepare_for_e7 of Laplace E7
align_qwen_espeak_ng.py (original: QuartzNet 15x5 CPU alignment over low-memory eSpeak NG synthetic speech).
The batch driver main(), E7 data paths (ROOT/REPO/E4/MODEL/PLAN...) and psutil memory helpers were dropped; the
functions below are byte-identical to the source. See PROVENANCE.md."""
from __future__ import annotations

import time

import numpy as np


def make_ctc_text_view(text: str, make_text_view, TextView, Word):
    """Expand only X2 to its Spanish spoken form while mapping chars to raw offsets."""
    base = make_text_view(text)
    chars: list[str] = []
    offsets: list[int] = []
    char_to_word: list[int] = []
    words = []

    def append_word(surface: str, source_offsets: list[int], original_start: int, original_end: int) -> None:
        if chars:
            chars.append(" ")
            offsets.append(max(0, original_start))
            char_to_word.append(-1)
        word_id = len(words)
        start = len(chars)
        chars.extend(surface)
        offsets.extend(source_offsets)
        char_to_word.extend([word_id] * len(surface))
        words.append(Word(
            index=word_id,
            text=surface,
            norm_start=start,
            norm_end=len(chars),
            original_start=original_start,
            original_end=original_end,
        ))

    for word in base.words:
        if word.text == "x2":
            x_offset = base.char_to_original[word.norm_start]
            digit_offset = base.char_to_original[word.norm_end - 1]
            append_word("equis", [x_offset] * len("equis"), x_offset, x_offset + 1)
            append_word("dos", [digit_offset] * len("dos"), digit_offset, digit_offset + 1)
        else:
            if any(character.isdigit() for character in word.text):
                raise ValueError(f"no frozen Spanish verbalization rule for numeric token {word.text!r}")
            source_offsets = list(base.char_to_original[word.norm_start:word.norm_end])
            append_word(word.text, source_offsets, word.original_start, word.original_end)

    normalized = "".join(chars)
    decisions = base.decisions + ("expand X2 as 'equis dos' for QuartzNet Spanish vocabulary; preserve original Qwen offsets",)
    return TextView(
        original=text,
        normalized=normalized,
        char_to_original=tuple(offsets),
        char_to_word=tuple(char_to_word),
        words=tuple(words),
        decisions=decisions,
    )


def map_anchor_for_e7(anchor: dict, view, words: list[dict], make_text_view, TextView, Word) -> dict:
    start_char = int(anchor["anchor_start"])
    end_char = int(anchor["anchor_end"])
    expected = str(anchor["anchor_text"])
    if view.original[start_char:end_char] != expected:
        return {"status": "manifest_span_text_mismatch", "start_s": None, "end_s": None, "words": []}
    selected = [word for word in words if word["start_char"] < end_char and word["end_char"] > start_char]
    expected_normalized = make_ctc_text_view(expected, make_text_view, TextView, Word).normalized
    actual_normalized = " ".join(word["word_normalized"] for word in selected)
    if not selected or actual_normalized != expected_normalized:
        return {
            "status": "anchor_word_mapping_mismatch",
            "start_s": None,
            "end_s": None,
            "words": selected,
            "anchor_normalized_for_ctc": expected_normalized,
        }
    if any(not word["supported"] or word["start_s"] is None for word in selected):
        return {
            "status": "unsupported_target_token",
            "start_s": None,
            "end_s": None,
            "words": selected,
            "anchor_normalized_for_ctc": expected_normalized,
        }
    start_s = min(word["start_s"] for word in selected)
    end_s = max(word["end_s"] for word in selected)
    if end_s <= start_s:
        return {
            "status": "invalid_interval",
            "start_s": None,
            "end_s": None,
            "words": selected,
            "anchor_normalized_for_ctc": expected_normalized,
        }
    return {
        "status": "aligned_ctc_not_human_validated",
        "start_s": start_s,
        "end_s": end_s,
        "words": selected,
        "anchor_normalized_for_ctc": expected_normalized,
    }


def prepare_for_e7(adapter, audio, sample_rate: int, text: str, extract_features, make_text_view, TextView, Word) -> dict:
    start = time.perf_counter()
    features, feature_length, frontend_metadata = extract_features(
        audio, sample_rate, adapter.model_key, str(adapter.model_dir)
    )
    preprocess_s = time.perf_counter() - start
    start = time.perf_counter()
    view = make_ctc_text_view(text, make_text_view, TextView, Word)
    target_tokens = adapter.encode_target(view)
    tokenize_s = time.perf_counter() - start
    adapter.frontend_metadata = frontend_metadata
    return {
        "audio": np.asarray(audio, dtype=np.float32).reshape(-1),
        "duration_s": len(audio) / int(sample_rate),
        "sample_rate": int(sample_rate),
        "features": features,
        "feature_length": int(feature_length),
        "view": view,
        "target_tokens": target_tokens,
        "preprocess_s": preprocess_s,
        "tokenize_s": tokenize_s,
        "frontend_metadata": frontend_metadata,
    }


