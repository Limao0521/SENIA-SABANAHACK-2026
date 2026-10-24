"""Clonado de comportamiento (DAgger) de la policy de AgiBot hacia nuestra red "brazos libres".

PPO desde cero se quedaba de pie sin dar pasos (v1, v2) y una recompensa de imitación no bastaba (v3). Aquí la red
aprende por regresión supervisada las acciones de piernas y cintura del profesor en los estados que visita ella
misma (DAgger: al principio conduce el profesor, luego cada vez más la red). Después PPO afina la policy con los
brazos aleatorios y la aleatorización de dominio crecientes (ver train.py y docs/06).
"""
from __future__ import annotations

import time
from collections import deque

import numpy as np
import torch


def behavior_clone(env, actor, iterations: int = 120, steps_per_iter: int = 24, epochs: int = 4,
                   buffer_iters: int = 6, lr: float = 1e-3, batch: int = 8192, log=print) -> None:
    """Entrena ``actor.mlp`` (media de la gaussiana de rsl_rl) para imitar ``env.teacher_act``."""
    device = env.device
    opt = torch.optim.Adam(actor.mlp.parameters(), lr=lr)
    obs_buf, act_buf = deque(maxlen=buffer_iters), deque(maxlen=buffer_iters)
    obs = env.get_observations()
    n = env.num_envs
    t0 = time.time()
    for it in range(iterations):
        beta = max(0.0, 1.0 - it / (0.6 * iterations))      # fracción de robots que conduce el profesor
        teacher_drives = torch.rand(n, 1, device=device) < beta
        o_list, a_list, falls, vx_err = [], [], 0, []
        with torch.no_grad():
            for _ in range(steps_per_iter):
                o = obs["policy"]
                teacher = torch.as_tensor(env.teacher_act, dtype=torch.float32, device=device)
                student = actor.mlp(o)
                o_list.append(o)
                a_list.append(teacher)
                obs, _, done, _ = env.step(torch.where(teacher_drives, teacher, student))
                falls += int(done.sum())
                _, _, _, lin_b, *_ = env._base_quantities()
                vx_err.append(np.abs(lin_b[:, 0] - env.cmd[:, 0])[~teacher_drives.cpu().numpy()[:, 0]].mean()
                              if (~teacher_drives).any() else np.nan)
        obs_buf.append(torch.cat(o_list))
        act_buf.append(torch.cat(a_list))
        X, Y = torch.cat(list(obs_buf)), torch.cat(list(act_buf))
        losses = []
        for _ in range(epochs):
            perm = torch.randperm(len(X), device=device)
            for k in range(0, len(X), batch):
                idx = perm[k:k + batch]
                loss = torch.mean((actor.mlp(X[idx]) - Y[idx]) ** 2)
                opt.zero_grad()
                loss.backward()
                opt.step()
                losses.append(loss.item())
        if it % 5 == 0 or it == iterations - 1:
            log(f"[BC {it:4d}/{iterations}] beta {beta:.2f} loss {np.mean(losses):.4f} "
                f"caídas/rollout {falls} |vx−cmd| red {np.nanmean(vx_err):.3f} m/s  ({time.time() - t0:.0f} s)")
