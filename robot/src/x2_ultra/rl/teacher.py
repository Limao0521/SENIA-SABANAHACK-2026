"""La policy oficial de AgiBot como "profesor" durante el entrenamiento (recompensa de imitación).

Por qué: sin guía, PPO se queda en el mínimo local de "quedarse de pie sin dar pasos" (v1 y v2, ver docs/06).
La policy de mc-rl camina bien en nuestra física (verificado), así que sus acciones de piernas y cintura sirven
de referencia densa al principio. El peso de la imitación baja con la dificultad: cuando los brazos se mueven
mucho, el profesor (que asume brazos cerca de la postura por defecto) deja de ser buena referencia y mandan las
recompensas de la tarea.

La red (MLP 500-512-256-128-29, ELU) se carga del ONNX y se evalúa en lote con torch para miles de robots.
"""
from __future__ import annotations

import numpy as np
import onnx
import torch
from onnx import numpy_helper

from x2_ultra import paths
from x2_ultra.control import obs_spec as S
from x2_ultra.control.locomotion import AGIBOT_ACTION_JOINTS, AGIBOT_OBS_JOINTS, AGIBOT_TERMS
from x2_ultra.robot import joints as J

# Rango de órdenes con el que AgiBot entrenó (más allá sus acciones no son referencia fiable).
TEACHER_CMD_CLIP = np.array([[-0.6, -0.3, -0.3], [1.0, 0.3, 0.3]])


def load_agibot_mlp(device: str) -> torch.nn.Module:
    model = onnx.load(str(paths.agibot_policy()))
    w = {i.name: torch.as_tensor(numpy_helper.to_array(i).copy()) for i in model.graph.initializer}
    layers, dims = [], [(500, 512), (512, 256), (256, 128), (128, 29)]
    for k, (din, dout) in enumerate(dims):
        lin = torch.nn.Linear(din, dout)
        lin.weight.data.copy_(w[f"mlp.{2 * k}.weight"])
        lin.bias.data.copy_(w[f"mlp.{2 * k}.bias"])
        layers += [lin, torch.nn.ELU()] if k < 3 else [lin]
    return torch.nn.Sequential(*layers).to(device).eval()


class AgibotTeacher:
    def __init__(self, num_envs: int, device: str):
        self.n, self.device = num_envs, device
        self.net = load_agibot_mlp(device)
        self.perm = np.array([S.OBS_JOINTS.index(n) for n in AGIBOT_OBS_JOINTS])          # nuestro orden -> AgiBot
        self.amap = np.array([AGIBOT_ACTION_JOINTS.index(n) for n in S.ACTION_JOINTS])    # AgiBot 29 -> nuestras 15
        self.default = np.array([J.DEFAULT_POSE[n] for n in AGIBOT_OBS_JOINTS])
        self.hist = {name: np.zeros((num_envs, 5, dim)) for name, dim in AGIBOT_TERMS}
        self.last_action = np.zeros((num_envs, 29))
        self.fresh = np.ones(num_envs, dtype=bool)

    def reset(self, ids: np.ndarray) -> None:
        self.last_action[ids] = 0.0
        self.fresh[ids] = True

    @torch.no_grad()
    def act(self, q: np.ndarray, qd: np.ndarray, ang_b: np.ndarray, grav: np.ndarray, cmd: np.ndarray) -> np.ndarray:
        """q, qd en el orden S.OBS_JOINTS (absolutos). Devuelve la acción del profesor en el orden S.ACTION_JOINTS."""
        frame = {
            "base_ang_vel": ang_b * 0.25,
            "projected_gravity": grav,
            "velocity_commands": np.clip(cmd, TEACHER_CMD_CLIP[0], TEACHER_CMD_CLIP[1]),
            "joint_pos_rel": q[:, self.perm] - self.default,
            "joint_vel_rel": qd[:, self.perm] * 0.05,
            "last_action": self.last_action,
        }
        for name, _ in AGIBOT_TERMS:
            h = self.hist[name]
            h[:, :-1] = h[:, 1:]
            h[:, -1] = frame[name]
            if self.fresh.any():
                h[self.fresh] = frame[name][self.fresh][:, None, :]
        self.fresh[:] = False
        obs = np.concatenate([self.hist[name].reshape(self.n, -1) for name, _ in AGIBOT_TERMS], -1)
        obs = torch.as_tensor(np.clip(obs, -100, 100), dtype=torch.float32, device=self.device)
        action = torch.clamp(self.net(obs), -5.0, 5.0).cpu().numpy().astype(np.float64)
        self.last_action = action
        return action[:, self.amap]
