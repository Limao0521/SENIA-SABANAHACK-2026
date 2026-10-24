#!/usr/bin/env python3
"""Convert X2 GMR references to SONIC motion files and run headless MuJoCo.

The rollout is diagnostic: even if a GMR reference fails a kinematic QC gate,
it can be simulated here so the experiment can measure the consequence. The
decision summary preserves the upstream failure and never upgrades it to PASS.
"""
from __future__ import annotations

import argparse
import contextlib
import csv
import fcntl
import json
import os
import pickle
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Select the headless renderer before importing MuJoCo; the platform is chosen at import time.
os.environ.setdefault("MUJOCO_GL", "egl")

import mujoco
import numpy as np
from scipy.spatial.transform import Rotation, Slerp

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # HRIEGO_gesture root -> paths.py
import paths as _hp  # noqa: E402
X2_ROOT = Path(os.environ.get("X2_MOTION_ROOT") or Path.cwd() / "x2")  # CLI default; workers/bench pass explicit dirs
RETARGET = X2_ROOT / "retarget"
SONIC_OUT = X2_ROOT / "sonic"
LOGS = X2_ROOT / "logs"
X2_EXTERNAL_ROOT = _hp.x2_external_root()  # $X2_EXTERNAL_ROOT

SONIC_ROOT = X2_EXTERNAL_ROOT / "sonic_x2_c9959443"
RETARGETER = X2_EXTERNAL_ROOT / "agibot_x2_retargeter_bb582d97" / "retarget"
SONIC_SCRIPTS = SONIC_ROOT / "scripts"
MODELS = ("B_right_v1_s0", "B_right_v1_s1", "B_right_v2_s0", "B_right_v3_s0", "C1_right_v1_s0", "C2_right_v1_s0", "C2_right_v1_s1", "C2_right_v1_s2", "C2_right_v1_s3", "C2_right_v1_s4", "B_left_v1_s0", "B_left_v1_s1", "B_left_v2_s0", "B_left_v3_s0", "C2_left_v1_s0", "B_front_v1_s0", "B_front_v1_s1", "B_front_v2_s0", "B_front_v3_s0", "C2_front_v1_s0", "B_none_v1_s0", "A_native_right_v1_s0", "D_cfg1_native_right_v1_s0", "HYB_right_v1_s0", "HYB_left_v1_s0", "HYB_front_v1_s0", "HYB_IK_right_v1_s0", "HYB_IK_left_v1_s0", "HYB_IK_front_v1_s0", "HYB_IK_right_v1_s1", "HYB_IK_left_v1_s1", "HYB_IK_front_v1_s1", "HYB_IK_right_v2_s0", "HYB_IK_right_v3_s0", "HYB_IK_left_v2_s0", "HYB_IK_left_v3_s0", "HYB_IK_front_v2_s0", "HYB_IK_front_v3_s0", "HYB_SEQ_RLF_v1_s0", "HYB_SEQ_RLF_v1_s0_vlim9", "HYB_IK_right_v1_s0_vlim9", "HYB_IK_left_v1_s0_vlim9", "HYB_IK_front_v1_s0_vlim9")
MODELS += tuple(f"HYB_IK_{direction}_v1_s{seed}"
                for direction in ("right", "left", "front") for seed in (2, 3, 4))
SOURCE_FPS = 30.0
SONIC_FPS = 50.0
CAMERA_AZIMUTH = 150.0
CAMERA_ELEVATION = -22.0


class Tee:
    """Mirror runner output to the terminal and a per-model log file."""

    def __init__(self, *streams):
        self.streams = streams

    def write(self, value: str) -> int:
        for stream in self.streams:
            stream.write(value)
            stream.flush()
        return len(value)

    def flush(self) -> None:
        for stream in self.streams:
            stream.flush()

    def isatty(self) -> bool:
        return any(getattr(stream, "isatty", lambda: False)() for stream in self.streams)


@contextlib.contextmanager
def workspace_lock(job: str):
    LOGS.mkdir(parents=True, exist_ok=True)
    lock_path = LOGS / ".x2_heavy_job.lock"
    with lock_path.open("a+") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            lock.seek(0)
            owner = lock.read().strip() or "another X2 job"
            raise RuntimeError(f"local X2 lock held ({owner}): {lock_path}") from exc
        lock.seek(0)
        lock.truncate()
        lock.write(f"pid={os.getpid()} job={job}\n")
        lock.flush()
        try:
            yield
        finally:
            lock.seek(0)
            lock.truncate()
            lock.flush()


def resample(q: np.ndarray, src_fps: float, fps: float = SONIC_FPS) -> np.ndarray:
    t0 = np.arange(len(q), dtype=np.float64) / src_fps
    t1 = np.arange(int(round((len(q) - 1) / src_fps * fps)) + 1) / fps
    t1 = np.minimum(t1, t0[-1])
    result = np.stack([np.interp(t1, t0, q[:, k]) for k in range(q.shape[1])], axis=1)
    rotation = Rotation.from_quat(q[:, 3:7], scalar_first=True)
    result[:, 3:7] = Slerp(t0, rotation)(t1).as_quat(scalar_first=True)
    return result


def convert_one(model_name: str, force: bool, ramp_seconds: float = 1.5) -> dict:
    sys.path.insert(0, str(SONIC_SCRIPTS))
    sys.path.insert(0, str(RETARGETER))
    from eval_x2_mujoco import DEFAULT_DOF, MJCF_PATH, MUJOCO_JOINT_NAMES
    from retarget_quality import foot_collision_geoms, sole_height

    source = RETARGET / f"{model_name}_x2_gmr_full.npz"
    decision_path = RETARGET / f"{model_name}_DECISION_SUMMARY.json"
    output = SONIC_OUT / f"{model_name}_full_ramp_sonic.pkl"
    audit_path = SONIC_OUT / f"{model_name}_full_ramp_sonic.audit.json"
    for path in (source, decision_path):
        if not path.is_file():
            raise FileNotFoundError(path)
    if not force and (output.exists() or audit_path.exists()):
        raise FileExistsError(f"SONIC conversion output exists for {model_name}; pass --force")
    decision = json.loads(decision_path.read_text())
    with np.load(source, allow_pickle=False) as archive:
        q = np.asarray(archive["qpos"], dtype=np.float64)
        names = archive["joint_names"].tolist()
        src_fps = float(archive["fps"])
    if q.ndim != 2 or q.shape[1] != 38 or len(names) != 31:
        raise ValueError(f"{model_name}: X2 qpos ABI mismatch: {q.shape}, {len(names)} joints")
    if names != MUJOCO_JOINT_NAMES:
        raise ValueError(f"{model_name}: GMR and SONIC X2 joint orders differ")
    model = mujoco.MjModel.from_xml_path(MJCF_PATH)
    if (model.nq, model.nv, model.nu) != (38, 37, 31):
        raise ValueError(f"unexpected SONIC model shape {(model.nq, model.nv, model.nu)}")
    data = mujoco.MjData(model)
    feet = foot_collision_geoms(model, mujoco)
    q50 = resample(q, src_fps)
    start = q50[0].copy()
    start[7:] = DEFAULT_DOF
    data.qpos[:] = start
    mujoco.mj_forward(model, data)
    start[2] -= sole_height(model, data, feet)
    hold_n = 25
    blend_n = int(round(ramp_seconds * SONIC_FPS))
    if blend_n < 1:
        raise ValueError(f"ramp_seconds must be positive, got {ramp_seconds}")
    hold = np.repeat(start[None], hold_n, axis=0)
    u = np.linspace(0.0, 1.0, blend_n + 1)[1:]
    weight = 3 * u**2 - 2 * u**3
    blend = start[None] + weight[:, None] * (q50[0] - start)[None]
    blend[:, 3:7] = q50[0, 3:7]
    all_q = np.concatenate([hold, blend, q50], axis=0)
    if not np.isfinite(all_q).all():
        raise ValueError(f"{model_name}: non-finite reference before SONIC")
    soles = np.empty(len(all_q), dtype=np.float64)
    for idx, row in enumerate(all_q):
        data.qpos[:] = row
        mujoco.mj_forward(model, data)
        soles[idx] = sole_height(model, data, feet)
    if soles.min() < -0.06:
        raise ValueError(f"{model_name}: SONIC ramp sole penetration {soles.min():.3f} m")

    payload = {
        model_name: {
            "fps": SONIC_FPS,
            "dof": all_q[:, 7:].astype(np.float32),
            "root_trans_offset": all_q[:, :3].astype(np.float32),
            "root_rot": all_q[:, [4, 5, 6, 3]].astype(np.float32),
        }
    }
    SONIC_OUT.mkdir(parents=True, exist_ok=True)
    with output.open("wb") as stream:
        pickle.dump(payload, stream, protocol=4)
    result = {
        "schema": "WAYNE069_X2_SONIC_MOTION_ABI_V1",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "model": model_name,
        "status": "PASS" if decision["status"] == "PASS" else "DIAGNOSTIC_ONLY",
        "gmr_qc_status": decision["status"],
        "gmr_qc_fail_reason": decision["qc"].get("fail"),
        "source_qpos": str(source),
        "source_frames": int(len(q)),
        "source_fps": src_fps,
        "sonic_frames_including_transition": int(len(all_q)),
        "sonic_fps": SONIC_FPS,
        "transition_hold_frames": hold_n,
        "transition_ramp_frames": blend_n,
        "transition_ramp_seconds": blend_n / SONIC_FPS,
        "source_motion_start_frame": hold_n + blend_n,
        "source_motion_start_time_s": (hold_n + blend_n) / SONIC_FPS,
        "joint_order_exact": True,
        "sonic_model_nq_nv_nu": [model.nq, model.nv, model.nu],
        "min_sole_height_m": float(soles.min()),
        "max_joint_step_rad": float(np.max(np.abs(np.diff(all_q[:, 7:], axis=0)))),
        "hands": "not represented; X2 motion ABI contains no finger DOFs",
        "motion_pkl": str(output),
    }
    audit_path.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    return result


def _trace_file(phase: str, model_name: str) -> Path | None:
    # Always capture the controller trace. Environment overrides are useful for
    # alternate output roots, but an unset variable must not silently skip it.
    trace_dir = Path(os.environ.get("X2_TRACE_DIR", X2_ROOT / "traces" / "E3_PLACE_GUIDE"))
    tag = os.environ.get("X2_TRACE_TAG", f"E3_{model_name}")
    path = Path(trace_dir) / f"{phase}_{tag}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _install_trace(runner, motion_path: Path, init_frame: int, trace_path: Path | None):
    """Capture each SONIC control tick without editing the pinned evaluator."""
    if trace_path is None:
        return None
    with motion_path.open("rb") as stream:
        motion = pickle.load(stream)
    clip = motion[list(motion.keys())[0]]
    q_des = np.asarray(clip["dof"], dtype=np.float64)
    root_des = np.asarray(clip["root_trans_offset"], dtype=np.float64)
    fps = float(clip["fps"])
    decimation = int(runner.DECIMATION)
    control_dt = float(runner.CONTROL_DT)
    original_step = runner.mujoco.mj_step
    state = {"calls": 0, "rows": []}
    arm_body_ids = {}

    def capture_step(model, data):
        result = original_step(model, data)
        state["calls"] += 1
        if state["calls"] % decimation:
            return result
        tick = state["calls"] // decimation - 1
        reference_time = init_frame / fps + tick * control_dt
        frame = int(reference_time * fps)
        if not 0 <= frame < len(q_des):
            return result
        root_quat = np.asarray(data.qpos[3:7], dtype=np.float64)
        from scipy.spatial.transform import Rotation
        gravity_body = Rotation.from_quat(root_quat, scalar_first=True).inv().apply(
            np.array([0.0, 0.0, -1.0]))
        tilt = float(np.rad2deg(np.arccos(np.clip(-gravity_body[2], -1.0, 1.0))))
        actual = np.asarray(data.qpos[7:7 + len(q_des[frame])], dtype=np.float64).copy()
        velocity = np.asarray(data.qvel[6:6 + len(actual)], dtype=np.float64).copy()
        desired = q_des[frame]
        row = {
            "frame": frame,
            "time_s": frame / fps,
            "pelvis_z_des_m": float(root_des[frame, 2]),
            "pelvis_z_actual_m": float(data.qpos[2]),
            "tilt_deg": tilt,
            "root_quat_w": float(root_quat[0]), "root_quat_x": float(root_quat[1]),
            "root_quat_y": float(root_quat[2]), "root_quat_z": float(root_quat[3]),
        }
        contacts = [data.contact[i] for i in range(int(data.ncon))]
        distances = [float(c.dist) for c in contacts]
        deep_pairs = set()
        for c in contacts:
            if float(c.dist) < -0.015:
                a = runner.mujoco.mj_id2name(model, runner.mujoco.mjtObj.mjOBJ_GEOM, int(c.geom1)) or str(c.geom1)
                b = runner.mujoco.mj_id2name(model, runner.mujoco.mjtObj.mjOBJ_GEOM, int(c.geom2)) or str(c.geom2)
                deep_pairs.add("|".join(sorted((a, b))))
        row.update({"contact_count": int(data.ncon),
                    "min_contact_dist_m": min(distances) if distances else None,
                    "deep_contact_pairs_below_minus_15mm": json.dumps(sorted(deep_pairs))})
        for side in ("left", "right"):
            if side not in arm_body_ids:
                arm_body_ids[side] = tuple(runner.mujoco.mj_name2id(
                    model, runner.mujoco.mjtObj.mjOBJ_BODY, f"{side}_{part}_link")
                    for part in ("shoulder_pitch", "elbow", "wrist_roll"))
            shoulder_id, elbow_id, wrist_id = arm_body_ids[side]
            shoulder, elbow, wrist = (np.asarray(data.xpos[idx], dtype=np.float64).copy()
                                      for idx in (shoulder_id, elbow_id, wrist_id))
            arm_world = wrist - shoulder
            arm_local = Rotation.from_quat(root_quat, scalar_first=True).inv().apply(arm_world)
            arm_norm = max(float(np.linalg.norm(arm_local)), 1e-8)
            chain_length = (np.linalg.norm(elbow - shoulder) + np.linalg.norm(wrist - elbow))
            extension = float(np.linalg.norm(arm_world) / max(float(chain_length), 1e-8))
            row.update({
                f"{side}_arm_local_x_m": float(arm_local[0]),
                f"{side}_arm_local_y_m": float(arm_local[1]),
                f"{side}_arm_local_z_m": float(arm_local[2]),
                f"{side}_arm_extension": extension,
                f"{side}_arm_cos_right": float(arm_local[1] * -1.0 / arm_norm),
                f"{side}_arm_cos_left": float(arm_local[1] / arm_norm),
                f"{side}_arm_cos_front": float(arm_local[0] / arm_norm),
            })
        names = getattr(runner, "MUJOCO_JOINT_NAMES", [f"dof_{i:02d}" for i in range(len(actual))])
        for i, name in enumerate(names[:len(actual)]):
            row[f"{name}_desired_rad"] = float(desired[i])
            row[f"{name}_actual_rad"] = float(actual[i])
            row[f"{name}_actual_velocity_rad_s"] = float(velocity[i])
        state["rows"].append(row)
        return result

    runner.mujoco.mj_step = capture_step

    def restore_and_write():
        runner.mujoco.mj_step = original_step
        rows = state["rows"]
        if not rows:
            print(f"  [trace] no control ticks captured; rollout failed before simulation: {trace_path}", flush=True)
            return
        with trace_path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        print(f"  [trace] wrote {len(rows)} control ticks -> {trace_path}", flush=True)

    return restore_and_write


def rollout_one(model_name: str, force: bool, total_seconds: float | None,
                record: bool = True) -> dict:
    wall_started = time.perf_counter()
    motion_path = SONIC_OUT / f"{model_name}_full_ramp_sonic.pkl"
    video_path = SONIC_OUT / f"{model_name}_x2_sonic_rollout.mp4"
    if not motion_path.is_file():
        raise FileNotFoundError(motion_path)
    if record and video_path.exists() and not force:
        raise FileExistsError(f"rollout exists: {video_path}; pass --force")
    sys.path.insert(0, str(SONIC_SCRIPTS))
    import eval_x2_mujoco_onnx as runner

    original_recorder = runner.VideoRecorder

    class FixedAngleRecorder(original_recorder):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.cam.azimuth = CAMERA_AZIMUTH
            self.cam.elevation = CAMERA_ELEVATION

    runner.VideoRecorder = FixedAngleRecorder
    if total_seconds is None:
        with motion_path.open("rb") as stream:
            motion_data = pickle.load(stream)
        clip_data = motion_data[model_name]
        total_seconds = float(len(clip_data["dof"]) / clip_data["fps"])
    args = [
        "--onnx", str(SONIC_ROOT / "models/x2_sonic_14000_g1.onnx"),
        "--motion", str(motion_path),
        "--no-viewer",
        "--max-episode", f"{total_seconds:.2f}" if total_seconds is not None else "0",
        "--init-frame", "0",
    ]
    if record:
        args.extend(["--record", str(video_path)])
    sys.argv = [str(SONIC_SCRIPTS / "eval_x2_mujoco_onnx.py"), *args]
    os.environ.setdefault("MUJOCO_GL", "egl")
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ.setdefault("MKL_NUM_THREADS", "1")
    os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
    os.environ.setdefault("ORT_DISABLE_TELEMETRY", "1")
    with workspace_lock(f"SONIC-X2-{model_name}"):
        log_path = LOGS / f"{model_name}_x2_sonic.log"
        with log_path.open("w", encoding="utf-8") as log:
            tee = Tee(sys.stdout, log)
            trace_done = _install_trace(runner, motion_path, 0,
                                        _trace_file("full", model_name))
            try:
                with contextlib.redirect_stdout(tee), contextlib.redirect_stderr(tee):
                    runner.main()
            finally:
                if trace_done:
                    trace_done()
    wall_seconds = time.perf_counter() - wall_started
    return {"model": model_name, "recording": str(video_path) if record else None,
            "log": str(log_path), "simulated_seconds": total_seconds,
            "wall_seconds_including_model_init_and_recording": wall_seconds,
            "runtime_factor_wall_over_sim": wall_seconds / total_seconds if total_seconds else None}


def source_window_one(model_name: str, force: bool) -> dict:
    """Measure controller tracking over motion frames only, excluding warm-up."""
    motion_path = SONIC_OUT / f"{model_name}_full_ramp_sonic.pkl"
    audit_path = SONIC_OUT / f"{model_name}_full_ramp_sonic.audit.json"
    log_path = LOGS / f"{model_name}_x2_sonic_source_window.log"
    if not motion_path.is_file() or not audit_path.is_file():
        raise FileNotFoundError(f"missing SONIC reference/audit for {model_name}")
    if log_path.exists() and not force:
        raise FileExistsError(f"source-window log exists: {log_path}; pass --force")
    audit = json.loads(audit_path.read_text())
    init_frame = int(audit["source_motion_start_frame"])
    fps = float(audit["sonic_fps"])
    source_frames = int(audit["sonic_frames_including_transition"]) - init_frame
    source_seconds = source_frames / fps
    sys.path.insert(0, str(SONIC_SCRIPTS))
    import eval_x2_mujoco_onnx as runner

    sys.argv = [
        str(SONIC_SCRIPTS / "eval_x2_mujoco_onnx.py"),
        "--onnx", str(SONIC_ROOT / "models/x2_sonic_14000_g1.onnx"),
        "--motion", str(motion_path),
        "--no-viewer",
        "--max-episode", f"{source_seconds:.2f}",
        "--init-frame", str(init_frame),
    ]
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ.setdefault("MKL_NUM_THREADS", "1")
    os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
    os.environ.setdefault("ORT_DISABLE_TELEMETRY", "1")
    with workspace_lock(f"SONIC-X2-SOURCE-WINDOW-{model_name}"):
        with log_path.open("w", encoding="utf-8") as log:
            tee = Tee(sys.stdout, log)
            trace_done = _install_trace(runner, motion_path, init_frame,
                                        _trace_file("source_window", model_name))
            try:
                with contextlib.redirect_stdout(tee), contextlib.redirect_stderr(tee):
                    runner.main()
            finally:
                if trace_done:
                    trace_done()
    return {"model": model_name, "source_window_s": source_seconds, "log": str(log_path)}


def main() -> None:
    global RETARGET, SONIC_OUT, LOGS
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("convert", "rollout", "source-window"))
    parser.add_argument("--model", action="append",
                        help="run selected model(s); arbitrary run-local model IDs are accepted when their source files exist")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--no-record", action="store_true",
                        help="skip MP4 recording while still running and tracing the rollout")
    parser.add_argument("--retarget-dir", type=Path, default=RETARGET,
                        help="source GMR directory; supports isolated solver comparisons")
    parser.add_argument("--sonic-out-dir", type=Path, default=SONIC_OUT,
                        help="separate SONIC motions, audits, and videos for this run")
    parser.add_argument("--log-dir", type=Path, default=LOGS,
                        help="separate per-model rollout logs and workspace lock")
    parser.add_argument("--trace-dir", type=Path, default=None,
                        help="optional separate MuJoCo control-trace directory")
    parser.add_argument("--total-seconds", type=float,
                        help="optional cap for debugging; normal rollouts end at --max-episode")
    args = parser.parse_args()
    if args.model and any(not name or not all(c.isalnum() or c in "_-" for c in name) for name in args.model):
        parser.error("model IDs may contain only letters, digits, underscores, and hyphens")
    RETARGET = args.retarget_dir.resolve()
    SONIC_OUT = args.sonic_out_dir.resolve()
    LOGS = args.log_dir.resolve()
    if args.trace_dir is not None:
        os.environ["X2_TRACE_DIR"] = str(args.trace_dir.resolve())
    models = tuple(dict.fromkeys(args.model or MODELS))
    SONIC_OUT.mkdir(parents=True, exist_ok=True)
    if args.phase == "convert":
        results = [convert_one(name, args.force) for name in models]
    elif args.phase == "rollout":
        results = [rollout_one(name, args.force, args.total_seconds, record=not args.no_record) for name in models]
    else:
        results = [source_window_one(name, args.force) for name in models]
    print(json.dumps({"phase": args.phase, "models": list(models), "results": results}, indent=2))


if __name__ == "__main__":
    main()
