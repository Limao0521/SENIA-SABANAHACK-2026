#!/usr/bin/env python3
"""Servidor de movimiento online (T4): proceso propio en el entorno ``physicalai``.

Reutiliza SIN MODIFICAR la cadena probada de HRIEGO_gesture/opt:
- ``m2_motion_server`` (StreamTalk fp16 streaming + GMR 15 Hz + SONIC online, serial pull),
- ``o3_brain_motor/brain_server.Brain`` (modelos V2 de o1a_meshes + first-block split),
  configurado como en la parte B de o3: v2, rate 15, prefix fast, split 14, armed.

IPC: UNIX socket, JSON-lines + float32 en base64 (ver protocol.py). Un cliente a la vez;
cada ``start_turn`` corre ``Brain.run`` con un sink que envía cada lote de ticks de
inmediato (50 Hz, arms14+waist3+head2 = 19 float32 por tick).

Uso:  ~/anaconda3/envs/physicalai/bin/python server_proc.py --sock /tmp/hri_motion_online.sock
"""
from __future__ import annotations

import argparse
import os
import socket
import sys
import time
from pathlib import Path

_VENDORED = Path(__file__).resolve().parents[4] / "third_party/hriego_opt"   # copia del código (sin pesos)
HRIEGO_OPT = Path(os.environ.get("HRIEGO_OPT", str(_VENDORED)))
O3 = HRIEGO_OPT / "o3_brain_motor"
M2 = HRIEGO_OPT / "m2_motion_server"
sys.path.insert(0, str(O3))
sys.path.insert(0, str(M2))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np  # noqa: E402

import protocol as P  # noqa: E402


def log(*a):
    print(f"[motion-server {time.strftime('%H:%M:%S')}]", *a, flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sock", default="/tmp/hri_motion_online.sock")
    ap.add_argument("--models", default="v2")
    ap.add_argument("--rate", type=int, default=15)
    ap.add_argument("--split", type=int, default=14)
    ap.add_argument("--prefix", default="fast")
    ap.add_argument("--no-warmup", action="store_true")
    a = ap.parse_args()
    os.environ.setdefault("MUJOCO_GL", "egl")
    for v in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
        os.environ.setdefault(v, "2")

    from brain_server import Brain
    from m2common import smaps, trim, vram_mb

    brain = Brain(models=a.models, rate=a.rate, prefix=a.prefix, solver="native",
                  pipeline="serial", warm_iters=20, armed=True, ort_threads=2)
    t0 = time.perf_counter()
    load_s = brain.load()
    log("LOAD", {k: round(v, 1) for k, v in load_s.items()},
        "pss_mb", round(smaps()["Pss"]), "vram_mb", vram_mb())
    if not a.no_warmup:
        brain.arm()
        w, _ = brain.run(np.zeros(16000 * 3, np.float32), split=a.split)
        trim()
        log("WARMUP first_cmd_s", round(w["first_cmd_s"], 3))
    log("READY", round(time.perf_counter() - t0, 1), "s")

    sock_path = Path(a.sock)
    sock_path.unlink(missing_ok=True)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(sock_path))
    os.chmod(sock_path, 0o777)
    srv.listen(1)
    log("listening", sock_path)

    while True:
        conn, _ = srv.accept()
        log("client connected")
        try:
            handle(conn, brain, a, smaps, vram_mb, trim)
        except Exception as e:                                   # noqa: BLE001
            import traceback
            traceback.print_exc()
            try:
                P.send_line(conn, {"cmd": "error", "msg": f"{type(e).__name__}: {e}"})
            except OSError:
                pass
        finally:
            conn.close()
            log("client disconnected")


def handle(conn, brain, a, smaps, vram_mb, trim):
    P.send_line(conn, {"cmd": "ready"})
    buf = bytearray()
    for msg in P.recv_lines(buf, conn):
        if msg.get("cmd") != "start_turn":
            P.send_line(conn, {"cmd": "error", "msg": f"cmd desconocido {msg.get('cmd')!r}"})
            continue
        audio = P.decode_audio(msg["b64"], int(msg["n"]))
        sr = int(msg.get("sr", 16000))
        if sr != 16000:                                          # remuestreo lineal a 16 kHz
            x0 = np.arange(len(audio), dtype=np.float64) / sr
            x1 = np.arange(0.0, len(audio) / sr, 1.0 / 16000.0)
            audio = np.interp(x1, x0, audio).astype(np.float32)
        split = int(msg.get("split", a.split))
        rtf = float(msg.get("audio_rtf", 0.0))
        log(f"turn: {len(audio)/16000:.1f}s audio, split={split}, rtf={rtf}")
        brain.arm()
        t_req = time.perf_counter()

        def sink(k0, arr, t_mono, _c=conn):
            P.send_line(_c, {"cmd": "frames", "k0": int(k0), "n": int(arr.shape[0]),
                             "t_mono": t_mono, "t_req": time.perf_counter() - t_req,
                             "b64": P.encode_frames(arr)})

        res, _ = brain.run(audio, sink=sink, split=split, audio_rtf=rtf,
                           t_origin=t_req)
        res["mem"] = {"pss_mb": smaps()["Pss"], "vram_mb": vram_mb()}
        P.send_line(conn, {"cmd": "done", "res": res})
        log("turn done: first_cmd", round(res["first_cmd_s"], 3),
            "first_motion_cmd", res.get("first_motion_cmd_s"),
            "rtf", round(res["rtf"], 2), "ticks", res["ticks"])
        trim()


if __name__ == "__main__":
    main()
