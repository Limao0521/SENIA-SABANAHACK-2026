"""4e: MP4 of one mission (walk -> arc -> walk -> stop -> settle -> 6 visit turns) from traces/m_<rank>_{log.csv,qpos.npz}, walk events and per-turn jsons.
Chase/side camera while walking, framed upper-body (frontal) during the interaction. Audio = the turn WAVs placed at their real audio-start times.
  X2_URDF_ROOT=~/robots/agibot_x2_urdf PYTHONPATH=<teleop>/src MUJOCO_GL=egl python render_4e_video.py RANK
"""
import csv, json, subprocess, sys
from pathlib import Path
import mujoco, numpy as np
from PIL import Image, ImageDraw, ImageFont
from x2_ultra.twin.model import build_twin_model
H = Path(__file__).resolve().parent; R = H / "runs/4e_missions"; V = H.parent / "visits_v2"
rk = sys.argv[1]; NAME = f"m_{rk}"; W, HT, FPS = 1280, 720, 30
fnt = lambda s: ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", s)
F18, F22, F26 = fnt(18), fnt(22), fnt(26)
L = list(csv.DictReader(open(R / f"traces/{NAME}_log.csv"))); Z = np.load(R / f"traces/{NAME}_qpos.npz"); qpos = Z["qpos"]; t = Z["t"]
f_ = lambda k: np.array([float(r[k]) if r[k] not in ("", "None") else np.nan for r in L])
mode = [r["mode"] for r in L]; z_, vx_, wz_ = f_("z"), f_("vx"), f_("wz")
ev = {}
for e in json.load(open(R / f"traces/{NAME}_walk_events.json")): ev[e["event"] + (":" + e["name"] if e["event"] == "phase" else "")] = e["t"]
runs = {}
for l in open(R / "logs/turn_runs.jsonl"):
    d = json.loads(l)
    if d["rank"] == rk: runs[d["turn"]] = d
turns = []
for l in open(V / rk / "tts_manifest.jsonl"):
    m = json.loads(l); tid = m["turn_id"]; j = json.load(open(R / f"traces/{tid}_r1.json")); run = runs[tid]
    if "audio_start_monotonic" in j: a0 = j["audio_start_monotonic"]
    elif "T0" in j: a0 = j["T0"]
    else: a0 = j["t0_abs"] + j["ramp_ticks"] * 0.02
    kind = run["kind"]; anc = None
    ep = V / rk / f"anchor_plan/events/{tid}.event.json"; ap_ = V / rk / f"anchor_plan/events/{tid}.anchor.json"
    if kind == "PRESET": e = json.load(open(ep)); anc = (e["anchor"]["start_sample"] / e["audio"]["sample_rate_hz"], e["anchor"]["text"])
    elif kind == "POINT": e = json.load(open(ap_)); anc = (e["start_sample"] / e["sample_rate_hz"], e["text"])
    turns.append(dict(id=tid, idx=len(turns), state=m["state"], speech=m["speech"], emote=m["emote"], kind=kind, wav=m["wav_path"], dur=m["duration_s"], a0=a0, anchor=anc, t0=run["t_start_mono"], t1=run["t_end_mono"]))
t_start = t[0]; tu = np.arange(t_start, t[-1], 1 / FPS); idx = np.clip(np.searchsorted(t, tu), 0, len(t) - 1)
model = build_twin_model("vacia"); model.vis.global_.offwidth, model.vis.global_.offheight = W, HT
data = mujoco.MjData(model); renderer = mujoco.Renderer(model, HT, W)
pel = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "pelvis")
Q = qpos[:, 3:7]; yaw = np.unwrap(np.arctan2(2 * (Q[:, 0] * Q[:, 3] + Q[:, 1] * Q[:, 2]), 1 - 2 * (Q[:, 2] ** 2 + Q[:, 3] ** 2)))
out = R / f"video/{NAME}_mission.mp4"
inputs, filt = [], []
for i, T in enumerate(turns):
    inputs += ["-i", T["wav"]]; filt.append(f"[{i + 1}:a]adelay={int(round((T['a0'] - t_start) * 1000))}:all=1[a{i}]")
filt.append("".join(f"[a{i}]" for i in range(len(turns))) + f"amix=inputs={len(turns)}:normalize=0:duration=longest[aout]")
ff = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{HT}", "-r", str(FPS), "-i", "-", *inputs,
                       "-filter_complex", ";".join(filt), "-map", "0:v", "-map", "[aout]", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "22", "-c:a", "aac", "-b:a", "160k",
                       "-t", f"{tu[-1] - tu[0]:.2f}", str(out)], stdin=subprocess.PIPE)
def wrap(draw, text, font, width):
    words, lines, cur = text.split(), [], ""
    for w in words:
        if draw.textlength((cur + " " + w).strip(), font=font) > width: lines.append(cur); cur = w
        else: cur = (cur + " " + w).strip()
    return lines + [cur]
def phase_at(ts):
    if ts < ev["phase:walk1"]: return "INIT", ""
    if ts < ev["phase:arc"]: return "WALK", "straight, vx 0.4 m/s"
    if ts < ev["phase:walk2"]: return "ARC", "turning while walking (vx > 0 with wz)"
    if ts < ev["phase:stop"]: return "WALK", "straight again"
    if ts < ev["stable"] + 1.0: return "STOP", "MC returning to STABLE"
    return "SETTLE", "STABLE, waiting for the first turn"
cam = mujoco.MjvCamera(); cam.type = mujoco.mjtCamera.mjCAMERA_FREE
S = {"yaw": None, "dist": 3.4, "el": -12.0, "azo": 90.0, "lz": 0.2}
first_turn = turns[0]["t0"]
xs, ys = f_("x"), f_("y"); wm = t < first_turn; xo, yo = xs[wm], ys[wm]
span = max(np.ptp(xo), np.ptp(yo)) + 0.6; cx0, cy0 = (xo.min() + xo.max()) / 2, (yo.min() + yo.max()) / 2
MM = 230; mx0, my0 = W - MM - 20, 104
def mp(x, y): return (mx0 + MM / 2 + (x - cx0) / span * MM, my0 + MM / 2 - (y - cy0) / span * MM)
for k, i in enumerate(idx):
    ts = tu[k]; data.qpos[:] = qpos[i]; mujoco.mj_forward(model, data)
    S["yaw"] = yaw[i] if S["yaw"] is None else S["yaw"] + 0.06 * (yaw[i] - S["yaw"])
    walking = ts < first_turn - 0.5
    tgt = (3.6, -12.0, 90.0, 0.15) if walking else (2.9, -6.0, 160.0, 0.45)
    for key, v in zip(("dist", "el", "azo", "lz"), tgt): S[key] += 0.04 * (v - S[key])
    cam.distance, cam.elevation, cam.azimuth = S["dist"], S["el"], np.degrees(S["yaw"]) + S["azo"]
    cam.lookat[:] = data.xpos[pel] + np.array([0.0, 0.0, S["lz"]])
    renderer.update_scene(data, cam); img = Image.fromarray(renderer.render()); dr = ImageDraw.Draw(img, "RGBA")
    dr.rectangle([0, 0, W, 92], fill=(0, 0, 0, 150)); dr.rectangle([0, HT - 150, W, HT], fill=(0, 0, 0, 150))
    cur = next((T for T in turns if T["t0"] - 0.3 <= ts <= T["t1"]), None)
    ph, sub = phase_at(ts)
    if ts >= ev["stable"] + 1.0 and ts < first_turn - 0.1: ph, sub = "SETTLE", "STABLE, waiting for the first turn"
    if cur is not None: ph, sub = f"TURN {cur['idx'] + 1}/6  {cur['state']}", f"mode {mode[i]}"
    elif ts >= first_turn: ph, sub = "TRANSITION", "arms/waist reset between turns"
    col = {"ARC": (255, 200, 60), "WALK": (120, 220, 120), "STOP": (255, 130, 100)}.get(ph, (255, 255, 255))
    dr.text((20, 10), f"{rk.upper()}  |  {ph}", font=F26, fill=col); dr.text((20, 48), f"{sub}   |   t {ts - t_start:5.1f} s   pelvis z {z_[i]:.3f} m   MC {mode[i]}   odom vx {vx_[i]:.2f} m/s  wz {wz_[i]:.2f} rad/s", font=F18, fill=(230, 230, 230))
    if ts < first_turn - 0.3:
        dr.rectangle([mx0, my0, mx0 + MM, my0 + MM], fill=(0, 0, 0, 140)); n = np.searchsorted(t, ts)
        pts = [mp(xs[j], ys[j]) for j in range(0, n, 5)]
        if len(pts) > 1: dr.line(pts, fill=(255, 200, 60, 255), width=3)
        px, py = mp(xs[i], ys[i]); dr.ellipse([px - 6, py - 6, px + 6, py + 6], fill=(255, 255, 255, 255))
        hx, hy = np.cos(S["yaw"]), np.sin(S["yaw"]); dr.line([px, py, px + 22 * hx, py - 22 * hy], fill=(120, 220, 120, 255), width=3)
        dr.text((mx0 + 6, my0 + MM - 24), "top view (odometry)", font=F18, fill=(230, 230, 230))
    y = HT - 144
    if cur is not None:
        dur_end = cur["a0"] + cur["dur"]; speaking = cur["a0"] <= ts <= dur_end + 0.2
        gest = {"PRESET": f"GESTURE: preset {cur['emote']}", "POINT": f"GESTURE: pointing {cur['emote'][6:]} (arm + head, SONIC base)", "NONE": "GESTURE: NONE -> SONIC co-speech"}[cur["kind"]]
        dr.text((20, y), gest, font=F22, fill=(130, 200, 255)); y += 30
        if speaking or ts < cur["a0"] + 0.3:
            for ln in wrap(dr, "“" + cur["speech"] + "”" if speaking else "(waiting for audio start)", F22, W - 40)[:2]: dr.text((20, y), ln, font=F22, fill=(255, 255, 255)); y += 28
        if cur["anchor"] is not None:
            at = cur["a0"] + cur["anchor"][0]
            if at - 0.15 <= ts <= at + 0.7: dr.rectangle([0, 92, W, 130], fill=(255, 190, 0, 200)); dr.text((20, 98), f"CTC ANCHOR START: “{cur['anchor'][1]}”  (t = {cur['anchor'][0]:.2f} s into the audio)", font=F22, fill=(0, 0, 0))
        bx0, bx1, by = 20, W - 20, HT - 22                       # audio progress bar with anchor tick
        dr.rectangle([bx0, by, bx1, by + 8], fill=(90, 90, 90, 255))
        p = np.clip((ts - cur["a0"]) / cur["dur"], 0, 1); dr.rectangle([bx0, by, bx0 + (bx1 - bx0) * p, by + 8], fill=(80, 180, 255, 255))
        if cur["anchor"] is not None:
            ax = bx0 + (bx1 - bx0) * cur["anchor"][0] / cur["dur"]; dr.rectangle([ax - 2, by - 8, ax + 2, by + 16], fill=(255, 190, 0, 255))
    ff.stdin.write(np.asarray(img).tobytes())
ff.stdin.close()
if ff.wait() != 0: sys.exit("ffmpeg failed")
json.dump({"video": str(out), "frames": len(idx), "fps": FPS, "audio_starts_s": {T["id"]: round(T["a0"] - t_start, 3) for T in turns}}, open(R / f"video/{NAME}_video.json", "w"), indent=1)
print("OK", out, len(idx))
