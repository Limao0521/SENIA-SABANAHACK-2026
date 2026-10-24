"""Shared local ONNX CTC encoder adapter and manifest-span align API."""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from pathlib import Path

import numpy as np
import onnxruntime as ort
import psutil

from .ctc_forced_aligner import ctc_viterbi
from .features import HOP_LENGTH, SAMPLE_RATE, extract_features
from .span_mapper import assign_word_times, map_anchor
from .text_normalization import make_text_view


class MemoryGuard:
    def __init__(
        self,
        stop_below_bytes: int = 4 * 1024**3,
        preflight_below_bytes: int = 6 * 1024**3,
        device: str = "cpu",
        gpu_stop_free_bytes: int = 2 * 1024**3,
        gpu_preflight_free_bytes: int = 4 * 1024**3,
    ):
        self.stop_below_bytes = stop_below_bytes
        self.preflight_below_bytes = preflight_below_bytes
        self.device = device
        self.gpu_stop_free_bytes = gpu_stop_free_bytes
        self.gpu_preflight_free_bytes = gpu_preflight_free_bytes
        self.process = psutil.Process()
        self.peak_rss_bytes = self.process.memory_info().rss
        self.min_available_bytes = psutil.virtual_memory().available
        self.gpu_baseline = self._gpu_memory() if device == "cuda" else None
        self.gpu_min_free_bytes = self.gpu_baseline["free_bytes"] if self.gpu_baseline else None
        self.gpu_peak_used_bytes = self.gpu_baseline["used_bytes"] if self.gpu_baseline else None
        self._gpu_preflight_captured = False
        self._stop = threading.Event()
        self._thread = None

    def preflight(self):
        available = psutil.virtual_memory().available
        if available < self.preflight_below_bytes:
            raise MemoryError(f"preflight stopped: only {available / 1024**3:.2f} GiB available")
        if self.device == "cuda":
            gpu = self._gpu_memory()
            if not self._gpu_preflight_captured:
                self.gpu_baseline = gpu
                self.gpu_min_free_bytes = gpu["free_bytes"]
                self.gpu_peak_used_bytes = gpu["used_bytes"]
                self._gpu_preflight_captured = True
            if gpu["free_bytes"] < self.gpu_preflight_free_bytes:
                raise MemoryError(
                    f"GPU preflight stopped: only {gpu['free_bytes'] / 1024**3:.2f} GiB VRAM free"
                )

    @staticmethod
    def _gpu_memory() -> dict[str, int]:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total,memory.free,memory.used", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        fields = [int(item.strip()) for item in result.stdout.strip().split(",")]
        if len(fields) != 3:
            raise RuntimeError(f"unexpected nvidia-smi memory result: {result.stdout!r}")
        return {"total_bytes": fields[0] * 1024**2, "free_bytes": fields[1] * 1024**2, "used_bytes": fields[2] * 1024**2}

    def start(self):
        self._thread = threading.Thread(target=self._watch, name="e4-memory-guard", daemon=True)
        self._thread.start()

    def _watch(self):
        while not self._stop.is_set():
            available = psutil.virtual_memory().available
            self.min_available_bytes = min(self.min_available_bytes, available)
            self.peak_rss_bytes = max(self.peak_rss_bytes, self.process.memory_info().rss)
            if available < self.stop_below_bytes:
                os.kill(os.getpid(), signal.SIGTERM)
            if self.device == "cuda":
                try:
                    gpu = self._gpu_memory()
                    self.gpu_min_free_bytes = min(self.gpu_min_free_bytes, gpu["free_bytes"])
                    self.gpu_peak_used_bytes = max(self.gpu_peak_used_bytes, gpu["used_bytes"])
                    if gpu["free_bytes"] < self.gpu_stop_free_bytes:
                        os.kill(os.getpid(), signal.SIGTERM)
                except (OSError, subprocess.SubprocessError, ValueError, RuntimeError):
                    # A failed GPU memory query is not evidence of safety; stop the CUDA run.
                    os.kill(os.getpid(), signal.SIGTERM)
            self._stop.wait(0.5 if self.device == "cuda" else 0.1)

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        self.peak_rss_bytes = max(self.peak_rss_bytes, self.process.memory_info().rss)
        self.min_available_bytes = min(self.min_available_bytes, psutil.virtual_memory().available)


class OnnxCTCAdapter:
    model_key = ""
    features_size = 0
    subsampling_factor = 0
    blank_id = -1
    vocab = None

    def __init__(
        self,
        model_dir: str | Path,
        cpu_threads: int = 2,
        device: str = "cpu",
        gpu_mem_limit_bytes: int = 1024**3,
        gpu_stop_free_bytes: int = 2 * 1024**3,
        gpu_preflight_free_bytes: int = 4 * 1024**3,
    ):
        self.model_dir = Path(model_dir)
        self.model_path = self.model_dir / "model.onnx"
        self.cpu_threads = int(cpu_threads)
        if device not in {"cpu", "cuda"}:
            raise ValueError(f"unsupported device: {device}")
        self.device = device
        self.gpu_mem_limit_bytes = int(gpu_mem_limit_bytes)
        if not self.model_path.is_file():
            raise FileNotFoundError(self.model_path)
        self.vocab = self._load_vocab(self.model_dir / "vocab.txt")
        self._validate_vocabulary()
        self.frontend_metadata = None
        self.guard = MemoryGuard(
            device=device,
            gpu_stop_free_bytes=gpu_stop_free_bytes,
            gpu_preflight_free_bytes=gpu_preflight_free_bytes,
        )
        self.guard.preflight()
        self.rss_before_load = psutil.Process().memory_info().rss
        self.system_available_before_load = psutil.virtual_memory().available
        options = ort.SessionOptions()
        options.intra_op_num_threads = self.cpu_threads
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_DISABLE_ALL
        options.enable_mem_pattern = False
        options.enable_cpu_mem_arena = True
        self.provider_options = {}
        if device == "cuda":
            self.provider_options = {
                "device_id": "0",
                "gpu_mem_limit": str(self.gpu_mem_limit_bytes),
                "arena_extend_strategy": "kSameAsRequested",
                "cudnn_conv_algo_search": "HEURISTIC",
                "do_copy_in_default_stream": "1",
            }
            providers = [("CUDAExecutionProvider", self.provider_options), "CPUExecutionProvider"]
            options.enable_profiling = True
        else:
            providers = ["CPUExecutionProvider"]
        self.guard.start()
        start = time.perf_counter()
        try:
            self.session = ort.InferenceSession(
                str(self.model_path),
                sess_options=options,
                providers=providers,
            )
        except Exception:
            self.guard.stop()
            raise
        self.load_model_s = time.perf_counter() - start
        self.rss_after_load = psutil.Process().memory_info().rss
        self.system_available_after_load = psutil.virtual_memory().available
        self.guard.peak_rss_bytes = max(self.guard.peak_rss_bytes, self.rss_after_load)
        self.guard.start()
        self.input_meta = {item.name: item for item in self.session.get_inputs()}
        self.output_name = self.session.get_outputs()[0].name
        self.providers = self.session.get_providers()
        self.provider = self.providers[0]
        if device == "cpu" and self.provider != "CPUExecutionProvider":
            raise RuntimeError(f"expected CPUExecutionProvider, got {self.provider}")
        if device == "cuda" and "CUDAExecutionProvider" not in self.providers:
            raise RuntimeError(f"CUDAExecutionProvider unavailable, got {self.providers}")
        audio_input = self.input_meta.get("audio_signal")
        if audio_input is None:
            raise RuntimeError(f"missing audio_signal input: {list(self.input_meta)}")
        if len(audio_input.shape) != 3 or audio_input.shape[1] != self.features_size:
            raise RuntimeError(f"unexpected input shape: {audio_input.shape}")

    @staticmethod
    def _load_vocab(path: Path) -> dict[str, int]:
        vocab = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            token, sep, token_id = line.rpartition(" ")
            if not sep:
                raise ValueError(f"invalid vocabulary row: {line!r}")
            if token in vocab:
                raise ValueError(f"duplicate vocabulary token {token!r}")
            vocab[token] = int(token_id)
        return vocab

    def _validate_vocabulary(self):
        blanks = [token_id for token, token_id in self.vocab.items() if token == "<blk>"]
        if blanks != [self.blank_id]:
            raise ValueError(f"expected blank {self.blank_id}, found {blanks}")

    def encode_target(self, view):
        raise NotImplementedError

    def prepare(self, audio: np.ndarray, sample_rate: int, text: str) -> dict:
        start = time.perf_counter()
        features, feature_length, frontend_metadata = extract_features(
            audio, sample_rate, self.model_key, str(self.model_dir)
        )
        preprocess_s = time.perf_counter() - start
        start = time.perf_counter()
        view = make_text_view(text)
        target_tokens = self.encode_target(view)
        tokenize_s = time.perf_counter() - start
        self.frontend_metadata = frontend_metadata
        return {
            "audio": np.asarray(audio, dtype=np.float32).reshape(-1),
            "duration_s": len(audio) / int(sample_rate),
            "sample_rate": int(sample_rate),
            "features": features,
            "feature_length": int(feature_length),
            "view": view,
            "target_tokens": target_tokens,
            "preprocess_s": preprocess_s,
            "tokenize_s": tokenize_s,
            "frontend_metadata": frontend_metadata,
        }

    def infer(self, prepared: dict) -> tuple[np.ndarray, float]:
        feed = {"audio_signal": prepared["features"]}
        if "length" in self.input_meta:
            feed["length"] = np.asarray([prepared["feature_length"]], dtype=np.int64)
        start = time.perf_counter()
        output = np.asarray(self.session.run([self.output_name], feed)[0])
        inference_s = time.perf_counter() - start
        if output.ndim != 3:
            raise RuntimeError(f"expected rank-3 logits, got {output.shape}")
        if output.shape[-1] != max(self.vocab.values()) + 1:
            raise RuntimeError(f"class count {output.shape[-1]} disagrees with vocabulary")
        if output.shape[0] == 1:
            emissions = output[0]
        elif output.shape[1] == 1:
            emissions = output[:, 0, :]
        else:
            raise RuntimeError(f"cannot identify batch axis from logits shape {output.shape}")
        return np.asarray(emissions, dtype=np.float32), inference_s

    def align_prepared(self, prepared: dict, emissions: np.ndarray, anchors: list[dict]) -> dict:
        start = time.perf_counter()
        ids = [int(token["id"]) for token in prepared["target_tokens"]]
        result = ctc_viterbi(emissions, ids, self.blank_id)
        for token, score in zip(prepared["target_tokens"], result["token_mean_log_probabilities"]):
            token["mean_log_probability"] = float(score)
        frame_step_s = HOP_LENGTH / SAMPLE_RATE * self.subsampling_factor
        words = assign_word_times(
            prepared["view"], prepared["target_tokens"], result["token_spans"], frame_step_s, prepared["duration_s"]
        )
        align_s = time.perf_counter() - start
        start = time.perf_counter()
        events = []
        for anchor in anchors:
            mapped = map_anchor(anchor, prepared["view"].original, words)
            events.append({**anchor, **mapped})
        span_s = time.perf_counter() - start
        return {
            "words": words,
            "events": events,
            "frame_step_s": frame_step_s,
            "frame_count": result["frame_count"],
            "mean_token_log_probability": result["mean_token_log_probability"],
            "total_path_log_probability": result["total_path_log_probability"],
            "align_s": align_s,
            "span_s": span_s,
            "token_spans": result["token_spans"],
            "path_state_ids": result["path_state_ids"],
        }

    def align(self, audio: np.ndarray, sample_rate: int, text: str, anchor_spans: list[dict]) -> dict:
        prepared = self.prepare(audio, sample_rate, text)
        emissions, inference_s = self.infer(prepared)
        aligned = self.align_prepared(prepared, emissions, anchor_spans)
        return {
            "prepared": prepared,
            "emissions": emissions,
            "alignment": aligned,
            "inference_s": inference_s,
        }
