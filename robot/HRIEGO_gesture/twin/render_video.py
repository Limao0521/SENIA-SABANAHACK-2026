"""4aa video: twin qpos of a take (rep 1) rendered with MuJoCo; overlay speech text, direction, phase, audio clock, anchor marker, target-direction compass;
the WAV is muxed at its audio start (T0). env laplace:
  X2_URDF_ROOT=~/robots/agibot_x2_urdf PYTHONPATH=<x2_ultra-real-robot-teleop>/src MUJOCO_GL=egl python render_video.py READY_RIGHT [SONIC_LEFT ...]"""
import csv, json, subprocess, sys
from pathlib import Path
import cv2, mujoco, numpy as np
from x2_ultra.twin.model import build_twin_model
R = Path(__file__).resolve().parent / "runs/4aa_pointing"; W, H, FPS = 1280, 720, 50
SPEECH = {json.loads(l)["turn_id"]: json.loads(l) for l in open(R / "wav/manifest.jsonl")}
COL = {"BASE": (200, 200, 200), "PREP": (255, 200, 60), "HOLD": (80, 255, 120), "RECOVER": (255, 140, 80), "ramp": (160, 160, 160), "settle": (160, 160, 160)}
ARROW = {"RIGHT": (1, 0), "LEFT": (-1, 0), "FRONT": (0, -1)}   # screen direction of the compass (robot forward = up)
def main(tid):
    tag, D = tid.split("_"); rec = np.load(R / f"video/{tid}_r1_qpos.npz"); qpos = rec["qpos"]; tp = rec["t_pub"]
    rows = list(csv.DictReader(open(R / f"traces/{tid}_r1.csv"))); meta = json.load(open(R / f"traces/{tid}_r1.json"))
    plan = json.load(open(R / f"plans/{D}.point.json")); fs = plan["sample_rate_hz"]
    a0, a1 = plan["anchor_start_sample"] / fs, plan["anchor_end_sample"] / fs; dur = plan["sample_count"] / fs
    ta = np.array([float(r["audio_s"]) for r in rows]); T0 = meta["T0"]; text = SPEECH[D]["speech"]
    k0 = int(np.argmax(ta >= -0.8)); k1 = int(np.argmax(ta >= max(dur, meta["recovery_end_s"]) + 0.6)) or len(ta)
    model = build_twin_model("vacia"); model.vis.global_.offwidth, model.vis.global_.offheight = W, H
    data = mujoco.MjData(model); renderer = mujoco.Renderer(model, H, W)
    cam = mujoco.MjvCamera(); cam.type, cam.distance, cam.elevation = mujoco.mjtCamera.mjCAMERA_FREE, 2.0, -10.0
    pelvis = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "pelvis")
    w_, x, y, z = qpos[0][3:7]; yaw = np.degrees(np.arctan2(2 * (w_ * z + x * y), 1 - 2 * (y * y + z * z))); cam.azimuth = yaw + 150.0
    tmp, out = R / f"video/{tid}_silent.mp4", R / f"video/{tid}.mp4"
    ff = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
                           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20", str(tmp)], stdin=subprocess.PIPE)
    font = cv2.FONT_HERSHEY_SIMPLEX
    for k in range(k0, min(k1, len(qpos))):
        t = ta[k]; data.qpos[:] = qpos[k]; mujoco.mj_forward(model, data)
        cam.lookat[:] = data.xpos[pelvis] + np.array([0.0, 0.0, 0.35]); renderer.update_scene(data, cam)
        img = np.ascontiguousarray(renderer.render()); img[:112] = (img[:112] * 0.35).astype(np.uint8); img[H - 36:] = (img[H - 36:] * 0.35).astype(np.uint8)
        ph = rows[k]["phase"]; w = float(rows[k]["w"])
        cv2.putText(img, f"\"{text}\"", (20, 34), font, 0.8, (255, 255, 255), 2, cv2.LINE_AA)
        cv2.putText(img, f"direction {D}   base {tag}   audio clock t = {t:+.2f} s   speech {'PLAYING' if 0 <= t <= dur else '-'}", (20, 66), font, 0.6, (255, 255, 255), 1, cv2.LINE_AA)
        # speech progress bar with the anchor window marked
        bx0, bx1, by = 20, 760, 92; sc = (bx1 - bx0) / (dur + 1.0); cv2.rectangle(img, (bx0, by - 8), (bx1, by + 8), (90, 90, 90), 1)
        cv2.rectangle(img, (bx0 + int(a0 * sc), by - 8), (bx0 + int(a1 * sc), by + 8), (255, 200, 0), -1)
        cv2.line(img, (bx0 + int(a0 * sc), by - 14), (bx0 + int(a0 * sc), by + 14), (255, 255, 0), 3)
        cv2.putText(img, "anchor_start", (bx0 + int(a0 * sc) - 40, by - 18), font, 0.45, (255, 255, 0), 1, cv2.LINE_AA)
        cv2.line(img, (bx0 + int(max(t, 0) * sc), by - 12), (bx0 + int(max(t, 0) * sc), by + 12), (255, 255, 255), 2)
        cv2.putText(img, f"{ph}  w={w:.2f}", (800, 40), font, 0.9, COL.get(ph, (255, 255, 255)), 2, cv2.LINE_AA)
        if abs(t - a0) < 0.03: cv2.rectangle(img, (0, 0), (W - 1, H - 1), (255, 255, 0), 8); cv2.putText(img, "<< anchor_start >>", (W // 2 - 150, H // 2 - 200), font, 1.1, (255, 255, 0), 3, cv2.LINE_AA)
        elif a0 <= t <= a1: cv2.rectangle(img, (0, 0), (W - 1, H - 1), (255, 200, 0), 8)
        if ph == "HOLD" and abs(t - meta["stroke_s"]) < 0.03: cv2.putText(img, "STROKE", (W // 2 - 70, H // 2 - 160), font, 1.1, (80, 255, 120), 3, cv2.LINE_AA)
        # compass (robot frame, forward = up) with the target direction
        cx, cy, rr = W - 90, 90, 55; cv2.circle(img, (cx, cy), rr, (200, 200, 200), 1); cv2.putText(img, "fwd", (cx - 14, cy - rr - 6), font, 0.45, (200, 200, 200), 1, cv2.LINE_AA)
        dx, dy = ARROW[D]; cv2.arrowedLine(img, (cx, cy), (cx + int(dx * rr), cy + int(dy * rr)), (80, 255, 120) if ph == "HOLD" else (255, 255, 255), 4, tipLength=0.3)
        cv2.putText(img, "MuJoCo twin (MC v1.0.0), virtual TTS playback (Supertonic M1 WAV muxed) - simulation, not the physical robot", (20, H - 14), font, 0.5, (230, 230, 230), 1, cv2.LINE_AA)
        ff.stdin.write(img.tobytes())
    ff.stdin.close(); assert ff.wait() == 0
    ms = int(round((T0 - tp[k0]) * 1000))
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(tmp), "-i", str(R / f"wav/P_{D}.wav"), "-filter_complex", f"[1:a]adelay={ms}|{ms}[a]",
                    "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-shortest", str(out)], check=True)
    tmp.unlink(); print(json.dumps({"video": str(out), "audio_offset_ms": ms}))
if __name__ == "__main__":
    for n in sys.argv[1:]: main(n)
