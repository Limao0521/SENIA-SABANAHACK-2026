# Vendored sources: provenance

Source repository: `$LAPLACE_ROOT` (the Laplace repo, `~/GitHub/Laplace` on the dev box; HEAD at vendoring time: `1f84deaeca5b1f425abf21d4f6daef1a0f08be4d`, 2026-10-22).
`git log -1 -- <file>` returned **no commit** for any of these files: the c09 scripts are untracked in Laplace and
`voice/experimental/*` (E4, E7) is git-ignored there (`.gitignore:313`). Identity is therefore pinned by the sha256 of the
**original** file at vendoring time (not of the vendored copy).

| vendored file | source (relative to Laplace root) | source sha256 | change |
|---|---|---|---|
| `common.py` | `experiments/RCP-01/runs/20261021_rcp01c09_gesture_event_scheduler/scripts/common.py` | `fa6a54be455f84cf823b0e8c2f7dec0228d28739b2dbe686d95a35ae7513ab29` | Laplace-tree path constants (RUN/REPO/E4/E7/PLACEHOLDER_LATENCY) removed |
| `align_anchor.py` | `experiments/RCP-01/runs/20261021_rcp01c09_gesture_event_scheduler/scripts/align_anchor.py` | `d704d76895f78fdccec16616650cf67922620a33f695961b67134d6bd89e159f` | relative imports; model path from `QUARTZNET_ONNX`; ONNX sha256 pin enforced (fail closed unless `QUARTZNET_ALLOW_UNPINNED=1`); E4/E7 sys.path hacks removed; `__main__` removed |
| `event_builder.py` | `experiments/RCP-01/runs/20261021_rcp01c09_gesture_event_scheduler/scripts/event_builder.py` | `1f733e0960a1c6753d5dc67082e804835868c788daeb2a69b5b3a85ad17e6c99` | relative import only |
| `preset_scheduler.py` | `experiments/RCP-01/runs/20261021_rcp01c09_gesture_event_scheduler/scripts/preset_scheduler.py` | `5a0d3679aadbac9bb547ac096e8278d7d8ab0ae9c1e964a961f01fe953c9f056` | relative imports; added `midpoint` policy (`_lead`) and `plan_event_auto` (stroke -> midpoint -> onset); CLI `__main__` removed (use align/anchor_plan.py) |
| `base_adapter.py` | `voice/experimental/hri_temporal_alignment_20261020/E4_COMPACT_CTC/adapters/base_adapter.py` | `bcc6eef16b24286478864cac2ed3cb6060d317eedf78641011d4caaf3bca61af` | relative imports only |
| `quartznet_adapter.py` | `voice/experimental/hri_temporal_alignment_20261020/E4_COMPACT_CTC/adapters/quartznet_adapter.py` | `363d8231c8661b92b06354af8287f2f061fe8793a349a46377693c07eadc5b00` | relative import only |
| `features.py` | `voice/experimental/hri_temporal_alignment_20261020/E4_COMPACT_CTC/alignment/features.py` | `86dbb3a683f4e987b9d19f0d73d7f3bad7d1c7ac80a8ce61d5a5dd7581a97122` | none |
| `ctc_forced_aligner.py` | `voice/experimental/hri_temporal_alignment_20261020/E4_COMPACT_CTC/alignment/ctc_forced_aligner.py` | `432f52f331636b2c512b07a873fbf9810a143b4a887a0b3e4faad753f5e836f8` | none |
| `span_mapper.py` | `voice/experimental/hri_temporal_alignment_20261020/E4_COMPACT_CTC/alignment/span_mapper.py` | `3b404d5526aae0ee2efcabf101afc2730d14e03909ca986ffe04f3cf2995f321` | relative import only |
| `text_normalization.py` | `voice/experimental/hri_temporal_alignment_20261020/E4_COMPACT_CTC/alignment/text_normalization.py` | `8578dff6bc61482b3d437ee295003d4b6ab42c4e0cfe431023b8c1c9d69fc5d3` | none |
| `align_qwen_espeak_ng.py` | `voice/experimental/hri_temporal_alignment_20261020/E7_X2_PROPOSAL_PILOT/experiments/align_qwen_espeak_ng.py` | `298f967e634615c25829f7268ead686acf7e65ebc9a792184c12947a483d15de` | extracted: only `make_ctc_text_view`, `map_anchor_for_e7`, `prepare_for_e7` (lines 62-181, unchanged); batch driver and E7 data paths dropped |

Not vendored (not imported): `citrinet_adapter.py`, other E7 scripts, E4 benchmarks.

## External model (not vendored, 75.6 MB)

* Model: OpenVoiceOS `stt_es_quartznet15x5_onnx` (ONNX export of NVIDIA NeMo `stt_es_quartznet15x5`, licence CC-BY-4.0).
* Pinned revision: `openvoiceos@8f9307e93cdb05a06fcaa0b977312495e96f405d`.
* Pinned `model.onnx` sha256: `e66ee9f0798f0faca8f426b9124c66c20df76d6dadb3a2371c808cff597c3931`.
* Location: `$QUARTZNET_ONNX` (path to `model.onnx`; `vocab.txt` must be in the same directory). Default:
  `$LAPLACE_ROOT/voice/experimental/hri_temporal_alignment_20261020/E4_COMPACT_CTC/models/quartznet15x5_es/model.onnx`.
* The sha256 is verified when `Aligner()` is created; a mismatch raises (override only with `QUARTZNET_ALLOW_UNPINNED=1`).
* Python deps: `onnxruntime`, `numpy`, `scipy`, `soundfile`, `psutil` (the `physicalai` env has them).
