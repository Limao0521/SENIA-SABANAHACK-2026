#!/usr/bin/env python3
"""Run the unchanged StreamTalk implementation with a verified CPU-side cache."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import runpy
import sys

import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # HRIEGO_gesture root -> paths.py
import paths as _hp  # noqa: E402
import pandas as pd
from scipy.spatial import cKDTree

UPSTREAM = Path(__file__).resolve().parent / "streamtalk_runner.py"
STREAMTALK = _hp.streamtalk_root()  # $STREAMTALK_ROOT
SCRIPTS = STREAMTALK / "Scripts"
FM = SCRIPTS / "FM"
VENDOR = STREAMTALK / "vendor"
sys.path[:0] = [str(VENDOR), str(SCRIPTS), str(FM)]
import FindNearest  # noqa: E402


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--cache-dir", type=Path, default=_hp.streamtalk_retrieval_cache())
    ap.add_argument("--csv", type=Path, default=_hp.streamtalk_train_csv())
    ap.add_argument("--data-folder", type=Path, default=_hp.beat2_smplx_dir())
    custom, upstream_args = ap.parse_known_args()
    pid = "1_wayne_0"
    if "--pid" in upstream_args:
        pid_pos = upstream_args.index("--pid")
        pid = upstream_args[pid_pos + 1]
    else:
        upstream_args.extend(["--pid", pid])
    expected_pid = pid
    manifest_path = custom.cache_dir / "cache_manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"retrieval cache missing: run precompute_streamtalk_retrieval.py first")
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("status") != "COMPLETE" or manifest.get("pid") != pid:
        raise RuntimeError(f"retrieval cache is incomplete or for a different speaker: {manifest.get('status')}")
    if manifest.get("csv_sha256") != sha256(custom.csv):
        raise RuntimeError("retrieval CSV changed since cache creation")

    def cached_make_kdtree(pid="2_scott_0", data_folder=None, csv_file=None, smplx_model_path=None):
        if pid != expected_pid:
            raise RuntimeError(f"unexpected StreamTalk retrieval speaker {pid}")
        table = pd.read_csv(csv_file)
        ids = [str(x) for x in table.loc[table["type"] == "train", "id"] if str(x).startswith(pid)]
        rec_by_id = {rec["id"]: rec for rec in manifest["files"]}
        if set(ids) != set(rec_by_id):
            raise RuntimeError(f"cache file count mismatch: current={len(ids)} cached={len(rec_by_id)}")
        joint_parts, pose_parts = [], []
        expected_total = 0
        for sample_id in ids:
            source = Path(data_folder) / f"{sample_id}.npz"
            rec = rec_by_id[sample_id]
            if sha256(source) != rec["source_sha256"]:
                raise RuntimeError(f"retrieval source changed after cache: {sample_id}")
            cache_path = custom.cache_dir / rec["cache"]
            with np.load(cache_path, allow_pickle=False) as z:
                if z["source_sha256"].item() != rec["source_sha256"] or z["model_sha256"].item() != manifest["model_sha256"]:
                    raise RuntimeError(f"retrieval cache hash mismatch: {sample_id}")
                j = np.asarray(z["joints"], dtype=np.float32)
                p = np.asarray(z["poses"], dtype=np.float32)
            if len(j) != rec["frames"] or len(p) != rec["frames"]:
                raise RuntimeError(f"retrieval cache shape mismatch: {sample_id}")
            joint_parts.append(j.reshape(len(j), -1))
            pose_parts.append(p)
            expected_total += len(j)
        all_joints = np.concatenate(joint_parts, axis=0)
        all_poses = np.concatenate(pose_parts, axis=0)
        if len(all_joints) != expected_total or expected_total != manifest["total_frames"]:
            raise RuntimeError("retrieval cache total-frame mismatch")
        tree = cKDTree(all_joints, copy_data=False)
        print(f"[retrieval-cache] verified {len(ids)} files / {len(all_joints)} frames; CPU cache -> exact L1 GPU chunks", flush=True)
        return {"kdtree": tree, "poses": all_poses, "joints": all_joints}

    FindNearest.MakeKDTree = cached_make_kdtree
    sys.argv = [str(UPSTREAM), *upstream_args]
    runpy.run_path(str(UPSTREAM), run_name="__main__")


if __name__ == "__main__":
    main()
