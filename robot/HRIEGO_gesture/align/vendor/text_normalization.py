"""Loss-minimizing text cleanup with a reversible character-offset map.

Punctuation is represented as a word boundary, whitespace is collapsed,
letter case is lowered, and Spanish diacritics are preserved. No accent
folding or semantic rewriting is performed.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Word:
    index: int
    text: str
    norm_start: int
    norm_end: int
    original_start: int
    original_end: int


@dataclass(frozen=True)
class TextView:
    original: str
    normalized: str
    char_to_original: tuple[int, ...]
    char_to_word: tuple[int, ...]
    words: tuple[Word, ...]
    decisions: tuple[str, ...]


def make_text_view(text: str) -> TextView:
    out: list[str] = []
    offsets: list[int] = []
    decisions = [
        "lowercase",
        "replace punctuation and symbols with a space",
        "collapse repeated whitespace",
        "preserve Spanish accents and ñ",
        "do not fold accents or expand abbreviations",
    ]

    for i, char in enumerate(text):
        if char.isalpha() or char.isdigit():
            lowered = char.lower()
            for item in lowered:
                out.append(item)
                offsets.append(i)
        else:
            item = " "
            if not out or out[-1] != " ":
                out.append(item)
                offsets.append(i)

    while out and out[0] == " ":
        out.pop(0)
        offsets.pop(0)
    while out and out[-1] == " ":
        out.pop()
        offsets.pop()

    normalized = "".join(out)
    words: list[Word] = []
    char_to_word = [-1] * len(out)
    i = 0
    while i < len(out):
        if out[i] == " ":
            i += 1
            continue
        start = i
        while i < len(out) and out[i] != " ":
            i += 1
        end = i
        word_id = len(words)
        original_start = min(offsets[start:end])
        original_end = max(offsets[start:end]) + 1
        words.append(
            Word(
                index=word_id,
                text=normalized[start:end],
                norm_start=start,
                norm_end=end,
                original_start=original_start,
                original_end=original_end,
            )
        )
        for j in range(start, end):
            char_to_word[j] = word_id

    return TextView(
        original=text,
        normalized=normalized,
        char_to_original=tuple(offsets),
        char_to_word=tuple(char_to_word),
        words=tuple(words),
        decisions=tuple(decisions),
    )
