from __future__ import annotations

import json
import os
import sys
import time
import traceback
from pathlib import Path
from typing import Any


def _rss_high_water_bytes() -> int | None:
    try:
        return int(Path("/proc/self/status").read_text().split("VmHWM:", 1)[1].split()[0]) * 1024
    except (OSError, IndexError, ValueError):
        return None


def _run(request: dict[str, Any]) -> dict[str, Any]:
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    import torch
    import transformers
    from transformers import AutoModelForMultimodalLM, AutoProcessor

    if not torch.cuda.is_available():
        raise RuntimeError("Qwen worker no detecta CUDA; no se intenta ejecutar en CPU")
    model_path = str(Path(request["model_path"]).resolve())
    if not Path(model_path, "config.json").is_file():
        raise FileNotFoundError(f"Qwen local no encontrado: {model_path}")

    started = time.perf_counter()
    processor = AutoProcessor.from_pretrained(model_path, local_files_only=True)
    messages = request["messages"]
    inputs = processor.apply_chat_template(
        messages,
        add_generation_prompt=True,
        enable_thinking=False,
        tokenize=True,
        return_dict=True,
        return_tensors="pt",
    )
    prompt_tokens = int(inputs["input_ids"].shape[-1])
    budget = int(request["input_token_budget"])
    if prompt_tokens > budget:
        return {"ok": False, "error": f"Contexto excede presupuesto: {prompt_tokens}>{budget}",
                "metrics": {"prompt_tokens": prompt_tokens, "input_token_budget": budget}}

    dtype = getattr(torch, request["dtype"])
    load_started = time.perf_counter()
    model = AutoModelForMultimodalLM.from_pretrained(
        model_path,
        local_files_only=True,
        dtype=dtype,
        device_map=request["device_map"],
        max_memory={0: request["gpu_limit"], "cpu": request["cpu_limit"]},
    )
    model.eval()
    load_s = time.perf_counter() - load_started
    inputs = inputs.to(model.device)
    if torch.cuda.is_available():
        torch.cuda.synchronize()
        torch.cuda.reset_peak_memory_stats()

    generation_started = time.perf_counter()
    generated_ids = model.generate(
        **inputs,
        max_new_tokens=int(request["max_new_tokens"]),
        do_sample=False,
        use_cache=True,
    )
    if torch.cuda.is_available():
        torch.cuda.synchronize()
    generation_s = time.perf_counter() - generation_started
    new_ids = generated_ids[0][prompt_tokens:]
    raw = processor.decode(new_ids, skip_special_tokens=True).strip()
    return {
        "ok": True,
        "raw": raw,
        "metrics": {
            "model_id": request["model_id"],
            "model_revision": request["model_revision"],
            "python": sys.version.split()[0],
            "torch": torch.__version__,
            "transformers": transformers.__version__,
            "cuda_version": torch.version.cuda,
            "device": torch.cuda.get_device_name(0),
            "load_s": round(load_s, 4),
            "generation_s": round(generation_s, 4),
            "ttft_ms": None,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": int(new_ids.shape[-1]),
            "tokens_per_second": round(int(new_ids.shape[-1]) / generation_s, 3) if generation_s else None,
            "gpu_peak_allocated_bytes": int(torch.cuda.max_memory_allocated()),
            "gpu_peak_reserved_bytes": int(torch.cuda.max_memory_reserved()),
            "process_rss_peak_bytes": _rss_high_water_bytes(),
            "wall_s": round(time.perf_counter() - started, 4),
            "max_new_tokens": int(request["max_new_tokens"]),
            "dtype": request["dtype"],
        },
    }


def main() -> int:
    try:
        request = json.load(sys.stdin)
        try:
            result = _run(request)
        except Exception as exc:
            result = {"ok": False, "error": f"{type(exc).__name__}: {exc}",
                      "traceback": traceback.format_exc(limit=8), "metrics": {}}
        print(json.dumps(result, ensure_ascii=False), flush=True)
        return 0 if result.get("ok") else 2
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}), flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
