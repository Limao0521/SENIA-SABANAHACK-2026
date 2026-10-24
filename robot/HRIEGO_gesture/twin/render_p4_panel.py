"""P4: render ONE panel (960x1080, 30 fps) of the split-screen from traces/<robot>_r<rep>_qpos.npz on the shared conversation clock.
  X2_URDF_ROOT=~/robots/agibot_x2_urdf PYTHONPATH=<teleop>/src MUJOCO_GL=egl python render_p4_panel.py ROBOT REP [--test C0,C1,...]   (--test: PNG stills at those clock times only)"""
import csv, json, subprocess, sys
from pathlib import Path
import mujoco, numpy as np
from PIL import Image, ImageDraw, ImageFont
from x2_ultra.twin.model import build_twin_model
H = Path(__file__).resolve().parent; O = H / "runs/p4_commercial"
rb, rep = sys.argv[1], sys.argv[2]; test = [float(x) for x in sys.argv[4].split(",")] if len(sys.argv) > 4 and sys.argv[3] == "--test" else None
W, HT, FPS = 960, 1080, 30
fnt = lambda s, b=False: ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans%s.ttf" % ("-Bold" if b else ""), s)
F20, F26, F34 = fnt(20), fnt(26), fnt(34, True)
Z = np.load(O / f"traces/{rb}_r{rep}_qpos.npz"); qpos, cc = Z["qpos"], Z["c"]; T = json.load(open(O / f"timeline_{rb}.json")); name = "X2 Ultra" if rb == "ultra" else "X2 Original"
meta = json.load(open(O / f"traces/{rb}_r{rep}.json")); P = meta["presets"]; lines = T["lines"]; C_END = T["C_END"]
rows = list(csv.DictReader(open(O / f"traces/{rb}_r{rep}.csv"))); hc = np.array([float(r["audio_s"]) if r["audio_s"] not in ("", "None") else np.nan for r in rows]); hy = np.array([float(r["wmeas_turn_head"]) if r["wmeas_turn_head"] not in ("", "None") else np.nan for r in rows])
model = build_twin_model("vacia"); model.vis.global_.offwidth, model.vis.global_.offheight = W, HT
data = mujoco.MjData(model); renderer = mujoco.Renderer(model, HT, W); pel = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "pelvis")
w0, x0, y0, z0 = qpos[0][3:7]; yaw0 = np.degrees(np.arctan2(2 * (w0 * z0 + x0 * y0), 1 - 2 * (y0 * y0 + z0 * z0)))
cam = mujoco.MjvCamera(); cam.type = mujoco.mjtCamera.mjCAMERA_FREE; cam.distance, cam.elevation, cam.azimuth = 2.5, -6.0, 160.0 + yaw0
data.qpos[:] = qpos[0]; mujoco.mj_forward(model, data); cam.lookat[:] = [data.xpos[pel][0], data.xpos[pel][1], 0.72]
def wrap(dr, text, font, width):
    out, cur = [], ""
    for w in text.split():
        if dr.textlength((cur + " " + w).strip(), font=font) > width: out.append(cur); cur = w
        else: cur = (cur + " " + w).strip()
    return out + [cur]
def frame(c):
    i = int(np.clip(np.searchsorted(cc, c), 0, len(cc) - 1)); data.qpos[:] = qpos[i]; mujoco.mj_forward(model, data)
    renderer.update_scene(data, cam); img = Image.fromarray(renderer.render()); dr = ImageDraw.Draw(img, "RGBA")
    spk = next((l for l in lines if l["start"] <= c <= l["end"]), None); mine = spk is not None and spk["speaks"]
    dr.rectangle([0, 0, W, 130], fill=(0, 0, 0, 170)); dr.rectangle([0, HT - 190, W, HT], fill=(0, 0, 0, 170))
    if mine: dr.rectangle([0, 0, W - 1, HT - 1], outline=(70, 220, 110, 255), width=12)
    dr.text((24, 14), name, font=F34, fill=(255, 255, 255)); dr.text((24, 62), "SPEAKING" if mine else "listening", font=F26, fill=(70, 220, 110) if mine else (170, 170, 170))
    dr.text((W - 220, 18), f"t = {c:6.1f} s", font=F26, fill=(230, 230, 230))
    # emote label: from dispatch to preset idle
    for p in P:
        if p["dispatch"] - 0.0 <= c <= (p.get("idle_audio_s") or p["dispatch"] + p["preset_dur_s"]):
            dr.text((24, 98), "EMOTE: " + p["label"], font=F20, fill=(130, 200, 255)); break
    if rb == "ultra":
        k = int(np.clip(np.searchsorted(np.nan_to_num(hc, nan=-1e9), c), 0, len(hy) - 1)); hv = hy[k]
        if not np.isnan(hv): dr.text((W - 400, 62), f"head yaw {hv:+.2f} rad" + (" (to Original)" if hv > 0.2 else ""), font=F20, fill=(255, 220, 140))
    # CTC anchor markers
    for p in P:
        if p["anchor_abs"] - 0.15 <= c <= p["anchor_abs"] + 0.8:
            dr.rectangle([0, 132, W, 178], fill=(255, 190, 0, 215)); dr.text((24, 140), f"CTC ANCHOR (stroke target): “{p['anchor_text']}”  t = {p['anchor_abs']:.2f} s", font=F20, fill=(0, 0, 0)); break
    y = HT - 180
    if spk is not None:
        who = "X2 Ultra" if spk["robot"] == "ultra" else "X2 Original"; dr.text((24, y), f"Línea {spk['n']:03d}  ·  {who}", font=F20, fill=(70, 220, 110) if mine else (200, 200, 200)); y += 28
        for ln in wrap(dr, spk["text"], F20, W - 48)[:5]: dr.text((24, y), ln, font=F20, fill=(255, 255, 255) if mine else (190, 190, 190)); y += 26
        bx = 24 + (W - 48) * np.clip((c - spk["start"]) / spk["dur"], 0, 1); dr.rectangle([24, HT - 14, W - 24, HT - 8], fill=(90, 90, 90, 255)); dr.rectangle([24, HT - 14, bx, HT - 8], fill=(80, 180, 255, 255))
    return img
if test is not None:
    for c in test: frame(c).save(O / f"video/test_{rb}_{c:.1f}.png")
    sys.exit(0)
tu = np.arange(0, C_END, 1 / FPS); out = O / f"video/panel_{rb}.mp4"
ff = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{HT}", "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "22", str(out)], stdin=subprocess.PIPE)
for c in tu: ff.stdin.write(np.asarray(frame(c)).tobytes())
ff.stdin.close(); assert ff.wait() == 0; print("OK", out, len(tu))
