"""Minimal stand-in for ``skimage.transform.SimilarityTransform``.

Only used when scikit-image is not installed (e.g. on board the X2 Ultra, where
the robot has no usable internet to fetch it). Implements exactly what
``face_utils`` needs: construction from scale/rotation/translation, Umeyama
estimation (same algorithm as scikit-image), ``params`` and composition with ``+``.
"""
from __future__ import annotations

import numpy as np


def _umeyama(src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    """Similarity (rotation + uniform scale + translation) least-squares fit."""
    src = np.asarray(src, dtype=np.float64)
    dst = np.asarray(dst, dtype=np.float64)
    num, dim = src.shape
    src_mean = src.mean(axis=0)
    dst_mean = dst.mean(axis=0)
    src_demean = src - src_mean
    dst_demean = dst - dst_mean
    A = dst_demean.T @ src_demean / num
    d = np.ones((dim,), dtype=np.float64)
    if np.linalg.det(A) < 0:
        d[dim - 1] = -1
    T = np.eye(dim + 1, dtype=np.float64)
    U, S, V = np.linalg.svd(A)
    rank = np.linalg.matrix_rank(A)
    if rank == 0:
        return np.nan * T
    if rank == dim - 1:
        if np.linalg.det(U) * np.linalg.det(V) > 0:
            T[:dim, :dim] = U @ V
        else:
            s = d[dim - 1]
            d[dim - 1] = -1
            T[:dim, :dim] = U @ np.diag(d) @ V
            d[dim - 1] = s
    else:
        T[:dim, :dim] = U @ np.diag(d) @ V
    scale = 1.0 / src_demean.var(axis=0).sum() * (S @ d)
    T[:dim, dim] = dst_mean - scale * (T[:dim, :dim] @ src_mean.T)
    T[:dim, :dim] *= scale
    return T


class SimilarityTransform:
    def __init__(self, matrix=None, scale=None, rotation=None, translation=None) -> None:
        if matrix is not None:
            self.params = np.asarray(matrix, dtype=np.float64)
            return
        s = 1.0 if scale is None else float(scale)
        r = 0.0 if rotation is None else float(rotation)
        tx, ty = (0.0, 0.0) if translation is None else translation
        self.params = np.array([[s * np.cos(r), -s * np.sin(r), tx],
                                [s * np.sin(r), s * np.cos(r), ty],
                                [0.0, 0.0, 1.0]], dtype=np.float64)

    def estimate(self, src, dst) -> bool:
        self.params = _umeyama(src, dst)
        return bool(np.all(np.isfinite(self.params)))

    @classmethod
    def from_estimate(cls, src, dst) -> "SimilarityTransform":
        tform = cls()
        tform.estimate(src, dst)
        return tform

    def __add__(self, other: "SimilarityTransform") -> "SimilarityTransform":
        # Same semantics as scikit-image: apply self first, then other.
        return SimilarityTransform(matrix=other.params @ self.params)
