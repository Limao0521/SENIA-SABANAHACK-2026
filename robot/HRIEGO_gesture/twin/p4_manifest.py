"""P4: build the CTC manifest for the 3 emote lines of the commercial show (guard speech[start:end]==trigger_text) and run anchor_plan.py (physicalai env)."""
import sys as _sys, pathlib as _pl; _sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
from paths import laplace_root as _lr, semantic_corporea_root as _sc, py_physicalai as _pyp, conda_exe as _conda, commercial_show_dir; _LR = _lr(); _SC = _sc()
import csv, json, hashlib, subprocess, wave
from pathlib import Path
S = commercial_show_dir(); H = Path(__file__).resolve().parent; O = H / "runs/p4_commercial"
rows = {r["orden"]: r for r in csv.DictReader(open(S / "orden_de_pistas.csv", encoding="utf-8-sig"))}
EM = [("001", "Hola", 3001, 11), ("005", "nosotros somos un ejemplo", 3004, 11), ("019", "despedirnos", 3031, 11)]
out = []
for o, trig, mid, area in EM:
    r = rows[o]; sp = r["parlamento"]; cs = sp.index(trig); ce = cs + len(trig); assert sp[cs:ce] == trig
    wav = S / "audio" / r["archivo"]; w = wave.open(str(wav))
    out.append(dict(case_id=f"p4_{o}", turn_id=f"p4_{o}", emote="P4_" + trig, motion_id=mid, area=area, speech=sp, trigger_text=trig, trigger_char_start=cs, trigger_char_end=ce,
                    wav_path=str(wav), wav_sha256=hashlib.sha256(wav.read_bytes()).hexdigest(), sample_rate=w.getframerate(), duration_s=w.getnframes() / w.getframerate()))
(O / "plans").mkdir(parents=True, exist_ok=True)
with open(O / "plans/anchor_manifest.jsonl", "w") as f:
    for x in out: f.write(json.dumps(x, ensure_ascii=False) + "\n")
r = subprocess.run([_pyp(), str(H.parent / "align/anchor_plan.py"), "--manifest", str(O / "plans/anchor_manifest.jsonl"), "--out", str(O / "plans/anchor"), "--align", "stroke"], capture_output=True, text=True)
print(r.stdout[-800:], r.stderr[-1500:])
