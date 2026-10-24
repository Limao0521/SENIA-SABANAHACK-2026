#!/usr/bin/env python
"""Bench T3 ASR: latencias warm p50/p95, RTF, RSS/VRAM pico, streaming VAD.

Uso: .venv-asr/bin/python -I out_diag/asr/bench.py [reps]
Escribe out_diag/asr/bench_results.json
"""
import json
import os
import subprocess
import sys
import threading
import time

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "src"))

REPS = int(sys.argv[1]) if len(sys.argv) > 1 else 3
WAV_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wav")
WAVS = [f"wav{i}.wav" for i in range(1, 7)]

from x2_ultra.hri_runtime.asr import Asr, StreamingAsr, SR  # noqa: E402


def gpu_mem_mb():
    out = subprocess.run(
        ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
        capture_output=True, text=True).stdout.strip().splitlines()
    return int(out[0])


def gpu_mem_self_mb():
    """VRAM de ESTE proceso (la GPU es compartida)."""
    out = subprocess.run(
        ["nvidia-smi", "--query-compute-apps=pid,used_memory",
         "--format=csv,noheader,nounits"],
        capture_output=True, text=True).stdout.strip()
    for line in out.splitlines():
        pid, mem = [x.strip() for x in line.split(",")]
        if int(pid) == os.getpid():
            return int(mem)
    return 0


def peak_rss_mb():
    import resource
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def main():
    t0 = time.perf_counter()
    asr = Asr(model_size="large-v3-turbo", device="cuda", compute_type="int8")
    load_s = time.perf_counter() - t0
    print(f"modelo cargado+warmup en {load_s:.1f}s")

    # --- VRAM atribuible: antes/después de carga ya hecha; medir delta del proceso
    vram_after_load = gpu_mem_self_mb()
    print(f"VRAM propio tras carga: {vram_after_load} MiB (GPU total usada: {gpu_mem_mb()} MiB)")

    # --- Transcripciones y latencias warm
    results = {}
    all_lat = []
    total_audio = 0.0
    total_compute = 0.0
    for w in WAVS:
        path = os.path.join(WAV_DIR, w)
        r0 = asr.transcribe(path)
        total_audio += r0["audio_dur_s"]
        lats = [r0["latency_s"]]
        for _ in range(REPS - 1):
            r = asr.transcribe(path)
            lats.append(r["latency_s"])
        total_compute += sum(lats)
        results[w] = {
            "text": r0["text"],
            "audio_dur_s": round(r0["audio_dur_s"], 2),
            "lat_s": [round(x, 3) for x in lats],
            "lat_p50_s": round(float(np.percentile(lats, 50)), 3),
        }
        all_lat.extend(lats)
        print(f"{w}: {r0['latency_s']:.3f}s | {r0['text'][:90]}")

    # --- Streaming con VAD: latencia fin-de-audio -> texto
    stream = StreamingAsr(asr)
    stream_lat = []
    for w in WAVS:
        path = os.path.join(WAV_DIR, w)
        import soundfile as sf
        pcm, sr = sf.read(path, dtype="float32")
        # simular llegada en tiempo real: chunks de 32 ms; medir t(audio end)->t(transcript)
        chunk = int(0.032 * SR)
        res = None
        t_audio_end = None
        stream.reset()
        for i in range(0, len(pcm), chunk):
            r = stream.feed(pcm[i : i + chunk])
            if r is not None:
                res = r
                break
        if res is None:
            res = stream.flush()
        t_done = time.perf_counter()
        # el "fin de audio" real = cuando termina el archivo en la simulación;
        # aprox: usamos t_end interno del transcript menos duración post-utterance
        stream_lat.append(res["latency_s"])
        print(f"stream {w}: endpoint+vad+transcribe {res['latency_s']:.3f}s text={res['text'][:60]}")

    peak_rss = peak_rss_mb()
    vram_self = gpu_mem_self_mb()
    vram_now = gpu_mem_mb()

    summary = {
        "model": "large-v3-turbo (mobiuslabsgmbh/faster-whisper-large-v3-turbo), int8, cuda",
        "load_s": round(load_s, 1),
        "reps": REPS,
        "lat_all_p50_s": round(float(np.percentile(all_lat, 50)), 3),
        "lat_all_p95_s": round(float(np.percentile(all_lat, 95)), 3),
        "rtf": round(total_compute / total_audio, 3),
        "total_audio_s": round(total_audio, 1),
        "peak_rss_mb": round(peak_rss, 0),
        "vram_self_mb_after_load": vram_after_load,
        "vram_self_mb_end": vram_self,
        "gpu_total_used_mb_end": vram_now,
        "stream_endpoint_transcribe_lat_s": [round(x, 3) for x in stream_lat],
        "per_wav": results,
    }
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bench_results.json")
    with open(out, "w") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
