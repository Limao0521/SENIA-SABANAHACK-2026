#!/usr/bin/env python3
"""T4 bench: métricas online sobre 3 WAV de turnos del Reto 2 + RMSE vs el npz precomputado.

Uso (venv del repo o cualquiera con numpy):
  python -m x2_ultra.hri_runtime.motion_online.bench --out out_diag/motion_online
"""
from __future__ import annotations

import argparse
import json
import time
import wave
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[4]
VISITS = REPO / "assets/hri/HRIEGO_gesture/opt/realnames/visits"


def load_wav(path: Path) -> tuple[np.ndarray, int]:
    w = wave.open(str(path))
    sr, n, ch = w.getframerate(), w.getnframes(), w.getnchannels()
    pcm = np.frombuffer(w.readframes(n), np.int16 if w.getsampwidth() == 2 else np.uint8)
    if w.getsampwidth() != 2:
        pcm = (pcm.astype(np.int16) - 128) * 256
    if ch > 1:
        pcm = pcm.reshape(-1, ch).mean(1).astype(np.int16)
    return pcm, sr


def wav_npz(turn: str) -> tuple[Path, Path]:
    person, tid = turn.split("_", 1)                     # laura_t02_dialogue
    base = VISITS / person
    return (base / f"wav/ps_{person}/ps_{turn}.wav",
            base / f"sonic/ps_{turn}/ps_{turn}_arms.npz")


def rmse_aligned(online: np.ndarray, ref: np.ndarray, max_shift: int = 300, skip: int = 100):
    """RMSE online vs precomputado con desfase entero óptimo (ticks 50 Hz).

    ``skip`` descarta los primeros ticks del solape: los prefijos de entrada difieren
    (fast 27 online vs el prefijo largo offline) — comparamos la parte de movimiento,
    como en opt/o3_brain_motor ("arm RMSE, motion part").
    """
    best = (np.inf, 0)
    n = min(len(online), len(ref))
    for s in range(-max_shift, max_shift + 1):
        m = n - abs(s)
        if m - skip < 50:
            continue
        a = online[max(0, s) + skip: max(0, s) + m]
        b = ref[max(0, -s) + skip: max(0, -s) + m]
        r = float(np.sqrt(((a - b) ** 2).mean()))
        if r < best[0]:
            best = (r, s)
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--turns", default="laura_t02_dialogue,josaed_t03_dialogue,sebastian_t02_dialogue")
    ap.add_argument("--out", default=str(REPO / "out_diag/motion_online"))
    ap.add_argument("--sock", default="/tmp/hri_motion_online.sock")
    ap.add_argument("--no-start", action="store_true")
    ap.add_argument("--paced", default="")               # turno extra con audio_rtf=1.0 (stream real)
    a = ap.parse_args()
    from x2_ultra.hri_runtime.motion_online import MotionServer

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    ms = MotionServer(sock_path=a.sock, autostart=not a.no_start)
    rows = []
    plan = [(t, 0.0) for t in a.turns.split(",")] + ([(a.paced, 1.0)] if a.paced else [])
    for turn, rtf in plan:
        wav, ref_npz = wav_npz(turn)
        pcm, sr = load_wav(wav)
        t0 = time.perf_counter()
        frames = []
        for t, arms, waist, head in ms.start_turn(pcm, sr, 0.0, audio_rtf=rtf):
            frames.append(np.concatenate([[t], arms, waist, head]))
        wall = time.perf_counter() - t0
        A = np.asarray(frames, np.float32)
        np.savez(out / f"{turn}_online.npz", t=A[:, 0], arms=A[:, 1:15],
                 waist=A[:, 15:18], head=A[:, 18:20])
        res = dict(ms.last_res or {})
        mem = res.pop("mem", {})
        wins = res.pop("windows", [])
        row = dict(turn=turn, audio_rtf=rtf, client_wall_s=wall, n_frames=len(A),
                   audio_s=res.get("audio_s"), first_cmd_s=res.get("first_cmd_s"),
                   first_motion_cmd_s=res.get("first_motion_cmd_s"),
                   lead_D_s=res.get("lead_D_s"), audio_start_s=res.get("audio_start_s"),
                   lag_last_s=res.get("lag_last_s"), rtf=res.get("rtf"),
                   fell=res.get("fell"), tilt_max=res.get("tilt_max"),
                   pss_mb=mem.get("pss_mb"), vram_mb=mem.get("vram_mb"))
        # motion frame vs audio start: el audio arranca en audio_start_s (convención o3)
        if res.get("audio_start_s") is not None and res.get("first_motion_cmd_s") is not None:
            row["motion_minus_audio_start_s"] = res["first_motion_cmd_s"] - res["audio_start_s"]
        # cómputo por ventana vs tiempo real de la ventana (1.7334 s de audio)
        per_win = [dict(win=w.get("win"), t_ready=w.get("t_ready"), gmr_s=w.get("gmr_s"),
                        sonic_s=w.get("sonic_s"), frames=w.get("frames")) for w in wins]
        row["windows"] = per_win
        # calidad vs precomputado (raw: sin k ni limiter → comparable a sonic_actual)
        raw = []
        for t, arms, waist, head in _raw_replay(ms, pcm, sr):
            raw.append(np.concatenate([arms, waist, head]))
        R = np.asarray(raw, np.float32)
        np.savez(out / f"{turn}_online_raw.npz", arms=R[:, :14], waist=R[:, 14:17], head=R[:, 17:19])
        if ref_npz.exists():
            z = np.load(ref_npz, allow_pickle=True)
            ref = np.asarray(z["sonic_actual"], np.float32)
            names = [str(x) for x in z["names"]] if "names" in z else None
            assert names is None or names == list(_arm_names()), f"orden de brazos distinto: {names}"
            r_arm, s_arm = rmse_aligned(R[:, :14], ref)
            r_wh, _ = rmse_aligned(R[:, 14:19], np.asarray(z["waist_head"], np.float32)) \
                if "waist_head" in z else (float("nan"), 0)
            row.update(ref=str(ref_npz), arm_rmse_rad=r_arm, arm_best_shift=s_arm,
                       wh_rmse_rad=r_wh, gate="< 0.20 (seed)", pass_gate=bool(r_arm < 0.20))
        rows.append(row)
        print(json.dumps({k: v for k, v in row.items() if k != "windows"}, default=float), flush=True)
    ms.stop()
    json.dump(rows, open(out / "bench.json", "w"), indent=1, default=float)
    print("WROTE", out / "bench.json")


def _arm_names():
    from x2_ultra.hri_runtime.motion_online.limits import ARM_JOINTS
    return tuple(ARM_JOINTS)


def _raw_replay(ms, pcm, sr):
    """Segundo pase del mismo turno con k=1 y sin limiter (comparable al npz precomputado)."""
    from x2_ultra.hri_runtime.motion_online.client import MotionServer
    saved = (ms.apply_limits, ms.arm_amp_k)
    ms.apply_limits, ms.arm_amp_k = False, 1.0
    try:
        yield from ms.start_turn(pcm, sr, 0.0)
    finally:
        ms.apply_limits, ms.arm_amp_k = saved


if __name__ == "__main__":
    main()
