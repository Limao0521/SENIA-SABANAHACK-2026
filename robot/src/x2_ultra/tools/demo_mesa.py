"""Demo programada: ir a la mesa, coger la botella con la mano derecha, levantarla y volver.

    python -m x2_ultra.tools.demo_mesa                     # sin vídeo, imprime las fases
    python -m x2_ultra.tools.demo_mesa --video docs/media/demo_mesa.mp4

Sirve también de ejemplo de cómo manejar ``SimEngine`` desde código: velocidad (lo mismo que WASD/QE), IK del brazo
en el marco del torso (lo mismo que IJKL/UO) y poses de mano (lo mismo que G/T).
"""
from __future__ import annotations

import os

os.environ.setdefault("MUJOCO_GL", "egl")

import argparse  # noqa: E402
import math  # noqa: E402
from pathlib import Path  # noqa: E402

import mujoco  # noqa: E402
import numpy as np  # noqa: E402

from x2_ultra.control.arm_ik import ArmIK  # noqa: E402
from x2_ultra.robot import joints as J  # noqa: E402
from x2_ultra.sim.engine import SimEngine  # noqa: E402

ARM_R = slice(7, 14)


def _wrap(a: float) -> float:
    return (a + math.pi) % (2 * math.pi) - math.pi


class Demo:
    def __init__(self, video: Path | None, policy_path: Path | None = None):
        self.eng = SimEngine(scene="mesa", policy_path=policy_path)
        self.eng.heading_hold = False
        self.m, self.d = self.eng.model, self.eng.data
        self.ik = ArmIK(self.m)
        self.cube = mujoco.mj_name2id(self.m, mujoco.mjtObj.mjOBJ_BODY, "botella")
        self.torso = self.eng.torso
        self.frames, self.writer, self.renderer = 0, None, None
        self.label = ""
        if video:
            import imageio.v2 as imageio
            from x2_ultra.tools.record import _caption, _font
            self.writer = imageio.get_writer(str(video), fps=25, codec="libx264", quality=7, macro_block_size=1)
            self.renderer = mujoco.Renderer(self.m, height=540, width=960)
            self.cam = mujoco.MjvCamera()
            self.cam.distance, self.cam.elevation, self.cam.azimuth = 2.2, -18.0, 200.0
            self.cam.lookat[:] = [0, 0.6, 0.8]
            self._caption, self.font, self.small = _caption, _font(22), _font(13)

    # ------------------------------------------------------------------------------------ utilidades
    def tick(self, n: int = 1) -> None:
        for _ in range(n):
            self.eng.step_control()
            self.frames += 1
            if self.writer is not None and self.frames % 2 == 0:
                target = np.array([self.d.qpos[0], self.d.qpos[1] + 0.3, 0.8])
                self.cam.lookat[:] += 0.1 * (target - self.cam.lookat)
                self.renderer.update_scene(self.d, camera=self.cam)
                img = self._caption(self.renderer.render(), self.label,
                                    "AgiBot X2 Ultra-N + OmniHand · demo programada (tools/demo_mesa.py)",
                                    self.font, self.small)
                self.writer.append_data(img)
        if self.eng.state != "activo":
            raise RuntimeError(f"el robot se cayó en la fase '{self.label}'")

    def phase(self, text: str) -> None:
        self.label = text
        print(f"[{self.d.time:6.1f} s] {text}", flush=True)

    def torso_frame(self):
        return self.d.xpos[self.torso].copy(), self.d.xmat[self.torso].reshape(3, 3).copy()

    # ------------------------------------------------------------------------------------ fases
    def turn_to(self, yaw_goal: float, tol: float = 0.06, max_s: float = 15.0) -> None:
        for _ in range(int(max_s / 0.02)):
            err = _wrap(yaw_goal - self.eng.info.yaw)
            if abs(err) < tol:
                break
            # la policy apenas gira en el sitio con órdenes < 0,7 rad/s: mínimo de 0,8 hasta llegar
            wz = float(np.sign(err) * np.clip(abs(2.0 * err), 0.8, 1.0))
            self.eng.set_velocity_target(0.0, 0.0, wz)
            self.tick()
        print(f"    rumbo {math.degrees(self.eng.info.yaw):.1f}° (objetivo {math.degrees(yaw_goal):.1f}°)", flush=True)
        self.eng.set_velocity_target(0.0, 0.0, 0.0)

    def walk_to(self, x_goal: float, y_goal: float, yaw_goal: float, tol: float = 0.05, max_s: float = 20.0) -> None:
        """Avanza (+y del mundo, mirando a la mesa) corrigiendo x con paso lateral y el rumbo con giro."""
        for _ in range(int(max_s / 0.02)):
            x, y = self.d.qpos[0], self.d.qpos[1]
            ex, ey = x_goal - x, y_goal - y
            if abs(ex) < tol and abs(ey) < tol:
                break
            yaw = self.eng.info.yaw
            # error en el marco del robot (x adelante, y izquierda)
            fwd = math.cos(yaw) * ex + math.sin(yaw) * ey
            left = -math.sin(yaw) * ex + math.cos(yaw) * ey
            # Por debajo de ~0,25 m/s la policy no da pasos (umbral de arranque, docs/04) y frena en pocos cm:
            # velocidad mínima de 0,28 m/s mientras quede distancia, y parar dentro de la tolerancia.
            vx = float(np.sign(fwd) * np.clip(abs(1.2 * fwd), 0.28, 0.4)) if abs(fwd) > tol else 0.0
            vy = float(np.sign(left) * np.clip(abs(1.5 * left), 0.28, 0.3)) if abs(left) > tol else 0.0
            self.eng.set_velocity_target(vx, vy, float(np.clip(1.5 * _wrap(yaw_goal - yaw), -0.5, 0.5)))
            self.tick()
        print(f"    posición ({self.d.qpos[0]:+.2f}, {self.d.qpos[1]:+.2f}) objetivo ({x_goal:+.2f}, {y_goal:+.2f})", flush=True)
        self.eng.set_velocity_target(0.0, 0.0, 0.0)

    def reach(self, world_target: np.ndarray, R_world: np.ndarray, seconds: float, settle: float = 0.5) -> float:
        """Lleva el punto de agarre derecho a un objetivo del mundo (recta en el mundo). El objetivo se vuelve a
        expresar en el marco del torso en cada paso, así la mano sigue el punto aunque el cuerpo se balancee."""
        q = self.eng.arm_target[ARM_R].copy()
        sid = mujoco.mj_name2id(self.m, mujoco.mjtObj.mjOBJ_SITE, "R_grasp")
        p0_w = self.d.site_xpos[sid].copy()
        steps = int(seconds / 0.02)
        for k in range(1, steps + int(settle / 0.02) + 1):
            s = 0.5 - 0.5 * math.cos(math.pi * min(k, steps) / steps)
            pt, Rt = self.torso_frame()
            goal = Rt.T @ (p0_w + s * (world_target - p0_w) - pt)
            res = self.ik.solve("R", q, goal, Rt.T @ R_world, iters=20, rot_weight=0.3)
            q = res.q
            arms = self.eng.arm_target.copy()
            arms[ARM_R] = q
            self.eng.set_arm_targets(arms)
            self.tick()
        return float(np.linalg.norm(self.d.site_xpos[sid] - world_target))

    def run(self) -> dict:
        eng = self.eng
        cube0 = self.d.xpos[self.cube].copy()
        self.phase("De pie")
        self.tick(50)
        self.phase("Q: girar 90° hacia la mesa")
        self.turn_to(math.pi / 2)
        self.phase("W + A/D: acercarse a la mesa")
        # la botella debe quedar delante y a la izquierda de la mano derecha (mirando a +y, la izquierda es -x)
        self.walk_to(cube0[0] - 0.06, cube0[1] - 0.33, math.pi / 2)
        self.tick(40)
        # agarre lateral: palma (+x) hacia la botella (-x del mundo), dedos (+z) hacia la mesa (+y), pulgar arriba
        side = np.stack([[-1.0, 0, 0], [0, 0, 1.0], [0, 1.0, 0]], axis=1)
        eng.set_hand_pose("R", "abierta")
        cube = self.d.xpos[self.cube].copy()
        sid = mujoco.mj_name2id(self.m, mujoco.mjtObj.mjOBJ_SITE, "R_grasp")
        hand = self.d.site_xpos[sid].copy()
        self.phase("IK: subir la mano por delante del cuerpo")
        self.reach(np.array([hand[0], hand[1] + 0.10, cube[2] + 0.12]), side, 1.5, settle=0.2)
        self.phase("IK: mano a la derecha de la botella (J/L, I/K)")
        print(f"    error IK {self.reach(cube + np.array([0.12, -0.02, 0.0]), side, 1.5) * 1000:.0f} mm", flush=True)
        self.phase("IK: rodear la botella")
        print(f"    error IK {self.reach(cube + np.array([0.0, -0.01, 0.0]), side, 1.5) * 1000:.0f} mm", flush=True)
        self.phase("G: cerrar la mano (agarre)")
        eng.set_hand_pose("R", "agarre")
        self.tick(40)
        self.phase("IK: levantar la botella (U)")
        self.reach(cube + np.array([0.02, -0.06, 0.15]), side, 1.5)
        self.tick(25)
        lifted = self.d.xpos[self.cube][2] - cube[2]
        self.phase("S: retroceder con la botella")
        eng.set_velocity_target(-0.25, 0.0, 0.0)
        self.tick(100)
        eng.set_velocity_target(0.0, 0.0, 0.0)
        self.tick(50)
        carried = self.d.xpos[self.cube][2] - cube[2]
        info = {"botella_levantada_m": round(float(lifted), 3), "botella_tras_retroceder_m": round(float(carried), 3),
                "estado": eng.state, "tiempo_s": round(float(self.d.time), 1)}
        if self.writer is not None:
            self.writer.close()
            self.renderer.close()
        return info


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--video", type=Path)
    ap.add_argument("--policy-path", type=Path)
    args = ap.parse_args(argv)
    print(Demo(args.video, args.policy_path).run())


if __name__ == "__main__":
    main()
