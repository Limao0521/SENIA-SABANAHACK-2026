"""Graba un vídeo de demostración sin ventana (MuJoCo fuera de pantalla + imageio/ffmpeg).

    python -m x2_ultra.tools.record docs/media/demo.mp4
    python -m x2_ultra.tools.record demo.mp4 --policy agibot --width 960 --height 540

Guion: andar, girar, lateral, poses de brazos caminando y parado, manos (puño, pinza, agarre) y empujón.
Con ``--snapshot img.png`` guarda además un fotograma representativo.
"""
from __future__ import annotations

import os

os.environ.setdefault("MUJOCO_GL", "egl")

import argparse  # noqa: E402
import math  # noqa: E402
from pathlib import Path  # noqa: E402

import imageio.v2 as imageio  # noqa: E402
import mujoco  # noqa: E402
import numpy as np  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from x2_ultra.sim.engine import SimEngine  # noqa: E402

# (duración s, rótulo, acción)
SCRIPT = [
    (2.0, "De pie", lambda e: None),
    (4.0, "W: avanzar 0,5 m/s", lambda e: e.set_velocity_target(0.5, 0.0, 0.0)),
    (3.0, "W + Q: avanzar girando", lambda e: e.set_velocity_target(0.4, 0.0, 0.6)),
    (3.0, "D: paso lateral", lambda e: e.set_velocity_target(0.0, -0.3, 0.0)),
    (3.0, "E: girar en el sitio (derecha)", lambda e: e.set_velocity_target(0.0, 0.0, -1.0)),
    (3.0, "Q: girar en el sitio (izquierda)", lambda e: e.set_velocity_target(0.0, 0.0, 1.0)),
    (4.0, "Caminando con los brazos al frente (2)", lambda e: (e.set_velocity_target(0.4, 0.0, 0.0), e.play_pose("al_frente"))),
    (4.0, "Caminando con los brazos arriba (3)", lambda e: e.play_pose("arriba")),
    (3.0, "Parar (Espacio) y saludar (1)", lambda e: (e.stop_walking(), e.play_pose("saludar"))),
    (3.0, "Manos: puño (G)", lambda e: (e.play_pose("al_frente"), e.set_hand_pose("LR", "puño"))),
    (2.5, "Manos: pinza (T)", lambda e: e.set_hand_pose("LR", "pinza")),
    (2.5, "Manos: agarre", lambda e: e.set_hand_pose("LR", "agarre")),
    (3.0, "Aplaudir (9)", lambda e: e.play_pose("aplaudir")),
    (3.0, "Empujón lateral (flecha)", lambda e: (e.play_pose("reposo"), e.push(np.array([0.0, 1.0, 0.0]), 150.0))),
    (2.0, "Reposo (0)", lambda e: e.play_pose("reposo")),
]


def _font(size: int):
    for path in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf"):
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _caption(frame: np.ndarray, text: str, sub: str, font, small) -> np.ndarray:
    img = Image.fromarray(frame)
    draw = ImageDraw.Draw(img, "RGBA")
    w, h = img.size
    draw.rectangle([0, h - int(h * 0.12), w, h], fill=(10, 14, 22, 170))
    draw.text((int(w * 0.03), h - int(h * 0.105)), text, font=font, fill=(240, 244, 250, 255))
    draw.text((int(w * 0.03), int(h * 0.025)), sub, font=small, fill=(220, 228, 240, 230))
    return np.asarray(img)


def record(out: Path, policy: str, policy_path: Path | None, width: int, height: int, fps: int,
           snapshot: Path | None) -> dict:
    eng = SimEngine(scene="vacia", policy=policy, policy_path=policy_path)
    m = eng.model
    renderer = mujoco.Renderer(m, height=height, width=width)
    cam = mujoco.MjvCamera()
    cam.type = mujoco.mjtCamera.mjCAMERA_FREE
    cam.distance, cam.elevation = 2.8, -14.0
    cam.lookat[:] = [0, 0, 0.75]
    steps_per_frame = max(1, int(round(1.0 / (fps * 0.02))))
    writer = imageio.get_writer(str(out), fps=fps, codec="libx264", quality=8, macro_block_size=1)
    labels, falls, t, snap_done = [], 0, 0.0, False
    font, small = _font(max(14, height // 24)), _font(max(11, height // 40))
    sub = f"AgiBot X2 Ultra-N + OmniHand · MuJoCo · policy: {eng.policy.name}"
    try:
        for dur, label, action in SCRIPT:
            action(eng)
            labels.append((round(t, 1), label))
            for k in range(int(dur / 0.02)):
                eng.step_control()
                t += 0.02
                if eng.state == "caido":
                    falls += 1
                    eng.reset()
                if k % steps_per_frame:
                    continue
                yaw = eng.info.yaw
                target = np.array([eng.data.qpos[0], eng.data.qpos[1], 0.75])
                cam.lookat[:] = cam.lookat + 0.2 * (target - cam.lookat)
                cam.azimuth = math.degrees(yaw) + 150.0
                renderer.update_scene(eng.data, camera=cam)
                frame = renderer.render()
                c = eng.info.cmd
                frame = _caption(frame, label, f"{sub} · orden vx {c[0]:+.2f} vy {c[1]:+.2f} wz {c[2]:+.2f}",
                                 font, small)
                writer.append_data(frame)
                if snapshot and not snap_done and label.startswith("Caminando con los brazos al frente") and k > 120:
                    imageio.imwrite(str(snapshot), frame)
                    snap_done = True
    finally:
        writer.close()
        renderer.close()
    return {"video": str(out), "seconds": round(t, 1), "falls": falls, "labels": labels}


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out", type=Path)
    ap.add_argument("--policy", default="brazos_libres", choices=("brazos_libres", "agibot"))
    ap.add_argument("--policy-path", type=Path)
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=720)
    ap.add_argument("--fps", type=int, default=25)
    ap.add_argument("--snapshot", type=Path)
    args = ap.parse_args(argv)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    info = record(args.out, args.policy, args.policy_path, args.width, args.height, args.fps, args.snapshot)
    print(info)


if __name__ == "__main__":
    main()
