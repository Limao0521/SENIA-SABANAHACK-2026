"""Torch/librosa-free wav loader: scipy.io.wavfile + resample_poly to 16 kHz mono float32 (stand-in for librosa.load(sr=16000))."""
import numpy as np
def load_wav16(path):
    from scipy.io import wavfile
    from scipy.signal import resample_poly
    from math import gcd
    sr, x = wavfile.read(path)
    x = x.astype(np.float32) / (32768.0 if x.dtype == np.int16 else (2147483648.0 if x.dtype == np.int32 else 1.0))
    if x.ndim > 1: x = x.mean(1)
    if sr != 16000: g = gcd(sr, 16000); x = resample_poly(x, 16000 // g, sr // g).astype(np.float32)
    return x.astype(np.float32)
