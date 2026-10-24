"""Isolated CPU TTS benchmark using HRIEGO's last real utterances."""
from __future__ import annotations

import json
import subprocess
import threading
import time
from pathlib import Path

import psutil
from supertonic import TTS


ROOT = Path(__file__).resolve().parent
CASES = [
    ("GREETING", "Bienvenido, Luis. Estamos en el laboratorio.", 1.05),
    ("GUIDANCE", "En este escenario, el laboratorio está situado a mi derecha.", 1.0),
    ("FAREWELL", "Nos vemos pronto, Luis.", 0.98),
]


def available_ram() -> int:
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) * 1024
    raise RuntimeError("MemAvailable unavailable")


def gpu_used_mib() -> int | None:
    result = subprocess.run(
        ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
        capture_output=True, text=True, timeout=3,
    )
    return int(result.stdout.splitlines()[0].strip()) if result.returncode == 0 else None


def main() -> None:
    if available_ram() < 5 * 1024**3:
        raise MemoryError("Need 5 GiB available RAM to start benchmark")
    stop = threading.Event()
    proc = psutil.Process()
    samples: list[dict] = []

    def watch() -> None:
        while not stop.wait(.25):
            samples.append({"ram_available_bytes": available_ram(), "rss_bytes": proc.memory_info().rss,
                            "gpu_used_mib": gpu_used_mib()})
            if samples[-1]["ram_available_bytes"] < 2 * 1024**3:
                stop.set()

    thread = threading.Thread(target=watch, daemon=True)
    thread.start()
    rows = []
    try:
        start = time.perf_counter()
        tts = TTS(model_dir=ROOT / "model", auto_download=False,
                  intra_op_num_threads=4, inter_op_num_threads=1)
        load_s = time.perf_counter() - start
        for voice in ("M1", "M2"):
            style = tts.get_voice_style(voice)
            for state, speech, speed in CASES:
                if stop.is_set():
                    raise MemoryError("Benchmark guard: available RAM below 2 GiB")
                started = time.perf_counter()
                audio, duration = tts.synthesize(speech, voice_style=style,
                                                  total_steps=8, speed=speed, lang="es")
                synthesis_s = time.perf_counter() - started
                output = ROOT / "output" / voice / f"{state.lower()}.wav"
                output.parent.mkdir(parents=True, exist_ok=True)
                tts.save_audio(audio, str(output))
                audio_s = float(duration[0])
                row = {"voice": voice, "state": state, "speech": speech,
                       "synthesis_s": round(synthesis_s, 4), "audio_s": round(audio_s, 4),
                       "rtf": round(synthesis_s / audio_s, 4), "wav": str(output)}
                rows.append(row)
                print("CASE", json.dumps(row, ensure_ascii=False), flush=True)
        report = {"model": "Supertone/supertonic-3", "backend": "onnxruntime CPU",
                  "load_s": round(load_s, 4), "sample_rate": tts.sample_rate,
                  "min_ram_available_gib": round(min(x["ram_available_bytes"] for x in samples) / 1024**3, 3),
                  "max_rss_mib": round(max(x["rss_bytes"] for x in samples) / 1024**2),
                  "max_gpu_used_mib": max((x["gpu_used_mib"] for x in samples if x["gpu_used_mib"] is not None), default=None),
                  "cases": rows}
        (ROOT / "results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print("SUMMARY", json.dumps({k: v for k, v in report.items() if k != "cases"}, ensure_ascii=False), flush=True)
    finally:
        stop.set()
        thread.join(timeout=2)


if __name__ == "__main__":
    main()
