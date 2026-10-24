# HRIEGO_gesture/align

Standalone CLI: QuartzNet 15x5 CTC trigger anchors on Supertonic M1 WAVs (44.1 kHz) + preset dispatch plans.

    QUARTZNET_ONNX=/path/to/quartznet15x5_es/model.onnx $HRIEGO_PY_PHYSICALAI HRIEGO_gesture/align/anchor_plan.py \
        --manifest M.jsonl --out DIR [--latency L] [--align auto|stroke|midpoint|onset] [--stat median] [--max-initial-delay-s 1.5]

The c09 modules (`align_anchor`, `event_builder`, `preset_scheduler`, `common`) and the E4/E7 QuartzNet code are vendored in
`vendor/` (see `vendor/PROVENANCE.md`); only the ONNX model is external (`$QUARTZNET_ONNX`, sha256 pinned and checked at load).
QuartzNet runs on CPU with c09's lowered in-process memory guard (2 GiB preflight / 1.5 GiB stop). Default latency table:
`config/preset_latency_c08.json` (the c08 twin table). Manifest `wav_path` is relative to the HRIEGO_gesture root.

Alignment policy `--align` (default `auto`): dispatch = anchor start - lead. `stroke` uses lead_stroke_s, `onset` lead_onset_s,
`midpoint` their mean. `auto` tries stroke and, if that needs a start-of-audio delay above `--max-initial-delay-s`, falls back to
midpoint, then onset. The policy used is recorded per row (`align_policy_used`) and in `plans/*.json` `policy.align_policy_used`.
Guard G1: a row whose `speech[start:end] != trigger_text` is `rejected_trigger_span` (no event, no plan).

Audio is resampled to 16 kHz internally (scipy `resample_poly`, 160/441); all samples
in events/plans are at the WAV native rate. CTC frame step is 20 ms (anchor resolution).

Manifest row keys: case_id, visit_id, turn_id, state, speech, emote, motion_id, area, trigger_text,
trigger_char_start/end (char span in `speech`), wav_path, wav_sha256, sample_rate, duration_s, synth_s.

Outputs in DIR: `events/<case>.anchor.json` (CTC span), `events/<case>.event.json` (x2_gesture_event.v1),
`plans/<case>.plan.json` and `plans/<case>.plan_preroll0.json` (x2_gesture_plan.v1), `summary.json`, `summary.csv`.

Row status (`status`, `status_preroll0`): `planned` (c09 pending), `none_sonic` (emote NONE/null), `anchor_only_no_preset`
(motion_id null), `rejected_hash` (file sha/sample-rate/missing vs manifest), `rejected_trigger_span` (G1), `rejected_resources` (no latency for binding / table
unreadable), `rejected_horizon` (start-of-audio delay > 1.5 s), `rejected_conflict`, `alignment_<status>` (CTC not ok).
`delay_s` = required audio start delay (dispatch < 0 -> audio must start that much later; WAV never edited).
`plan_preroll0`: leads and duration reduced by each binding's `lead_play_start_s` (hypothetical zero MC pre-roll; missing -> rejected_resources).
Each case is independent (no cross-row conflicts: one trigger per turn).

Normalization (CTC text view): lowercased, punctuation (`¡ ! ¿ ? , .`) dropped, accents (á é í ó ú) kept - the Spanish model
supports them. Char spans refer to the ORIGINAL `speech`; trigger text including `¡` is fine, but prefer spans on plain words.
Digits/symbols are not covered by this model: write numbers as words in `speech`.

Test: `gen_test_wavs.py` (Supertonic venv python) -> `test_wavs/`, `test_manifest.jsonl`; run output in `test_out/`.
Caveats: CTC spans are not human validated; emote->motion mapping in the test manifest is illustrative.
