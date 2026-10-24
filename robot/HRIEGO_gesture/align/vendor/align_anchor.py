#!/usr/bin/env python3
"""[VENDORED from Laplace RCP-01c09 align_anchor.py, HRIEGO modifications marked "HRIEGO:" - see PROVENANCE.md]
Full-text QuartzNet 15x5 CTC alignment of one WAV; anchor span in native-rate samples.

The QuartzNet ONNX model stays external: set QUARTZNET_ONNX to the model.onnx of
OpenVoiceOS/stt_es_quartznet15x5_onnx (vocab.txt must sit next to it). Its sha256 is checked at load against the pin below.
"""
import os
from pathlib import Path

os.environ["CUDA_VISIBLE_DEVICES"] = ""
os.environ.setdefault("OMP_NUM_THREADS", "2")
from .common import sha256_file

PINNED_REVISION = "openvoiceos@8f9307e93cdb05a06fcaa0b977312495e96f405d"
PINNED_ONNX_SHA256 = "e66ee9f0798f0faca8f426b9124c66c20df76d6dadb3a2371c808cff597c3931"
METHOD = "quartznet15x5_ctc_viterbi"


def quartznet_onnx_path():
    """HRIEGO: model location from $QUARTZNET_ONNX; default $LAPLACE_ROOT/voice/experimental/.../models/quartznet15x5_es/model.onnx."""
    p = os.environ.get("QUARTZNET_ONNX")
    if not p:
        root = os.environ.get("LAPLACE_ROOT", str(Path.home() / "GitHub/Laplace"))
        p = str(Path(root) / "voice/experimental/hri_temporal_alignment_20261020/E4_COMPACT_CTC/models/quartznet15x5_es/model.onnx")
    return Path(p)


class Aligner:
    def __init__(self, cpu_threads=2, ram_stop_gib=1.5, ram_preflight_gib=2.0):
        import soundfile as sf
        from . import base_adapter
        # the model peaks near 0.3 GiB RSS; the E4 default 6 GiB preflight is too strict on a busy box
        d = base_adapter.MemoryGuard.__init__.__defaults__
        base_adapter.MemoryGuard.__init__.__defaults__ = (int(ram_stop_gib * 1024**3), int(ram_preflight_gib * 1024**3)) + d[2:]
        from .quartznet_adapter import QuartzNetAdapter
        from .features import extract_features
        from .text_normalization import TextView, Word, make_text_view
        from . import align_qwen_espeak_ng as e7
        self.sf, self.e7 = sf, e7
        self.ctx = (extract_features, make_text_view, TextView, Word)
        onnx = quartznet_onnx_path()
        if not onnx.is_file():
            raise FileNotFoundError(f"QuartzNet ONNX not found: {onnx} (set QUARTZNET_ONNX; see align/vendor/PROVENANCE.md)")
        sha = sha256_file(onnx)
        if sha != PINNED_ONNX_SHA256 and os.environ.get("QUARTZNET_ALLOW_UNPINNED") != "1":  # HRIEGO: fail closed on a different model
            raise RuntimeError(f"QuartzNet ONNX sha256 {sha} != pinned {PINNED_ONNX_SHA256} ({PINNED_REVISION}); "
                               "set QUARTZNET_ALLOW_UNPINNED=1 to override (results then not comparable)")
        model = onnx.parent
        self.adapter = QuartzNetAdapter(model, cpu_threads=cpu_threads, device="cpu")
        assert self.adapter.session.get_providers() == ["CPUExecutionProvider"]
        self.model_info = {"revision": PINNED_REVISION, "onnx_sha256": sha,
                           "provider": "CPUExecutionProvider", "cpu_threads": cpu_threads}
        self.model_info["onnx_sha256_matches_pinned"] = sha == PINNED_ONNX_SHA256
        self.model_info["model_revision"] = f"{PINNED_REVISION};onnx_sha256={sha}"
        self._cache = {}

    def close(self):
        self.adapter.guard.stop()

    def align(self, wav, text):
        """Full text vs full WAV -> dict(words, view, sample_rate_hz, sample_count, sha256)."""
        sha = sha256_file(wav)
        key = (sha, text)
        if key in self._cache:
            return self._cache[key]
        info = self.sf.info(str(wav))
        audio, sr = self.sf.read(str(wav), dtype="float32", always_2d=False)
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        ex, mk, TV, W = self.ctx
        prepared = self.e7.prepare_for_e7(self.adapter, audio, int(sr), text, ex, mk, TV, W)
        emissions, _ = self.adapter.infer(prepared)
        timing = self.adapter.align_prepared(prepared, emissions, [])
        res = {"sha256": sha, "sample_rate_hz": int(sr), "sample_count": int(info.frames),
               "words": timing["words"], "view": prepared["view"], "frame_step_s": timing["frame_step_s"],
               "frame_count": timing["frame_count"], "mean_token_log_probability": timing["mean_token_log_probability"]}
        self._cache[key] = res
        return res

    def anchor_span(self, al, char_start, char_end, occurrence=1):
        view = al["view"]
        text = view.original[char_start:char_end]
        m = self.e7.map_anchor_for_e7({"anchor_start": char_start, "anchor_end": char_end, "anchor_text": text},
                                      view, al["words"], *self.ctx[1:])
        out = {"status": m["status"], "text": text, "char_start": char_start, "char_end": char_end,
               "occurrence": occurrence, "start_s": m["start_s"], "end_s": m["end_s"],
               "start_sample": None, "end_sample": None, "sample_rate_hz": al["sample_rate_hz"],
               "frame_step_s": al["frame_step_s"]}
        if m["start_s"] is not None:
            fs, n = al["sample_rate_hz"], al["sample_count"]
            out["start_sample"] = min(n, int(round(m["start_s"] * fs)))
            out["end_sample"] = min(n, int(round(m["end_s"] * fs)))
        return out

    def anchor_for_wav(self, wav, text, char_start, char_end, occurrence=1):
        try:
            al = self.align(wav, text)
            span = self.anchor_span(al, char_start, char_end, occurrence)
        except Exception as exc:
            return {"status": "alignment_error", "error": f"{type(exc).__name__}: {exc}", "sha256": sha256_file(wav)}
        return {**span, "sha256": al["sha256"], "sample_count": al["sample_count"]}
