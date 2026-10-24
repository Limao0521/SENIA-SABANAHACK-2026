"""Thin x2-llm bridge: same `docker exec -i x2-llm python -c _LLM_BRIDGE` request shape as hriego_v2.LocalQwenRunner,
but with the gesture v2 json_schema. _LLM_BRIDGE is imported read-only from hriego_v2.v2_workers."""
from __future__ import annotations

import json
import random
import subprocess
import time

from hriego_v2.v2_workers import _LLM_BRIDGE as _BASE_BRIDGE

# same bridge + optional per-request sampling seed (llama-server returned identical text for repeated T=0.7 requests)
_LLM_BRIDGE = _BASE_BRIDGE.replace('payload = llm._payload(req["messages"], req["max_tokens"], req["temperature"], False)',
    'payload = llm._payload(req["messages"], req["max_tokens"], req["temperature"], False)\n    if req.get("seed") is not None: payload["seed"] = req["seed"]')
assert 'req.get("seed")' in _LLM_BRIDGE


def generate(settings, messages, schema, *, timeout=120, temperature=None):
    req = {"messages": messages, "max_tokens": int(settings.runtime["max_new_tokens"]),
           "temperature": float(settings.runtime["qwen_temperature"] if temperature is None else temperature), "schema": schema}
    if req["temperature"] > 0.5:
        req["seed"] = random.randint(1, 2**31 - 1)
    t0 = time.perf_counter()
    p = subprocess.run(["docker", "exec", "-i", settings.llm_container, "python", "-c", _LLM_BRIDGE],
                       input=json.dumps(req, ensure_ascii=False), capture_output=True, text=True, timeout=timeout)
    try:
        res = json.loads(p.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        return {"ok": False, "error": f"no JSON: {p.stderr[-300:]}", "raw": "", "metrics": {}, "wall_s": time.perf_counter() - t0}
    res["temperature"] = req["temperature"]
    res["seed"] = req.get("seed")
    res["wall_s"] = round(time.perf_counter() - t0, 4)
    return res
