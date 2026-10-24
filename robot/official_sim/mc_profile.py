#!/usr/bin/env python3
"""Build and inspect a private MC SIM tree for twin locomotion experiments.

The vendor's em_run.sh derives MC_ENV_PATH from the location of the script and
does not accept a SIM configuration override.  A private tree lets us run that
unchanged script while SimConfigAdapter writes only to a private ``sim/`` dir.
Large, read-only models and binaries remain symlinks to the vendor installation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import yaml


PROFILES = {
    "P0": "vendor baseline",
    "P1": "STAND_DEFAULT without automatic transition (rl_cpgtelecon)",
    "P2": "LOCOMOTION_DEFAULT with rl_cpgwalk",
}
ALIASES = {
    "baseline": "P0",
    "stand_cpgtelecon": "P1",
    "cpgwalk": "P2",
}


def canonical_profile(value: str) -> str:
    profile = ALIASES.get(value, value.upper())
    if profile not in PROFILES:
        raise ValueError(f"Unsupported MC profile {value!r}; choose P0, P1 or P2")
    return profile


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def stand_action(data: dict) -> dict:
    states = data.get("state_actions", {}).get("STABLE", [])
    matching = [action for action in states if action.get("name") == "STAND_DEFAULT"]
    if len(matching) != 1:
        raise ValueError("Expected exactly one STAND_DEFAULT in state_actions.STABLE")
    return matching[0]


def locomotion_runner(data: dict) -> dict:
    actions = data.get("transition_actions", {}).get("LOCOMOTION_DEFAULT", [])
    if len(actions) != 1 or len(actions[0].get("runners", [])) != 1:
        raise ValueError("Expected one LOCOMOTION_DEFAULT runner group with one runner")
    return actions[0]["runners"][0]


def patch_profile(robot_dir: Path, profile: str) -> None:
    if profile == "P1":
        path = robot_dir / "action_state.yaml"
        data = yaml.safe_load(path.read_text())
        stand_action(data)["auto_transition"] = False
        path.write_text(yaml.safe_dump(data, sort_keys=False))
    elif profile == "P2":
        path = robot_dir / "action_setting.yaml"
        data = yaml.safe_load(path.read_text())
        runner = locomotion_runner(data)
        runner["name"] = "rl_cpgwalk"
        runner["cfg_path"] = "./rl/cpgwalk_config.yaml"
        path.write_text(yaml.safe_dump(data, sort_keys=False))


def assert_profile(robot_dir: Path, profile: str, *, sim: bool = False) -> dict:
    base = robot_dir / "sim" if sim else robot_dir
    action_state = yaml.safe_load((base / "action_state.yaml").read_text())
    action_setting = yaml.safe_load((base / "action_setting.yaml").read_text())
    stand = stand_action(action_state)
    runner = locomotion_runner(action_setting)
    expected_stand = profile != "P1"
    expected_runner = "rl_cpgwalk" if profile == "P2" else "rl_foundationwalk"
    expected_cfg = "./rl/cpgwalk_config.yaml" if profile == "P2" else "./rl/foundationwalk_config.yaml"
    if stand.get("auto_transition") is not expected_stand:
        raise ValueError(f"{base}: unexpected STAND_DEFAULT.auto_transition")
    if runner.get("name") != expected_runner or runner.get("cfg_path") != expected_cfg:
        raise ValueError(f"{base}: unexpected LOCOMOTION_DEFAULT runner/config")
    if not (base / "rl" / "cpgwalk_config.yaml").exists() and not sim:
        # Absolute container symlinks may not resolve on the host; this check is
        # made only when preparation runs in the container.
        raise ValueError(f"{base}: missing cpgwalk_config.yaml")
    return {
        "stand_auto_transition": stand["auto_transition"],
        "locomotion_runner": runner["name"],
        "locomotion_config": runner["cfg_path"],
    }


def prepare(vendor: Path, runtime: Path, robot: str, profile: str) -> dict:
    profile = canonical_profile(profile)
    vendor = vendor.resolve(strict=True)
    source_bin = vendor / "bin"
    source_robot = source_bin / "mc_param" / "src" / "robot" / robot
    for required in ("em_run.sh", "aima-mc-app-main"):
        if not (source_bin / required).is_file():
            raise FileNotFoundError(source_bin / required)
    for required in ("mc_sim.yaml", "action_state.yaml", "action_setting.yaml"):
        if not (source_robot / required).is_file():
            raise FileNotFoundError(source_robot / required)
    if not (source_robot / "rl" / "cpgwalk_config.yaml").is_file():
        raise FileNotFoundError(source_robot / "rl" / "cpgwalk_config.yaml")

    runtime.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".mc-profile-", dir=runtime.parent))
    try:
        # docker exec normally builds this as root; let the host user inspect
        # the generated configuration and manifest without changing ownership.
        stage.chmod(0o755)
        private_mc = stage / "mc"
        private_bin = private_mc / "bin"
        private_robot = private_bin / "mc_param" / "src" / "robot" / robot
        private_bin.mkdir(parents=True)
        private_robot.mkdir(parents=True)
        (private_bin / "log").mkdir()
        (private_bin / "sim_robot_name_cache.txt").write_text(robot + "\n")
        for name in ("lib", "mc3th"):
            (private_mc / name).symlink_to(vendor / name, target_is_directory=True)
        for entry in source_bin.iterdir():
            if entry.name in {"mc_param", "log", "sim_robot_name_cache.txt"}:
                continue
            (private_bin / entry.name).symlink_to(entry, target_is_directory=entry.is_dir())
        (private_bin / "mc_param" / "src" / "urdf").symlink_to(
            source_bin / "mc_param" / "src" / "urdf", target_is_directory=True
        )
        for entry in source_robot.iterdir():
            dest = private_robot / entry.name
            if entry.name == "sim":
                dest.mkdir()
            elif entry.is_dir():
                dest.symlink_to(entry, target_is_directory=True)
            elif entry.is_file():
                shutil.copy2(entry, dest)
            else:
                raise ValueError(f"Unexpected MC configuration entry: {entry}")
        patch_profile(private_robot, profile)
        expected = assert_profile(private_robot, profile)
        manifest = {
            "profile": profile,
            "description": PROFILES[profile],
            "robot": robot,
            "vendor": str(vendor),
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "vendor_sha256": {
                name: sha256(source_robot / name)
                for name in ("mc_sim.yaml", "action_state.yaml", "action_setting.yaml")
            },
            "private_sha256": {
                name: sha256(private_robot / name)
                for name in ("mc_sim.yaml", "action_state.yaml", "action_setting.yaml")
            },
            "expected": expected,
        }
        (stage / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        if runtime.exists() or runtime.is_symlink():
            if runtime.is_dir() and not runtime.is_symlink():
                shutil.rmtree(runtime)
            else:
                runtime.unlink()
        os.replace(stage, runtime)
        return manifest
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def inspect(runtime: Path, log_path: Path | None = None) -> dict:
    manifest = json.loads((runtime / "manifest.json").read_text())
    profile = canonical_profile(manifest["profile"])
    robot_dir = runtime / "mc" / "bin" / "mc_param" / "src" / "robot" / manifest["robot"]
    source = assert_profile(robot_dir, profile)
    for name, expected in manifest["private_sha256"].items():
        if sha256(robot_dir / name) != expected:
            raise ValueError(f"Private MC source changed after preparation: {name}")
    generated = None
    if (robot_dir / "sim" / "action_state.yaml").is_file() and (
        robot_dir / "sim" / "action_setting.yaml"
    ).is_file():
        generated = assert_profile(robot_dir, profile, sim=True)
    loaded = None
    runner_initialized = None
    if log_path and log_path.is_file():
        log = log_path.read_text(errors="replace")
        private_cfg = str(robot_dir / "mc_sim.yaml")
        generated_setting = str(robot_dir / "sim" / "action_setting.yaml")
        loaded = private_cfg in log and generated_setting in log
        # The MC loads all runners at boot. Verify the runner mapped to this
        # specific action rather than a similarly named runner in another mode.
        action_match = re.search(
            r"Transition action: LOCOMOTION_DEFAULT,.*?(?=Transition action:|\Z)",
            log,
            re.DOTALL,
        )
        expected_runner = source["locomotion_runner"]
        runner_initialized = bool(
            action_match
            and re.search(rf"Runner name: {re.escape(expected_runner)}(?:\x1b|\s|$)", action_match.group())
        )
    return {
        "profile": profile,
        "description": PROFILES[profile],
        "robot": manifest["robot"],
        "created_utc": manifest["created_utc"],
        "source": source,
        "generated": generated,
        "mc_loaded_private_config": loaded,
        "locomotion_runner_initialized": runner_initialized,
        "vendor_sha256": manifest["vendor_sha256"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("prepare", help="build a private MC tree")
    build.add_argument("--vendor", type=Path, required=True)
    build.add_argument("--runtime", type=Path, required=True)
    build.add_argument("--robot", required=True)
    build.add_argument("--profile", required=True)
    status = commands.add_parser("inspect", help="verify source, generated SIM, and MC log")
    status.add_argument("--runtime", type=Path, required=True)
    status.add_argument("--log", type=Path)
    status.add_argument("--require-loaded", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            result = prepare(args.vendor, args.runtime, args.robot, args.profile)
        else:
            result = inspect(args.runtime, args.log)
            if args.require_loaded and not (
                result["generated"]
                and result["mc_loaded_private_config"]
                and result["locomotion_runner_initialized"]
            ):
                raise ValueError("MC has not loaded and initialized the selected private profile")
        print(json.dumps(result, indent=2))
        return 0
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f"MC profile error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
