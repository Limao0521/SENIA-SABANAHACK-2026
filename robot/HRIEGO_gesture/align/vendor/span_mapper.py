"""Map model token intervals back to manifest word and character spans."""

from __future__ import annotations

from .text_normalization import TextView


def assign_word_times(view: TextView, target_tokens: list[dict], token_spans: list[tuple[int, int]], frame_step_s: float, duration_s: float) -> list[dict]:
    if len(target_tokens) != len(token_spans):
        raise ValueError("token/span count mismatch")
    per_word: dict[int, list[dict]] = {word.index: [] for word in view.words}
    for token, (first, last) in zip(target_tokens, token_spans):
        word_index = token.get("word_index")
        if word_index is None or word_index < 0:
            continue
        per_word[word_index].append(
            {
                "start_frame": int(first),
                "end_frame_exclusive": int(last),
                "supported": bool(token.get("supported", True)),
                "mean_log_probability": token.get("mean_log_probability"),
            }
        )

    output = []
    for word in view.words:
        parts = per_word[word.index]
        supported = bool(parts) and all(p["supported"] for p in parts)
        starts = [p["start_frame"] for p in parts]
        ends = [p["end_frame_exclusive"] for p in parts]
        start_s = min(starts) * frame_step_s if supported else None
        end_s = max(ends) * frame_step_s if supported else None
        if start_s is not None:
            start_s = max(0.0, min(float(start_s), duration_s))
            end_s = max(start_s, min(float(end_s), duration_s))
        output.append(
            {
                "word_index": word.index,
                "word": view.original[word.original_start:word.original_end],
                "word_normalized": word.text,
                "start_char": word.original_start,
                "end_char": word.original_end,
                "start_s": start_s,
                "end_s": end_s,
                "supported": supported,
                "status": "aligned" if supported else "unsupported_or_unmapped_token",
                "mean_token_log_probability": (
                    sum(p["mean_log_probability"] for p in parts if p["mean_log_probability"] is not None) / len(parts)
                    if supported and parts else None
                ),
            }
        )
    return output


def map_anchor(anchor: dict, original_text: str, words: list[dict]) -> dict:
    start_char = int(anchor["anchor_start"])
    end_char = int(anchor["anchor_end"])
    expected = str(anchor["anchor_text"])
    actual = original_text[start_char:end_char]
    if actual != expected:
        return {"status": "manifest_span_text_mismatch", "start_s": None, "end_s": None, "words": []}

    selected = [w for w in words if w["start_char"] < end_char and w["end_char"] > start_char]
    exact = " ".join(w["word_normalized"] for w in selected)
    if not selected or exact != " ".join(expected.lower().split()):
        return {"status": "anchor_word_mapping_mismatch", "start_s": None, "end_s": None, "words": selected}
    if any(not w["supported"] or w["start_s"] is None for w in selected):
        return {"status": "unsupported_target_token", "start_s": None, "end_s": None, "words": selected}

    start_s = min(w["start_s"] for w in selected)
    end_s = max(w["end_s"] for w in selected)
    if end_s <= start_s:
        return {"status": "invalid_interval", "start_s": None, "end_s": None, "words": selected}
    return {"status": "aligned_ctc_not_human_validated", "start_s": start_s, "end_s": end_s, "words": selected}
