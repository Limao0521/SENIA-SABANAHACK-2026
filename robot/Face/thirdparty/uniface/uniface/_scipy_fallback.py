"""NumPy stand-ins for the two SciPy routines the tracker uses.

Only used when SciPy is not installed (e.g. on board the X2 Ultra without usable
internet). Results match ``scipy.optimize.linear_sum_assignment`` (minimum-cost
assignment, rectangular allowed, rows sorted) and ``scipy.linalg.cho_factor`` /
``cho_solve`` for the small dense matrices of ByteTrack.
"""
from __future__ import annotations

import numpy as np


def linear_sum_assignment(cost_matrix) -> tuple[np.ndarray, np.ndarray]:
    """Hungarian algorithm (shortest augmenting path, O(n^2 m))."""
    cost = np.asarray(cost_matrix, dtype=np.float64)
    if cost.ndim != 2:
        raise ValueError("expected a matrix (2-d array)")
    if cost.size == 0:
        return np.empty(0, dtype=np.intp), np.empty(0, dtype=np.intp)
    if not np.all(np.isfinite(cost)):
        raise ValueError("matrix contains invalid numeric entries")
    transposed = cost.shape[0] > cost.shape[1]
    if transposed:
        cost = cost.T
    n, m = cost.shape  # n <= m
    u = np.zeros(n + 1)
    v = np.zeros(m + 1)
    p = np.zeros(m + 1, dtype=np.intp)   # p[j]: row (1-based) assigned to column j
    way = np.zeros(m + 1, dtype=np.intp)
    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        minv = np.full(m + 1, np.inf)
        used = np.zeros(m + 1, dtype=bool)
        while True:
            used[j0] = True
            i0 = p[j0]
            free = ~used[1:]
            cur = cost[i0 - 1] - u[i0] - v[1:]
            better = free & (cur < minv[1:])
            minv[1:][better] = cur[better]
            way[1:][better] = j0
            cand = np.where(free, minv[1:], np.inf)
            j1 = int(np.argmin(cand)) + 1
            delta = cand[j1 - 1]
            u[p[used]] += delta
            v[used] -= delta
            minv[1:][free] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while True:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
            if j0 == 0:
                break
    rows = p[1:] - 1
    cols = np.arange(m)
    assigned = rows >= 0
    rows, cols = rows[assigned], cols[assigned]
    if transposed:
        rows, cols = cols, rows
    order = np.argsort(rows)
    return rows[order].astype(np.intp), cols[order].astype(np.intp)


def cho_factor(a, lower: bool = False, check_finite: bool = True):
    c = np.linalg.cholesky(np.asarray(a, dtype=np.float64))
    return (c if lower else c.T), lower


def cho_solve(c_and_lower, b, check_finite: bool = True):
    c, lower = c_and_lower
    low = c if lower else c.T
    y = np.linalg.solve(low, b)
    return np.linalg.solve(low.T, y)
