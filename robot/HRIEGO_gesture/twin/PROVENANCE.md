# twin/ provenance

Scripts copied verbatim (content unchanged) from the Laplace RCP-01 experiments so the twin harness runs from this repo.

| File in twin/ | Source (Laplace repo) | sha256 |
|---|---|---|
| `stream_arms_waist.py` | `experiments/RCP-01/runs/20261021_rcp01c07_ready_pose/scripts/stream_arms_waist.py` | f41261210082972ad54147c6d2596bf7c3edce942621323e99bbdfa2287e064f |
| `synced_playback.py` | `experiments/RCP-01/runs/20261021_rcp01c10_synced_preset_playback/scripts/synced_playback.py` | 5d38ce92a37cddb3ff3b5a97a493463d7ac59c478d33d1d7d576aa02ffcdcd55 |
| `mc_preset_helper.py` | `experiments/RCP-01/runs/20261021_rcp01c10_synced_preset_playback/scripts/mc_preset_helper.py` | cf68fabc290aa0a81ab95db6a3cfd3ef0be52fd9e990b410a68951759de810d0 |

Source paths are relative to the Laplace repo root (`$LAPLACE_ROOT`). `run_visits.py` and `mission_4e.py` now copy `stream_arms_waist.py` / `synced_playback.py` from this folder; `mc_preset_helper.py` is still referenced from `$LAPLACE_ROOT` by some orchestrators (identical content).
