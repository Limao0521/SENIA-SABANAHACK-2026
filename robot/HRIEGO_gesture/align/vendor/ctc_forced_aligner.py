"""Viterbi forced alignment over frame-wise CTC log probabilities."""

from __future__ import annotations

import numpy as np


class AlignmentError(ValueError):
    pass


def log_softmax(scores: np.ndarray) -> np.ndarray:
    scores = np.asarray(scores, dtype=np.float32)
    if scores.ndim != 2 or not np.isfinite(scores).all():
        raise AlignmentError("emissions must be a finite [frames, classes] matrix")
    maximum = scores.max(axis=1, keepdims=True)
    shifted = scores - maximum
    return shifted - np.log(np.exp(shifted).sum(axis=1, keepdims=True))


def ctc_viterbi(logits: np.ndarray, target_ids: list[int], blank_id: int) -> dict:
    """Return frame spans for each target symbol; end frames are exclusive.

    The dynamic program uses O(frames * target_symbols) backpointers. It
    returns a diagnostic path score, not a calibrated confidence estimate.
    """
    if not target_ids:
        raise AlignmentError("empty target sequence")
    logp = log_softmax(logits)
    frames, classes = logp.shape
    if blank_id < 0 or blank_id >= classes:
        raise AlignmentError(f"blank id {blank_id} outside {classes} output classes")
    if min(target_ids) < 0 or max(target_ids) >= classes:
        raise AlignmentError("target id outside model output classes")

    required = len(target_ids) + sum(a == b for a, b in zip(target_ids, target_ids[1:]))
    if frames < required:
        raise AlignmentError(f"too few frames ({frames}) for CTC target requiring {required}")

    states = np.full(2 * len(target_ids) + 1, blank_id, dtype=np.int64)
    states[1::2] = target_ids
    state_count = len(states)
    # A long target can exceed 127 states; int8 backpointers silently wrap.
    back = np.full((frames, state_count), -1, dtype=np.int32)
    previous = np.full(state_count, -np.inf, dtype=np.float32)

    for t in range(frames):
        current = np.full(state_count, -np.inf, dtype=np.float32)
        for s in range(state_count):
            best_score = previous[s]
            parent = s
            if s > 0 and previous[s - 1] > best_score:
                best_score = previous[s - 1]
                parent = s - 1
            if (
                s > 1
                and states[s] != blank_id
                and states[s] != states[s - 2]
                and previous[s - 2] > best_score
            ):
                best_score = previous[s - 2]
                parent = s - 2

            if t == 0:
                if s == 0:
                    best_score, parent = 0.0, 0
                elif s == 1:
                    best_score, parent = 0.0, 0
                else:
                    continue
            if np.isfinite(best_score):
                current[s] = best_score + logp[t, states[s]]
                back[t, s] = parent
        previous = current

    if state_count == 1:
        final_state = 0
    else:
        candidates = [state_count - 1, state_count - 2]
        final_state = max(candidates, key=lambda s: previous[s])
    if not np.isfinite(previous[final_state]):
        raise AlignmentError("no legal CTC path")

    path = np.full(frames, -1, dtype=np.int32)
    state = int(final_state)
    for t in range(frames - 1, -1, -1):
        path[t] = state
        parent = int(back[t, state])
        if t > 0:
            if parent < 0:
                raise AlignmentError("broken CTC backtrace")
            state = parent

    token_spans = []
    token_mean_logp = []
    for i, token_id in enumerate(target_ids):
        token_state = 2 * i + 1
        indices = np.flatnonzero(path == token_state)
        if indices.size == 0:
            raise AlignmentError(f"target token {i} received no frames")
        start, end = int(indices[0]), int(indices[-1]) + 1
        token_spans.append((start, end))
        token_mean_logp.append(float(logp[start:end, token_id].mean()))

    return {
        "token_spans": token_spans,
        "token_mean_log_probabilities": token_mean_logp,
        "path_state_ids": path,
        "mean_token_log_probability": float(np.mean(token_mean_logp)),
        "total_path_log_probability": float(previous[final_state]),
        "frame_count": frames,
    }
