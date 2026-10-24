#!/usr/bin/env python3
"""Retarget common-window audio-only SMPL-X streams to the AgiBot X2.

Uses the pinned GMR/Laplace IK model and audited toe-proxy MJCF from the
existing local X2 evidence tree. New inputs, qpos, logs and decisions stay in
the Gesture experiment directory. No G1 q_actual state enters this branch.
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import gc
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # HRIEGO_gesture root -> paths.py
import paths as _hp  # noqa: E402
X2_ROOT = Path(os.environ.get("X2_MOTION_ROOT") or Path.cwd() / "x2")  # CLI default; workers/bench pass explicit dirs
INPUTS = X2_ROOT / "inputs"
OUT = X2_ROOT / "retarget"
LOGS = X2_ROOT / "logs"
X2_EXTERNAL_ROOT = _hp.x2_external_root()  # $X2_EXTERNAL_ROOT
X2_IK_DIR = _hp.x2_ik_dir()  # $X2_IK_DIR

GMR_ROOT = X2_EXTERNAL_ROOT / "GMR"
RETARGETER = X2_EXTERNAL_ROOT / "agibot_x2_retargeter_bb582d97" / "retarget"
V2M02 = X2_IK_DIR
IK_XML = V2M02 / "retarget/model/x2_mocap_toe_proxy.xml"
IK_AUDIT = V2M02 / "retarget/model/V2M_02_X2_TOE_PROXY_AUDIT.json"
CANONICAL_HEIGHT_M = 1.8
FPS = 30.0
MODELS = ("B_right_v1_s0", "B_right_v1_s1", "B_right_v1_s2", "B_right_v1_s3", "B_right_v1_s4", "B_right_v2_s0", "B_right_v3_s0", "C1_right_v1_s0", "C2_right_v1_s0", "C2_right_v1_s1", "C2_right_v1_s2", "C2_right_v1_s3", "C2_right_v1_s4", "B_left_v1_s0", "B_left_v1_s1", "B_left_v1_s2", "B_left_v1_s3", "B_left_v1_s4", "B_left_v2_s0", "B_left_v3_s0", "C2_left_v1_s0", "B_front_v1_s0", "B_front_v1_s1", "B_front_v1_s2", "B_front_v1_s3", "B_front_v1_s4", "B_front_v2_s0", "B_front_v3_s0", "C2_front_v1_s0", "B_none_v1_s0", "A_native_right_v1_s0", "D_cfg1_native_right_v1_s0", "MOTOR_anchor_RIGHT", "MOTOR_anchor_LEFT", "MOTOR_anchor_FRONT")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def constrain_process() -> int:
    limit = max(1, int(os.environ.get("V2M_CPU_LIMIT", "2")))
    for name in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ[name] = str(limit)
    if hasattr(os, "sched_getaffinity") and hasattr(os, "sched_setaffinity"):
        available = sorted(os.sched_getaffinity(0))
        os.sched_setaffinity(0, set(available[:limit]))
    return limit


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


def verify_assets() -> dict:
    for path in (GMR_ROOT, RETARGETER, IK_XML, IK_AUDIT):
        if not path.exists():
            raise FileNotFoundError(path)
    audit = json.loads(IK_AUDIT.read_text())
    # The audit records the path where it was produced; compare the file name here and
    # rely on derived_sha256 below for content integrity (the root may differ per machine).
    if audit.get("status") != "PASS" or Path(str(audit.get("derived", ""))).name != IK_XML.name:
        raise ValueError(f"X2 toe-proxy audit is not passing: {IK_AUDIT}")
    if audit.get("derived_sha256") != sha256(IK_XML):
        raise ValueError("audited X2 IK MJCF hash no longer matches its audit")
    return audit


def retarget_one(model_name: str, batch, frames_fn, cpu_limit: int, audit: dict,
                 input_dir: Path = INPUTS, output_dir: Path = OUT,
                 solver_mode: str = "native_qp") -> dict:
    src = input_dir / f"{model_name}_gmr_stageii.npz"
    if not src.is_file():
        raise FileNotFoundError(src)
    with np.load(src, allow_pickle=False) as z:
        n = int(z["pose_body"].shape[0])
        src_fps = float(z["mocap_frame_rate"])
    if not np.isclose(src_fps, FPS):
        raise ValueError(f"{model_name}: expected {FPS:g} fps, got {src_fps:g}")

    print(f"[{model_name}] loading canonical SMPL-X ({n} frames)", flush=True)
    smplx_data, body_model, smplx_output, human_height = batch._load_canonical(str(src), 0, n)
    human_frames, fps = frames_fn(smplx_data, body_model, smplx_output, tgt_fps=FPS)
    print(f"[{model_name}] GMR X2 IK starting", flush=True)
    qpos = batch.retarget_frames(human_frames, fps, human_height)
    qpos, ground_offset = batch.ground_track(qpos, fps, max_rate=0.6)
    qc = batch.qc_checks(qpos, fps)
    mujoco, mj_model = batch._worker["mujoco"], batch._worker["model"]
    names = [mujoco.mj_id2name(mj_model, mujoco.mjtObj.mjOBJ_JOINT, j)
             for j in range(mj_model.njnt)
             if mj_model.jnt_type[j] == mujoco.mjtJoint.mjJNT_HINGE]
    status = "PASS" if qc.get("finite") and qc.get("fail") is None else "FAIL"
    ref_path = output_dir / f"{model_name}_x2_gmr_full.npz"
    output_dir.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        ref_path,
        qpos=qpos.astype(np.float32),
        joint_names=np.asarray(names),
        fps=np.asarray(fps, dtype=np.float32),
        source_frame_start=np.asarray(0, dtype=np.int32),
        source_frame_end=np.asarray(n, dtype=np.int32),
        canonical_height_m=np.asarray(CANONICAL_HEIGHT_M, dtype=np.float32),
        ground_track_mean_offset_m=np.asarray(ground_offset, dtype=np.float32),
        source_model=np.asarray(model_name),
    )
    decision = {
        "schema": "WAYNE069_X2_GMR_FULL_DECISION_V1",
        "model": model_name,
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "source_stageii": str(src),
        "source_stageii_sha256": sha256(src),
        "source_frames": n,
        "fps": float(fps),
        "duration_s": float(n / fps),
        "endpoint_time_s": float((n - 1) / fps),
        "canonical_human_height_m": CANONICAL_HEIGHT_M,
        "human_height_before_canonicalization_m": float(human_height),
        "coordinate_map": "GMR get_gvhmr_data_offline_fast, SMPL-X Y-up to MuJoCo Z-up (+90 deg X)",
        "ik_model": str(IK_XML),
        "solver_mode": solver_mode,
        "ik_model_sha256": audit["derived_sha256"],
        "ik_audit_status": audit["status"],
        "hands": "omitted; SMPL-X face/hand pose not passed to the X2 ABI",
        "ground_track_max_rate_m_s": 0.6,
        "ground_track_mean_offset_m": float(ground_offset),
        "cpu_limit": cpu_limit,
        "reference_npz": str(ref_path),
        "reference_sha256": sha256(ref_path),
        "qc": qc,
    }
    decision_path = output_dir / f"{model_name}_DECISION_SUMMARY.json"
    decision_path.write_text(json.dumps(decision, indent=2) + "\n")
    print(json.dumps({"model": model_name, "status": status, "qc": qc,
                      "reference": str(ref_path)}, indent=2), flush=True)

    del smplx_data, body_model, smplx_output, human_frames, qpos
    gc.collect()
    return decision


def write_batch_summary(output_dir: Path = OUT,
                        expected_models: tuple[str, ...] | None = MODELS) -> dict:
    records = []
    if expected_models is None:
        paths = sorted(output_dir.glob("*_DECISION_SUMMARY.json"))
    else:
        paths = [output_dir / f"{model_name}_DECISION_SUMMARY.json" for model_name in expected_models]
    for path in paths:
        if path.is_file():
            records.append(json.loads(path.read_text()))
    pass_count = sum(row["status"] == "PASS" for row in records)
    partial_count = sum(row["status"] == "PARTIAL" for row in records)
    fail_count = sum(row["status"] == "FAIL" for row in records)
    expected_count = len(paths) if expected_models is not None else len(records)
    summary = {
        "schema": "WAYNE069_X2_GMR_BATCH_SUMMARY_V1",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "models": [row["model"] for row in records],
        "status": "PASS" if len(records) == expected_count and pass_count == len(records) else "PARTIAL",
        "expected_count": expected_count,
        "gmr_pass": pass_count,
        "gmr_partial": partial_count,
        "gmr_fail": fail_count,
        "records": records,
    }
    summary_path = output_dir / "X2_GMR_BATCH_SUMMARY.json"
    summary_path.write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({"status": summary["status"], "models": summary["models"],
                      "summary": str(summary_path)}, indent=2))
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", action="append",
                        help="run selected model(s); arbitrary run-local model IDs are accepted when their source files exist")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--input-dir", type=Path, default=INPUTS,
                        help="Stage-II input directory; defaults to the five base model inputs")
    parser.add_argument("--output-dir", type=Path, default=OUT,
                        help="separate directory for X2 references and QC decisions")
    parser.add_argument("--solver-mode", choices=("native_qp", "analytic_gmr"),
                        default="native_qp",
                        help="native_qp reproduces the run baseline; analytic_gmr selects the calibrated analytic X2 arm solver")
    parser.add_argument("--summary-only", action="store_true",
                        help="rebuild the batch decision JSON from existing per-model summaries")
    args = parser.parse_args()
    models = tuple(dict.fromkeys(args.model or MODELS))
    if any(not name or not all(c.isalnum() or c in "_-" for c in name) for name in models):
        parser.error("model IDs may contain only letters, digits, underscores, and hyphens")
    output_dir = args.output_dir.resolve()
    input_dir = args.input_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    if args.summary_only:
        write_batch_summary(output_dir, tuple(args.model) if args.model else None)
        return
    targets = [output_dir / f"{name}_x2_gmr_full.npz" for name in models]
    if not args.force and any(path.exists() for path in targets):
        raise FileExistsError("one or more X2 retarget outputs exist; pass --force to replace selected models")
    audit = verify_assets()
    cpu_limit = constrain_process()

    with workspace_lock("GMR-X2-" + "-".join(models)):
        sys.path.insert(0, str(RETARGETER))
        sys.path.insert(0, str(GMR_ROOT))
        os.chdir(GMR_ROOT)
        import torch
        import batch_retarget_x2 as batch
        from general_motion_retargeting.params import ROBOT_XML_DICT
        from general_motion_retargeting.utils.smpl import get_gvhmr_data_offline_fast

        ROBOT_XML_DICT["agibot_x2"] = IK_XML
        batch.X2_XML = IK_XML
        saved_stdout = sys.stdout
        batch.worker_init(canonical=True, canonical_height=CANONICAL_HEIGHT_M)
        sys.stdout = saved_stdout
        if args.solver_mode == "analytic_gmr":
            from general_motion_retargeting.motion_retarget import GeneralMotionRetargeting
            batch._worker["GMR"] = GeneralMotionRetargeting
        batch._worker["frames"] = get_gvhmr_data_offline_fast
        torch.set_num_threads(1)
        torch.set_grad_enabled(False)

        records = []
        for model_name in models:
            records.append(retarget_one(model_name, batch, get_gvhmr_data_offline_fast,
                                        cpu_limit, audit, input_dir, output_dir,
                                        args.solver_mode))
        write_batch_summary(output_dir, models)


if __name__ == "__main__":
    main()
