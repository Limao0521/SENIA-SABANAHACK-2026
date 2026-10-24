#!/usr/bin/env python3
"""Adapted O3 replay driver for the motor_fix validation (host-side).
Same wire protocol as opt/vab/v2_o3_replay.py, plus:
 - handles op=fall (motor fall interlock): stop the replay, dump partial results, exit code 2;
 - handles op=turn_refused: record it as terminal for that turn and continue;
 - records anchor_start_s / T_a0 per turn for the stroke-anchor metric;
 - data (manifest, motion npz, preset latency) stays in opt/vab; outputs go to opt/motor_fix/out."""
from __future__ import annotations

import argparse
import json
import socket
import sys
import threading
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
VAB = HERE.parent / "vab"
sys.path.insert(0, str(HERE))
from o3common_fix import SOCK, send_chunk, send_json, recv_frame, start_offset  # noqa: E402

PREFIX_N_NEW = 27
PREFIX_N_ORIGINAL = 0
LAT_PATH = HERE.parents[1] / "twin/replay/presets_c08/preset_latency.json"


def manifest_rows(path: Path, reps: int, only: set[str]) -> list[dict]:
    all_rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    selected = []
    for rep in range(1, reps + 1):
        selected.extend({**r, "rep": rep} for r in all_rows if int(r.get("rep", 1)) == 1 and (not only or r["turn_id"] in only))
    return selected


def make_preset(r: dict, lat: dict) -> dict:
    key = f"{r['motion_id']}/{r['area']}"
    b = lat["bindings"][key]
    anchor = float(r["anchor_start_s"])
    onset = float(b["lead_onset_s"]["median"])
    stroke = float(b["lead_stroke_s"]["median"])
    return {"motion": int(r["motion_id"]), "area": int(r["area"]), "emote": r["emote"],
        "max_initial_delay_s": 1.5,
        "policy_dispatch_s": {"stroke": anchor - stroke, "mid": anchor - (onset + stroke) / 2, "onset": anchor - onset}}


def motion_array(r: dict, source: str) -> np.ndarray:
    name = r["turn_id"]
    if source == "new":
        z = np.load(VAB / "out/v2_motion/new" / f"{name}.npz")
        arr = np.concatenate([z["arms"], z["wh"]], axis=1)
    else:
        z = np.load(VAB / "out/v2_motion/original" / name / f"{name}_arms.npz")
        arr = np.concatenate([z["cmd"][100:], z["waist_head"][100:]], axis=1)
    return np.ascontiguousarray(arr, dtype=np.float32)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--tag", default="o3fix")
    ap.add_argument("--source", choices=("new", "original"), default="new")
    ap.add_argument("--only", default="")
    ap.add_argument("--guard-s", type=float, default=0.25)
    ap.add_argument("--gap-s", type=float, default=0.3)
    ap.add_argument("--ready-timeout-s", type=float, default=15.0)
    a = ap.parse_args()
    rows = manifest_rows(VAB / "out/v2_subset_reps.jsonl", a.reps, set(filter(None, a.only.split(","))))
    lat = json.loads(LAT_PATH.read_text())
    events: list[dict] = []
    ev_lock = threading.Lock()
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    t_c = time.monotonic()          # the motor unlinks/rebinds the socket at startup: retry through the stale-socket window
    while True:
        try:
            s.connect(SOCK); break
        except (ConnectionRefusedError, FileNotFoundError, OSError):
            if time.monotonic() - t_c > 30.0: raise
            time.sleep(0.5); s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)

    def read_events() -> None:
        try:
            while True:
                typ, payload = recv_frame(s)
                if typ == 1:
                    payload["t_host"] = time.monotonic()
                    with ev_lock:
                        events.append(payload)
        except (ConnectionError, OSError):
            return

    threading.Thread(target=read_events, daemon=True).start()
    deadline = time.monotonic() + a.ready_timeout_s
    while time.monotonic() < deadline:
        with ev_lock:
            ready = any(e.get("op") == "ready" for e in events)
        if ready:
            break
        time.sleep(0.05)
    else:
        print(json.dumps({"event": "ready_unconfirmed", "waited_s": a.ready_timeout_s}), flush=True)

    turns = []
    fell = False
    out = HERE / "out" / a.tag
    out.mkdir(parents=True, exist_ok=True)
    try:
        for idx, r in enumerate(rows):
            preset = make_preset(r, lat) if r["anchor_status"] == "planned" else None
            point = None
            if r["anchor_status"] == "anchor_only_no_preset":
                point = {"direction": r["direction"], "anchor_start_s": float(r["anchor_start_s"]),
                         "prep_s": 0.8, "hold_s": 1.5, "recovery_s": 1.65, "lead_s": 0.12, "head_amp": 0.30}
            arr = motion_array(r, a.source)
            prefix_n = PREFIX_N_NEW if a.source == "new" else PREFIX_N_ORIGINAL
            now = time.monotonic()
            lead = -start_offset(prefix_n, preset)
            t_a0 = now + lead + a.guard_s
            kind = "preset" if preset is not None else "sonic"
            send_json(s, {"op": "turn", "turn": idx, "kind": kind,
                "name": f"{r['turn_id']}_r{r['rep']}", "visit": r["visit_id"], "rep": r["rep"],
                "prefix_n": prefix_n, "T_a0": t_a0, "dur_s": float(r["duration_s"]),
                "preset": preset, "point": point, "wav_sha256": r["wav_sha256"],
                "requested_emote": r["requested"], "contract_valid": r["contract_valid"],
                "anchor_start_s": float(r.get("anchor_start_s") or 0.0)})
            send_chunk(s, idx, 0, arr, time.monotonic())
            send_json(s, {"op": "turn_end", "turn": idx, "n_ticks": len(arr)})
            started = time.monotonic()
            turn_done = None
            refused = None
            while time.monotonic() - started < max(120.0, float(r["duration_s"]) + 30.0):
                with ev_lock:
                    turn_done = next((e for e in reversed(events) if e.get("op") == "turn_done" and e.get("turn") == idx), None)
                    refused = next((e for e in reversed(events) if e.get("op") == "turn_refused" and e.get("turn") == idx), None)
                    fell = fell or any(e.get("op") == "fall" for e in events)
                if turn_done or refused or fell:
                    break
                time.sleep(0.05)
            if fell:
                print(json.dumps({"event": "fall_interlock", "turn": idx, "turn_id": r["turn_id"]}), flush=True)
                break
            if not turn_done and not refused:
                send_json(s, {"op": "quit"})
                raise TimeoutError(f"turn {idx} ({r['turn_id']}) did not finish")
            turns.append({"idx": idx, "turn_id": r["turn_id"], "visit_id": r["visit_id"], "rep": r["rep"],
                "kind": kind, "emote": r["emote"], "direction": r["direction"], "wav_sha256": r["wav_sha256"],
                "contract_valid": r["contract_valid"], "audio_start_after_send_s": t_a0 - now,
                "motion_source": a.source, "motion_ticks": len(arr),
                "T_a0": t_a0, "anchor_start_s": float(r.get("anchor_start_s") or 0.0),
                "preset": preset,
                "outcome": "refused" if (refused and not turn_done) else "done",
                "refused_reason": (refused or {}).get("reason"),
                "turn_done": turn_done})
            print(json.dumps({k: v for k, v in turns[-1].items() if k not in ("turn_done", "preset")}, ensure_ascii=False), flush=True)
            time.sleep(a.gap_s)
        send_json(s, {"op": "quit"})
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            with ev_lock:
                if any(e.get("op") == "bye" for e in events):
                    break
            time.sleep(0.1)
    finally:
        (out / "sender.json").write_text(json.dumps({"args": vars(a), "turns": turns, "fell": fell,
            "events": events, "count": len(turns)}, indent=2, ensure_ascii=False), encoding="utf-8")
    s.close()
    if fell:
        raise SystemExit(2)
    if len(turns) != len(rows):
        raise SystemExit(f"replayed {len(turns)} of {len(rows)} turns")
    print(json.dumps({"event": "complete", "turns": len(turns), "events": len(events)}), flush=True)


if __name__ == "__main__":
    main()
