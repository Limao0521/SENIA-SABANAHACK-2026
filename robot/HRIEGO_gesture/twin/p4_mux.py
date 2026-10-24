"""P4: hstack panel_ultra | panel_original and mux the 19 WAVs placed at t_k (conversation clock) -> video/commercial_split.mp4"""
import sys as _sys, pathlib as _pl; _sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
from paths import laplace_root as _lr, semantic_corporea_root as _sc, py_physicalai as _pyp, conda_exe as _conda, commercial_show_dir; _LR = _lr(); _SC = _sc()
import json, subprocess
from pathlib import Path
H = Path(__file__).resolve().parent; O = H / "runs/p4_commercial"; V = O / "video"
S = commercial_show_dir() / "audio"
cl = json.load(open(O / "conversation_clock.json")); L = cl["lines"]
inp = ["-i", str(V / "panel_ultra.mp4"), "-i", str(V / "panel_original.mp4")]; filt = ["[0:v][1:v]hstack=inputs=2[v]"]
for i, l in enumerate(L): inp += ["-i", str(S / (l["clip"] + ".wav"))]; ms = int(round(l["start"] * 1000)); filt.append(f"[{i + 2}:a]aresample=48000,adelay={ms}:all=1[a{i}]")
filt.append("".join(f"[a{i}]" for i in range(len(L))) + f"amix=inputs={len(L)}:normalize=0:duration=longest[aout]")
subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *inp, "-filter_complex", ";".join(filt), "-map", "[v]", "-map", "[aout]", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "22", "-c:a", "aac", "-b:a", "160k", "-t", f"{cl['C_END']:.2f}", str(V / "commercial_split.mp4")], check=True)
print("OK", V / "commercial_split.mp4")
