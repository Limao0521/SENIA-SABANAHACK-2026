# motion/ vendored sources: provenance

Source repository: `$LAPLACE_ROOT` (`~/GitHub/Laplace`, HEAD `1f84deaeca5b1f425abf21d4f6daef1a0f08be4d`, 2026-10-22). Paths below are relative to
`experiments/RCP-01/runs/`. Identity is pinned by the sha256 of the **original** file; the vendored copies differ only as listed.

| vendored file | source | source sha256 | change |
|---|---|---|---|
| `x2_retarget.py` | `20261020_rcp01c03_segment_scheduler/scripts/x2_retarget.py` | `cba0a229de21299181d5afd314ba0e0bf84ad15aec2d12f33691e7a62ad51944` | `g1/paths.py` import replaced by HRIEGO `paths.x2_external_root()` / `x2_ik_dir()`; `EXPERIMENT`-relative `X2_ROOT` default replaced by `$X2_MOTION_ROOT` (default `./x2`); logic unchanged |
| `x2_sonic.py` | `20261020_rcp01c03_segment_scheduler/scripts/x2_sonic.py` | `6ddecf9974ed09bbf3c8e5dac74b1c1d588ae263aeb8405afa84716ef8477ccb` | same as above (only `x2_external_root()`) |
| `streamtalk_runner.py` | `20261019_rcp01c02_fixedcalm/scripts/streamtalk_runner.py` | `51f0f1be5d05d5c1b77cd0edba390a2d321553c76a24656e4646790bd9447a36` | `BUNDLED_FACE_CHECKPOINT` from `paths.streamtalk_root()`; default `--example_motion_npz` -> `paths.READY_PROMPT`; `import paths` added |
| `streamtalk_cached_entry.py` | `20261019_rcp01c02_fixedcalm/scripts/streamtalk_cached_entry.py` | `9f38b4d5bfcbd466e98b48a8ebe2e3a40278a62eb4dbbf58efd4b8a283814716` | ROOT/RUN-derived defaults replaced by `paths.streamtalk_root()`, `streamtalk_retrieval_cache()`, `streamtalk_train_csv()`, `beat2_smplx_dir()` (removes a hard-coded absolute BEAT2 default); upstream runner is the sibling `streamtalk_runner.py` |
| `prep_arms.py` | `20261021_rcp01c07_ready_pose/scripts/prep_arms.py` | `ab40b6e3b71288cf184210c5d20ea356c6342c9ccd1a073a57096ca7532a0978` | none |
| `config/streamtalk_ready_prompt.npz` | `20261021_rcp01c07_ready_pose/configs/streamtalk_ready_prompt.npz` | `dfe0409c2ae06c6d63239f964703d4d02fc4d44e0f365e229521a923b0202d7c` | none (binary; un-ignored in `.gitignore`) |

## External (not copied)

* `sonic_x2_c9959443` bundle (SONIC scripts + ONNX), `agibot_x2_retargeter_bb582d97`, GMR (pip pin `bb1bbe4`): under `$X2_EXTERNAL_ROOT`
  (default `$LAPLACE_ROOT/mimo/X2/vendor/x2_external_evidence`); IK model dir `$X2_IK_DIR` (default `$LAPLACE_ROOT/mimo/X2/results/v2m_02_x2_ultra`).
* StreamTalk code + checkpoint + WavLM: `$STREAMTALK_ROOT` (default `$LAPLACE_ROOT/Gesture/third_party/StreamTalk`). Retrieval cache `$STREAMTALK_RETRIEVAL_CACHE`, train split `$STREAMTALK_TRAIN_CSV`.
* SMPL-X (`$SMPLX_NEUTRAL_NPZ`) and BEAT2 (`$BEAT2_SMPLX_DIR`): see `paths.py`.
