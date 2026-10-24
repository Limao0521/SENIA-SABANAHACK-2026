"""Deterministic NeMo-style log-mel front ends for the published ONNX graphs.

The Citrinet 80-bin window and mel matrix are read as raw tensors from the
repository's small TorchScript preprocessor archive without loading or
executing TorchScript. QuartzNet uses NeMo's documented default STFT settings
and a Slaney-normalized 64-bin filterbank.
"""

from __future__ import annotations

import math
import struct
import zipfile
from functools import lru_cache
from pathlib import Path

import numpy as np
from scipy.signal import resample_poly


SAMPLE_RATE = 16_000
N_FFT = 512
WIN_LENGTH = 400
HOP_LENGTH = 160
PREEMPHASIS = 0.97
LOG_FLOOR = 5.960464477539063e-08
STD_FLOOR = 5.960464477539063e-08
STD_EPSILON = 1.0e-5


def _slaney_mel_filterbank(n_mels: int) -> np.ndarray:
    """Equivalent to librosa.filters.mel(..., htk=False, norm='slaney')."""
    fmin, fmax = 0.0, SAMPLE_RATE / 2

    def hz_to_mel(frequency: np.ndarray) -> np.ndarray:
        f_sp = 200.0 / 3
        mels = frequency / f_sp
        log_region = frequency >= 1000.0
        min_log_hz = 1000.0
        min_log_mel = min_log_hz / f_sp
        logstep = math.log(6.4) / 27.0
        mels[log_region] = min_log_mel + np.log(frequency[log_region] / min_log_hz) / logstep
        return mels

    def mel_to_hz(mels: np.ndarray) -> np.ndarray:
        f_sp = 200.0 / 3
        frequencies = mels * f_sp
        log_region = mels >= 15.0
        frequencies[log_region] = 1000.0 * np.exp((mels[log_region] - 15.0) * (math.log(6.4) / 27.0))
        return frequencies

    endpoints = hz_to_mel(np.array([fmin, fmax], dtype=np.float64))
    mel_points = np.linspace(endpoints[0], endpoints[1], n_mels + 2, dtype=np.float64)
    hz_points = mel_to_hz(mel_points)
    fft_frequencies = np.linspace(0.0, SAMPLE_RATE / 2, 1 + N_FFT // 2, dtype=np.float64)
    differences = np.diff(hz_points)
    ramps = hz_points[:, None] - fft_frequencies[None, :]
    lower = -ramps[:-2] / differences[:-1, None]
    upper = ramps[2:] / differences[1:, None]
    filters = np.maximum(0.0, np.minimum(lower, upper))
    filters *= (2.0 / (hz_points[2:] - hz_points[:-2]))[:, None]
    return filters.astype(np.float32)


@lru_cache(maxsize=2)
def _frontend_arrays(kind: str, model_dir: str | None) -> tuple[np.ndarray, np.ndarray, dict]:
    if kind == "quartznet15x5_es":
        window = np.hanning(WIN_LENGTH).astype(np.float32)
        bank = _slaney_mel_filterbank(64).T.copy()  # [frequency, mel]
        return window, bank, {
            "source": "NeMo AudioToMelSpectrogramPreprocessor documented defaults",
            "verified_frontend_archive": False,
            "n_fft": N_FFT,
            "win_length": WIN_LENGTH,
            "hop_length": HOP_LENGTH,
            "window": "hann, symmetric/periodic=False",
            "preemphasis": PREEMPHASIS,
            "mel_bins": 64,
            "mel_norm": "slaney",
            "log_floor": LOG_FLOOR,
            "per_feature_normalization": "mean subtraction, sample std (ddof=1), epsilon 1e-5",
            "dither": 0.0,
        }
    if kind == "citrinet512_es":
        if model_dir is None:
            raise ValueError("Citrinet frontend requires its local model directory")
        archive = Path(model_dir) / "onnx" / "preprocessor.ts"
        with zipfile.ZipFile(archive) as zf:
            hop = struct.unpack("<q", zf.read("preprocessor/constants/0"))[0]
            preemphasis = struct.unpack("<d", zf.read("preprocessor/constants/2"))[0]
            log_floor = struct.unpack("<d", zf.read("preprocessor/constants/5"))[0]
            std_epsilon = struct.unpack("<d", zf.read("preprocessor/constants/6"))[0]
            window = np.frombuffer(zf.read("preprocessor/constants/3"), dtype="<f4").copy()
            bank = np.frombuffer(zf.read("preprocessor/constants/4"), dtype="<f4").reshape(257, 80).copy()
        if hop != HOP_LENGTH or window.size != WIN_LENGTH or bank.shape != (257, 80):
            raise ValueError("unexpected Citrinet TorchScript frontend tensor shape or hop")
        return window, bank, {
            "source": "pinned Citrinet repository TorchScript preprocessor constants (read as data only)",
            "verified_frontend_archive": True,
            "n_fft": N_FFT,
            "win_length": WIN_LENGTH,
            "hop_length": int(hop),
            "window": "archived Hann window, symmetric/periodic=False",
            "preemphasis": float(preemphasis),
            "mel_bins": 80,
            "mel_norm": "stored exact NeMo filterbank",
            "log_floor": float(log_floor),
            "std_floor": STD_FLOOR,
            "std_epsilon": float(std_epsilon),
            "dither": 0.0,
        }
    raise ValueError(f"unsupported frontend: {kind}")


def extract_features(audio: np.ndarray, sample_rate: int, kind: str, model_dir: str | None = None) -> tuple[np.ndarray, int, dict]:
    """Return [1, mel_bins, frames] float32 features and the frame length."""
    source = np.asarray(audio, dtype=np.float32).reshape(-1)
    if source.size < 2 or sample_rate <= 0:
        raise ValueError("audio must contain at least two samples at a positive sample rate")
    if sample_rate != SAMPLE_RATE:
        divisor = math.gcd(int(sample_rate), SAMPLE_RATE)
        up, down = SAMPLE_RATE // divisor, int(sample_rate) // divisor
        resampled = resample_poly(source, up, down).astype(np.float32, copy=False)
    else:
        up, down = 1, 1
        resampled = source

    window, bank, frontend_metadata = _frontend_arrays(kind, model_dir)
    preemphasis_coefficient = float(frontend_metadata.get("preemphasis", PREEMPHASIS))
    preemphasized = np.empty_like(resampled)
    preemphasized[0] = resampled[0]
    preemphasized[1:] = resampled[1:] - preemphasis_coefficient * resampled[:-1]

    padded = np.pad(preemphasized, (N_FFT // 2, N_FFT // 2), mode="reflect")
    frame_count = len(preemphasized) // HOP_LENGTH + 1
    shape = (frame_count, N_FFT)
    strides = (padded.strides[0] * HOP_LENGTH, padded.strides[0])
    frames = np.lib.stride_tricks.as_strided(padded, shape=shape, strides=strides, writeable=False)

    fft_window = np.zeros(N_FFT, dtype=np.float32)
    left = (N_FFT - WIN_LENGTH) // 2
    fft_window[left:left + WIN_LENGTH] = window
    spectrum = np.fft.rfft(frames * fft_window[None, :], n=N_FFT, axis=1)
    power = (spectrum.real * spectrum.real + spectrum.imag * spectrum.imag).astype(np.float32)
    mel = power @ bank
    log_mel = np.log(np.maximum(mel + float(frontend_metadata.get("log_floor", LOG_FLOOR)), np.finfo(np.float32).tiny))

    mean = log_mel.mean(axis=0, keepdims=True)
    if frame_count > 1:
        variance = np.square(log_mel - mean).sum(axis=0, keepdims=True) / (frame_count - 1)
    else:
        variance = np.zeros_like(mean)
    std_floor = float(frontend_metadata.get("std_floor", STD_FLOOR))
    std_epsilon = float(frontend_metadata.get("std_epsilon", STD_EPSILON))
    std = np.sqrt(np.maximum(variance, std_floor))
    normalized = ((log_mel - mean) / (std + std_epsilon)).T[None, :, :].astype(np.float32)

    metadata = dict(frontend_metadata)
    metadata.update(
        {
            "input_sample_rate_hz": int(sample_rate),
            "resample_up": int(up),
            "resample_down": int(down),
            "resampled_samples": int(len(resampled)),
            "feature_frames": int(frame_count),
            "feature_step_s": HOP_LENGTH / SAMPLE_RATE,
            "frame_origin_s": 0.0,
        }
    )
    return normalized, frame_count, metadata
