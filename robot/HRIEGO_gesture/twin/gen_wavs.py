#!/usr/bin/env python3
"""4aa: Supertonic M1 WAVs for the pointing sentences + manifest for align/anchor_plan.py (trigger = the direction word).
Run with the Supertonic venv python."""
import hashlib, json, subprocess
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]; HERE = Path(__file__).resolve().parent
OUT = HERE / "runs/4aa_pointing"; (OUT / "wav").mkdir(parents=True, exist_ok=True)
MODEL = ROOT / "experiments/hriego_v2_tts_eval_20261021/model"
CASES = [("RIGHT", "El laboratorio está a mi derecha.", "derecha"),
         ("LEFT", "La sala de reuniones está a mi izquierda.", "izquierda"),
         ("FRONT", "La recepción está frente a mí.", "frente")]
py = ROOT / "experiments/hriego_v2_tts_eval_20261021/.venv/bin/python"
p = subprocess.Popen([str(py), "-u", str(ROOT / "hriego_v2/supertonic_worker.py"), str(MODEL)],
                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
def reply():
    for line in p.stdout:
        if line.startswith("HRIEGO_WORKER_JSON="):
            r = json.loads(line.removeprefix("HRIEGO_WORKER_JSON=")); assert r.get("ok"), r; return r
assert reply()["sample_rate"] == 44100
rows = []
for d, speech, trig in CASES:
    out = OUT / "wav" / f"P_{d}.wav"
    p.stdin.write(json.dumps({"speech": speech, "state": "GUIDANCE", "output": str(out)}, ensure_ascii=False) + "\n"); p.stdin.flush()
    r = reply(); cs = speech.index(trig)
    rows.append({"case_id": f"P_{d}", "visit_id": "V_4AA", "turn_id": d, "state": "GUIDANCE", "speech": speech, "emote": f"POINT_{d}",
                 "motion_id": None, "area": None, "trigger_text": trig, "trigger_char_start": cs, "trigger_char_end": cs + len(trig),
                 "wav_path": str(out), "wav_sha256": hashlib.sha256(out.read_bytes()).hexdigest(), "sample_rate": r["sample_rate"],
                 "duration_s": r["duration_s"], "synth_s": r["synthesis_s"]})
p.terminate()
(OUT / "wav/manifest.jsonl").write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in rows), encoding="utf-8")
print(len(rows), "rows")
