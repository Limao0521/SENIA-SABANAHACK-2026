#!/usr/bin/env python3
"""Synthesize the test sentences with Supertonic M1 (worker protocol) and write test_manifest.jsonl.
Run with the Supertonic venv python:  experiments/hriego_v2_tts_eval_20261021/.venv/bin/python gen_test_wavs.py"""
import hashlib, json, subprocess, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
MODEL = ROOT / "experiments/hriego_v2_tts_eval_20261021/model"
# (case_id, state, speech, emote, motion_id, area, trigger_text)
CASES = [
 ("T01", "GREETING", "¡Hola! ¡Dame esos cinco! Bienvenido al laboratorio.", "HIGH_FIVE", 1008, 2, "Dame esos cinco"),
 ("T02", "GREETING", "Bienvenido, es un honor recibirle en nuestro laboratorio.", "BOW", 3001, 11, "Bienvenido"),
 ("T03", "GUIDANCE", "El laboratorio está a mi derecha.", "POINT_RIGHT", None, None, "derecha"),
 ("T04", "GUIDANCE", "La recepción está frente a mí.", "POINT_FRONT", None, None, "frente a mí"),
 ("T05", "GUIDANCE", "Lo siento, no puedo ayudarle con eso.", "SHAKE_HEAD", 3029, 11, "no puedo"),
 ("T06", "GREETING", "¡Felicidades por su ascenso!", "CLAP", 3017, 11, "Felicidades"),
 ("T07", "GUIDANCE", "Muchas gracias, me encanta su visita.", "HEART", 3004, 11, "me encanta"),
 ("T08", "GUIDANCE", "Sí, tu reserva sigue confirmada.", "NOD", 3028, 11, "Sí"),
 ("T09", "GUIDANCE", "¡Excelente idea!", "LIKE", 1005, 2, "Excelente"),
 ("T10", "FAREWELL", "Fue un placer verle, hasta pronto y buen viaje.", "WAVE", 1002, 2, "hasta pronto"),
 ("T11", "GUIDANCE", "Un momento, por favor.", "NONE", None, None, ""),
]
speeds = {"GREETING": 1.05, "GUIDANCE": 1.0, "FAREWELL": 0.98}
wavdir = HERE / "test_wavs"; wavdir.mkdir(exist_ok=True)
py = ROOT / "experiments/hriego_v2_tts_eval_20261021/.venv/bin/python"
p = subprocess.Popen([str(py), "-u", str(ROOT / "hriego_v2/supertonic_worker.py"), str(MODEL)],
                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
def reply():
    for line in p.stdout:
        if line.startswith("HRIEGO_WORKER_JSON="):
            r = json.loads(line.removeprefix("HRIEGO_WORKER_JSON="))
            assert r.get("ok"), r
            return r
rdy = reply(); assert rdy["sample_rate"] == 44100
rows = []
for cid, state, speech, emote, mid, area, trig in CASES:
    out = wavdir / f"{cid}.wav"
    p.stdin.write(json.dumps({"speech": speech, "state": state, "output": str(out)}, ensure_ascii=False) + "\n"); p.stdin.flush()
    r = reply()
    cs = speech.index(trig) if trig else None
    rows.append({"case_id": cid, "visit_id": "V_TEST", "turn_id": cid, "state": state, "speech": speech, "emote": emote,
                 "motion_id": mid, "area": area, "trigger_text": trig, "trigger_char_start": cs,
                 "trigger_char_end": cs + len(trig) if trig else None, "wav_path": out.relative_to(HERE.parent).as_posix(),  # relative to the HRIEGO_gesture root
                 "wav_sha256": hashlib.sha256(out.read_bytes()).hexdigest(), "sample_rate": r["sample_rate"],
                 "duration_s": r["duration_s"], "synth_s": r["synthesis_s"]})
p.terminate()
(HERE / "test_manifest.jsonl").write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in rows), encoding="utf-8")
print(len(rows), "rows")
