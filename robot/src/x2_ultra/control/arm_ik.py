"""Cinemática inversa de los brazos (7 ejes) con MuJoCo como modelo cinemático.

Los objetivos se expresan en el marco del torso (``torso_link``): así la mano mantiene su postura respecto
al cuerpo aunque el robot camine o la cintura se mueva. Se resuelve con mínimos cuadrados amortiguados
(posición + orientación del sitio ``{L,R}_grasp``), límites articulares con margen y un tirón en el
espacio nulo hacia una postura cómoda.
"""
from __future__ import annotations

from dataclasses import dataclass

import mujoco
import numpy as np

from x2_ultra.robot import joints as J

SIDES = ("L", "R")
ARM = {"L": J.LEFT_ARM_JOINTS, "R": J.RIGHT_ARM_JOINTS}
REST = {  # postura de referencia del espacio nulo (codo flexionado, brazo algo adelantado)
    "L": np.array([-0.4, 0.25, 0.0, -1.0, 0.0, 0.0, 0.0]),
    "R": np.array([-0.4, -0.25, 0.0, -1.0, 0.0, 0.0, 0.0]),
}
MIRROR = np.diag([1.0, -1.0, 1.0])


@dataclass
class IKResult:
    q: np.ndarray
    pos_err: float
    rot_err: float


def rot_log(R: np.ndarray) -> np.ndarray:
    """Vector de rotación (eje·ángulo) de una matriz de rotación."""
    cos = np.clip((np.trace(R) - 1) / 2, -1.0, 1.0)
    ang = np.arccos(cos)
    if ang < 1e-7:
        return np.zeros(3)
    v = np.array([R[2, 1] - R[1, 2], R[0, 2] - R[2, 0], R[1, 0] - R[0, 1]])
    if np.pi - ang < 1e-4:
        # cerca de 180°: eje desde la diagonal
        axis = np.sqrt(np.clip((np.diag(R) + 1) / 2, 0, None))
        axis *= np.sign(v + 1e-12)
        return axis / (np.linalg.norm(axis) + 1e-12) * ang
    return v / (2 * np.sin(ang)) * ang


def rot_exp(w: np.ndarray) -> np.ndarray:
    ang = np.linalg.norm(w)
    if ang < 1e-9:
        return np.eye(3)
    k = w / ang
    K = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
    return np.eye(3) + np.sin(ang) * K + (1 - np.cos(ang)) * K @ K


def mirror_pose(p: np.ndarray, R: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Refleja una pose respecto al plano sagital (y -> -y): de la mano izquierda a la derecha."""
    return MIRROR @ p, MIRROR @ R @ MIRROR


class ArmIK:
    def __init__(self, model: mujoco.MjModel, margin: float = 0.03):
        self.m = model
        self.d = mujoco.MjData(model)
        mujoco.mj_resetData(model, self.d)
        for name, v in J.DEFAULT_POSE.items():
            self.d.qpos[self._qadr(name)] = v
        self.d.qpos[3:7] = [1, 0, 0, 0]
        self.d.qpos[0:3] = 0.0
        mujoco.mj_kinematics(model, self.d)
        self.torso = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "torso_link")
        self.site = {s: mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SITE, f"{s}_grasp") for s in SIDES}
        self.qadr = {s: np.array([self._qadr(n) for n in ARM[s]]) for s in SIDES}
        self.dofadr = {s: np.array([model.jnt_dofadr[self._jid(n)] for n in ARM[s]]) for s in SIDES}
        self.lo = {s: np.array([J.JOINT_RANGE[n][0] for n in ARM[s]]) + margin for s in SIDES}
        self.hi = {s: np.array([J.JOINT_RANGE[n][1] for n in ARM[s]]) - margin for s in SIDES}
        self._jacp = np.zeros((3, model.nv))
        self._jacr = np.zeros((3, model.nv))

    def _jid(self, name: str) -> int:
        return mujoco.mj_name2id(self.m, mujoco.mjtObj.mjOBJ_JOINT, name)

    def _qadr(self, name: str) -> int:
        return self.m.jnt_qposadr[self._jid(name)]

    def _torso_frame(self):
        return self.d.xpos[self.torso].copy(), self.d.xmat[self.torso].reshape(3, 3).copy()

    def fk(self, side: str, q: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Pose del punto de agarre en el marco del torso."""
        self.d.qpos[self.qadr[side]] = q
        mujoco.mj_kinematics(self.m, self.d)
        pt, Rt = self._torso_frame()
        sid = self.site[side]
        p = Rt.T @ (self.d.site_xpos[sid] - pt)
        R = Rt.T @ self.d.site_xmat[sid].reshape(3, 3)
        return p, R

    def _residual(self, side, q, p_target, R_target, rot_weight):
        sid, dofs = self.site[side], self.dofadr[side]
        self.d.qpos[self.qadr[side]] = q
        mujoco.mj_kinematics(self.m, self.d)
        mujoco.mj_comPos(self.m, self.d)
        pt, Rt = self._torso_frame()
        p = Rt.T @ (self.d.site_xpos[sid] - pt)
        e_p = p_target - p
        mujoco.mj_jacSite(self.m, self.d, self._jacp, self._jacr, sid)
        Jp = Rt.T @ self._jacp[:, dofs]
        if R_target is None:
            return e_p, Jp, float(np.linalg.norm(e_p)), 0.0
        R = Rt.T @ self.d.site_xmat[sid].reshape(3, 3)
        e_r = rot_log(R_target @ R.T)
        Jr = Rt.T @ self._jacr[:, dofs]
        e = np.concatenate([e_p, rot_weight * e_r])
        return e, np.vstack([Jp, rot_weight * Jr]), float(np.linalg.norm(e_p)), float(np.linalg.norm(e_r))

    def solve(self, side: str, q0: np.ndarray, p_target: np.ndarray, R_target: np.ndarray | None,
              iters: int = 30, rot_weight: float = 0.3, null_gain: float = 0.02) -> IKResult:
        """Levenberg-Marquardt con amortiguamiento adaptativo; los ejes que tocan su límite se congelan."""
        lo, hi = self.lo[side], self.hi[side]
        q = np.clip(np.asarray(q0, dtype=float).copy(), lo, hi)
        e, Jm, pos_err, rot_err = self._residual(side, q, p_target, R_target, rot_weight)
        cost = float(e @ e)
        lam = 1e-3
        for _ in range(iters):
            if pos_err < 5e-4 and rot_err < 5e-3:
                break
            free = np.ones(7, dtype=bool)
            for _pass in range(2):
                Jf = Jm[:, free]
                nf = int(free.sum())
                H = Jf.T @ Jf + lam * np.eye(nf)
                pinv = np.linalg.solve(H, Jf.T)                       # (nf × m)
                dq = np.zeros(7)
                dq[free] = pinv @ e
                if null_gain > 0:                                       # tirón hacia REST en el espacio nulo
                    dq[free] += (np.eye(nf) - pinv @ Jf) @ (null_gain * (REST[side] - q)[free])
                blocked = ((q <= lo + 1e-6) & (dq < 0)) | ((q >= hi - 1e-6) & (dq > 0))
                if not (blocked & free).any():
                    break
                free &= ~blocked
            q_new = np.clip(q + dq, lo, hi)
            e_new, J_new, pe_new, re_new = self._residual(side, q_new, p_target, R_target, rot_weight)
            cost_new = float(e_new @ e_new)
            if cost_new < cost:
                q, e, Jm, pos_err, rot_err, cost = q_new, e_new, J_new, pe_new, re_new, cost_new
                lam = max(lam * 0.3, 1e-6)
            else:
                lam = min(lam * 10, 1e3)
                self._residual(side, q, p_target, R_target, rot_weight)
        return IKResult(q, pos_err, rot_err)

    def solve_global(self, side: str, p_target: np.ndarray, R_target: np.ndarray | None,
                     seeds: int = 12, rng_seed: int = 0) -> IKResult:
        """Varios arranques (para calcular poses fuera de línea); devuelve el de menor error."""
        rng = np.random.default_rng(rng_seed)
        starts = [REST[side], np.array([J.DEFAULT_POSE[n] for n in ARM[side]])]
        starts += [rng.uniform(self.lo[side], self.hi[side]) for _ in range(seeds)]
        best = None
        for q0 in starts:
            r = self.solve(side, q0, p_target, R_target, iters=150)
            score = r.pos_err + 0.05 * r.rot_err
            if best is None or score < best[0]:
                best = (score, r)
        return best[1]
