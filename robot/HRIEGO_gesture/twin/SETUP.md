# Reproducing the HRIEGO gesture experiments on the AgiBot X2 official digital twin

Everything here is manual and host-side; the scripts in this folder drive a running twin container (`x2-twin`) through `podman exec/cp`.
Paths come from env vars (see `../paths.py`): `X2_ULTRA_ROOT`, `LAPLACE_ROOT`, `X2_TELEOP_ROOT` (default `~/GitHub/x2_ultra-real-robot-teleop`),
`X2_URDF_ROOT` (default `~/robots/agibot_x2_urdf`), `X2_SIM_ROOT` (default `~/robots/x2_simulation`), `X2_TWIN_SHIM_DIR`
(default `~/.local/share/hriego/shim`), `CONDA_BASE`, `HRIEGO_PY_PHYSICALAI`, `COMMERCIAL_SHOW_DIR`.

## 1. Twin image and container (`official_sim/`)
In the x2_ultra repo (`git ls-files official_sim`): `fetch_assets.sh` (AimDK + MC assets into `$X2_SIM_ROOT`), `docker/Dockerfile` (`x2-ultra-sim:humble`),
`docker/Dockerfile.twin` (`x2-twin:humble`), `sim.sh image`, `twin.sh {image,up,start,stand,suelta,portico,reset,stop,down}`.
```
official_sim/sim.sh image      # x2-ultra-sim:humble
official_sim/twin.sh image     # x2-twin:humble
official_sim/twin.sh up        # container x2-twin (GPU, isolated network)
official_sim/twin.sh start     # twin + official MC x86 v1.0.0 (logs/twin.log, logs/twin_mc.log)
official_sim/twin.sh stand     # STAND_DEFAULT under the gantry; `suelta` releases it -> STABLE
```
The official MC is proprietary AgiBot software (not in this repo); `fetch_assets.sh` downloads it.

## 2. docker -> podman shim
`twin.sh` calls `docker`; the experiments ran with rootless podman. Put this on PATH (the scripts prepend `$X2_TWIN_SHIM_DIR`; export PATH yourself for the `.py` orchestrators):
```
mkdir -p "$X2_TWIN_SHIM_DIR"   # default ~/.local/share/hriego/shim
printf '#!/bin/sh\nexec podman "$@"\n' > "$X2_TWIN_SHIM_DIR/docker"
printf '#!/bin/sh\nexec /usr/bin/podman "$@"\n' > "$X2_TWIN_SHIM_DIR/podman"; chmod +x "$X2_TWIN_SHIM_DIR"/*
export PATH="$X2_TWIN_SHIM_DIR:$PATH"
```
(Only the `docker` link matters; with real docker installed no shim is needed, but the orchestrators call `podman`: alias it.)

## 3. Overlay 1: McBodyPose (`/tmp/aimdk_overlay`, in the container)
`aimdk_msgs` with the interfaces the robot has and the public SDK lacks (`McBodyPose`, `SetMcMotion`, ...). Waist/head control uses `McBodyPose`.
`scripts/build_aimdk_overlay.sh` (+ `scripts/aimdk_overlay/`, see its README) builds it on the host into `$X2_SIM_ROOT/aimdk_overlay/install`.
The twin scripts source it as `/tmp/aimdk_overlay/install/local_setup.bash`: after `twin.sh up`, copy it in
`podman cp $X2_SIM_ROOT/aimdk_overlay x2-twin:/tmp/aimdk_overlay` (or build it inside the container with the same colcon command). Lives in the container `/tmp`: redo it if the container is recreated.

## 4. Overlay 2: preset motions (`/tmp/aimdk_mc_overlay`)
Presets (BOW, HEART, APPLAUSE, FAREWELL...) are played via `SetMcPresetMotion`. MC v1.0.0 decodes the request as `{header, area, motion, interrupt, ani_path, play_timestamp}`,
**without** `input_source`; the public SDK type (and the twin `mc_shim` forwarder) inserts `input_source` after the header, so the MC reads `area=len(name)+1, motion=0` and plays the wrong thing.
Hence `mc_preset_helper.py` (this folder): a separate process (two `aimdk_msgs` cannot be loaded together) on the MC private DDS domain 77, linked against an overlay from which
`input_source` was removed. Build (needs overlay 1 first, ~70 s): `bash twin/build_mc_overlay.sh`. Stdin protocol: `set MOTION AREA INTERRUPT | get TASK | quit`, JSON on stdout.
Copy the helper into the container (`podman cp twin/mc_preset_helper.py x2-twin:/tmp/`) — `p4_commercial.py` does this for you from `$LAPLACE_ROOT/.../c10/scripts/` (same file).

## 5. MC parameters
`animation_player.yaml` (`$X2_SIM_ROOT/mc/bin/mc_param/src/robot/lx2501_3_t2d5/classic/animation_player.yaml`): keep `animation_cfg.preplaying_tick: 500` (vendor default, ~1.0 s pre-roll
before the player state becomes 2). c11b measured `0` removes the pre-roll but all reported runs here use 500; always restore it (sha256 d7b53398...ad2da).
The preset animation CSVs are AgiBot MC assets and are not included.

## 6. Memory and process rules
- Stop `x2-llm` and the warm workers (`../workers`) before starting the twin; one heavy GPU process at a time (SONIC / StreamTalk stages are strictly sequential).
- Twin + MC need several GB of host RAM; check `free -g` first and never run `sonic_batch.py` concurrently with a twin run.
- Leave the twin in `STAND_DEFAULT`/`STABLE` at the end of every run (`twin.sh stand && twin.sh suelta`); the scripts assert `wait_stable` at start and fail closed otherwise.
- Pose lifecycle: `STAND_DEFAULT` -> URS (`UPPERBODY_REMOTE_SPLIT`, arms at 50 Hz via source 65, waist/head via `McBodyPose`) -> back to `STAND_DEFAULT`; after URS turns the
  waist/head are zeroed (`stream_point --zero`). In-container paths used by the scripts: `/tmp/aimdk_overlay`, `/tmp/aimdk_mc_overlay`, `/workspace/aimdk`, `/workspace/x2_ultra`, scratch `/tmp/{p4,c12,v4c,v4e,p4aa}`.

## 7. Running
From `HRIEGO_gesture/twin` (python with numpy; `PYTHONPATH=$X2_TELEOP_ROOT/src` for analysis): `run_visits.py`, `mission_4e.py RANK`, `run_c12.py`, `p4_commercial.py ultra,original 1`,
`run_matrix.sh`. Vendored here (see `PROVENANCE.md`): `stream_arms_waist.py` (c07) and `synced_playback.py` (c10). `mc_preset_helper.py` is also a local copy (identical to c10). Still external (not in this repo): RCP-01 c10 `$LAPLACE_ROOT/experiments/RCP-01/runs/` for the remaining references,
the c08 preset latency metrics (copied to `replay/presets_c08/`). The motion inputs ship in `replay/` (see `replay/README.md`).
