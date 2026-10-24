"""IndexTTS worker using Laplace's adapter without its unused emotion LLM."""

from __future__ import annotations

import contextlib
import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any


@contextlib.contextmanager
def _redirect_fd_stdout_to_stderr():
    sys.stdout.flush()
    saved_stdout = os.dup(1)
    try:
        os.dup2(2, 1)
        yield
    finally:
        sys.stdout.flush()
        os.dup2(saved_stdout, 1)
        os.close(saved_stdout)


def _load_checkpoint_mmap(model, model_path: str):
    """Load Laplace's large GPT checkpoint from a file mapping, avoiding a CPU copy."""
    import gc
    import torch

    checkpoint = torch.load(model_path, map_location="cpu", mmap=True, weights_only=False)
    state = checkpoint["model"] if isinstance(checkpoint, dict) and "model" in checkpoint else checkpoint
    missing, unexpected = model.load_state_dict(state, strict=False, assign=True)
    if missing:
        print(f">> load_checkpoint: missing keys ({len(missing)}): {missing[:5]}")
    if unexpected:
        print(f">> load_checkpoint: skipping unexpected keys ({len(unexpected)}): {unexpected[:5]}")
    del state, checkpoint
    gc.collect()
    info_path = str(Path(model_path).with_suffix(".yaml"))
    if os.path.exists(info_path):
        import yaml
        with open(info_path, "r", encoding="utf-8") as stream:
            return yaml.load(stream, Loader=yaml.FullLoader) or {}
    return {}


def _build_backend(config: dict[str, Any]):
    from hri_voice.backends.index_tts_backend import IndexTTS25Backend, IndexTTSConfig
    import indextts.infer_v2_5 as index_impl

    use_qwen_emo = bool(config.pop("use_qwen_emo", False))
    if use_qwen_emo:
        raise ValueError("HRIEGO keeps QwenEmotion unloaded when using the fixed emotion vector")
    index_impl.load_checkpoint = _load_checkpoint_mmap
    for key in ("model_dir", "reference_audio", "cfg_path"):
        if config.get(key) is not None:
            config[key] = Path(config[key])
    backend_config = IndexTTSConfig(**config)
    backend = IndexTTS25Backend(backend_config)
    if not backend_config.model_dir.is_dir():
        raise FileNotFoundError(f"IndexTTS model directory not found: {backend_config.model_dir}")
    if not backend_config.reference_audio.is_file():
        raise FileNotFoundError(f"Authorized speaker reference not found: {backend_config.reference_audio}")
    cfg_path = backend_config.cfg_path or backend_config.model_dir / "config.yaml"
    if not cfg_path.is_file():
        raise FileNotFoundError(f"IndexTTS config not found: {cfg_path}")

    # The selected Laplace preset supplies a fixed calm vector and sets
    # use_emo_text=False, so QwenEmotion is not consulted by infer(). Skipping
    # its 1.2 GB checkpoint preserves the exact voice/vector while lowering RAM
    # and VRAM peaks on this 12 GB laptop GPU.
    backend.tts = index_impl.IndexTTS2(
        cfg_path=str(cfg_path),
        model_dir=str(backend_config.model_dir),
        use_bf16=backend_config.use_bf16,
        use_qwen_emo=use_qwen_emo,
    )
    backend.model_revision = backend_config.model_revision
    return backend


def main() -> int:
    protocol_out = sys.stdout
    backend = None
    for raw_line in sys.stdin:
        try:
            request = json.loads(raw_line)
            operation = request.get("op")
            if operation == "load":
                if backend is not None:
                    raise RuntimeError("IndexTTS worker is already loaded")
                with _redirect_fd_stdout_to_stderr(), contextlib.redirect_stdout(sys.stderr):
                    backend = _build_backend(dict(request["config"]))
                result = {"model_id": backend.model_id, "model_revision": backend.model_revision,
                          "qwen_emotion_loaded": False}
            elif operation == "synthesize":
                if backend is None:
                    raise RuntimeError("call load before synthesis")
                from hri_voice.domain import SynthesisInput
                stimulus = SynthesisInput.from_mapping(request["stimulus"])
                with _redirect_fd_stdout_to_stderr(), contextlib.redirect_stdout(sys.stderr):
                    audio = backend.synthesize(
                        stimulus, Path(request["output_path"]),
                        instruct=request.get("instruct"), seed=request.get("seed"),
                    )
                result = {
                    "path": str(audio.path), "sample_rate": audio.sample_rate,
                    "duration_s": audio.duration_s, "model_id": audio.model_id,
                    "model_revision": audio.model_revision, "metadata": audio.metadata,
                }
            elif operation == "close":
                with _redirect_fd_stdout_to_stderr(), contextlib.redirect_stdout(sys.stderr):
                    if backend is not None:
                        backend.unload()
                backend = None
                protocol_out.write(json.dumps({"ok": True, "result": {}}) + "\n")
                protocol_out.flush()
                return 0
            else:
                raise ValueError(f"unsupported operation {operation!r}")
            response = {"ok": True, "result": result}
        except Exception as exc:
            response = {"ok": False, "error": f"{type(exc).__name__}: {exc}",
                        "traceback": traceback.format_exc(limit=12)}
        protocol_out.write(json.dumps(response, ensure_ascii=False) + "\n")
        protocol_out.flush()
    if backend is not None:
        backend.unload()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
