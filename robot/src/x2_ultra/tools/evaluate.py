"""Evaluación sin ventana de una policy de marcha en el modelo completo del simulador (con dedos, dt 2 ms).

    python -m x2_ultra.tools.evaluate                         # policy "brazos libres" instalada
    python -m x2_ultra.tools.evaluate --policy agibot         # policy oficial de AgiBot
    python -m x2_ultra.tools.evaluate --policy-path runs/x/exported/policy.onnx --json out.json
    python -m x2_ultra.tools.evaluate --giro                  # curva de giro en el sitio (ambos sentidos)

Escenarios: de pie, avanzar, retroceder, lateral, girar, caminar cambiando de pose de brazos, brazos
aleatorios (IK) de pie y caminando, y empujones. Para cada uno: si se cae, velocidad media medida frente a
la ordenada y altura de la pelvis.
"""
from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from x2_ultra.control.poses import ARM_POSES
from x2_ultra.robot import joints as J
from x2_ultra.sim.engine import SimEngine


@dataclass
class Result:
    scenario: str
    fell: bool
    time_s: float
    cmd: tuple
    v_meas: tuple
    yaw_rate: float
    height_mean: float
    height_std: float


def _body_vel(eng: SimEngine) -> np.ndarray:
    yaw = eng.info.yaw
    v = eng.data.qvel[0:2]
    return np.array([math.cos(yaw) * v[0] + math.sin(yaw) * v[1], -math.sin(yaw) * v[0] + math.cos(yaw) * v[1]])


def run_scenario(eng: SimEngine, name: str, duration: float, cmd=(0.0, 0.0, 0.0), arms=None, push_every=None,
                 rng=None, push_force: float = 120.0) -> Result:
    eng.reset()
    eng.run_for(1.0)
    eng.set_velocity_target(*cmd)
    vels, heights, t = [], [], 0.0
    yaw_prev, yaw_accum, t_meas = eng.info.yaw, 0.0, 0.0
    poses = list(ARM_POSES)
    next_arm, k, next_push = 0.0, 0, push_every or 1e9
    lo = np.array([J.JOINT_RANGE[n][0] for n in J.ARM_JOINTS]) + 0.05
    hi = np.array([J.JOINT_RANGE[n][1] for n in J.ARM_JOINTS]) - 0.05
    default = np.array([J.DEFAULT_POSE[n] for n in J.ARM_JOINTS])
    while t < duration:
        if arms == "poses" and t >= next_arm:
            eng.play_pose(poses[k % len(poses)])
            k += 1
            next_arm += 2.0
        elif arms == "random" and t >= next_arm:
            eng.set_arm_targets(default + rng.uniform(0, 1) * (rng.uniform(lo, hi) - default))
            next_arm += rng.uniform(0.8, 2.0)
        if t >= next_push:
            eng.push(force=push_force)
            next_push += push_every
        eng.step_control()
        t += 0.02
        dyaw = math.atan2(math.sin(eng.info.yaw - yaw_prev), math.cos(eng.info.yaw - yaw_prev))
        yaw_prev = eng.info.yaw
        if t > 1.5:   # tras la rampa de velocidad; el giro se integra paso a paso (sin envolver a ±π)
            vels.append(_body_vel(eng))
            heights.append(eng.data.qpos[2])
            yaw_accum += dyaw
            t_meas += 0.02
        if eng.state == "caido":
            break
    v = np.mean(vels, axis=0) if vels else np.zeros(2)
    h = np.array(heights) if heights else np.zeros(1)
    return Result(name, eng.state == "caido", round(t, 2), tuple(cmd), tuple(round(float(x), 3) for x in v),
                  round(yaw_accum / max(t_meas, 1e-3), 3), round(float(h.mean()), 3), round(float(h.std()), 4))


SCENARIOS = [
    ("de_pie", 10.0, (0.0, 0.0, 0.0), None, None),
    ("avanzar_0.5", 10.0, (0.5, 0.0, 0.0), None, None),
    ("avanzar_0.9", 8.0, (0.9, 0.0, 0.0), None, None),
    ("retroceder_0.3", 8.0, (-0.3, 0.0, 0.0), None, None),
    ("lateral_0.3", 8.0, (0.0, 0.3, 0.0), None, None),
    ("girar_izq_0.8", 8.0, (0.0, 0.0, 0.8), None, None),
    ("girar_der_0.8", 8.0, (0.0, 0.0, -0.8), None, None),
    ("avanzar_y_girar", 10.0, (0.4, 0.0, 0.5), None, None),
    ("poses_de_pie", 22.0, (0.0, 0.0, 0.0), "poses", None),
    ("poses_caminando", 22.0, (0.4, 0.0, 0.0), "poses", None),
    ("brazos_aleatorios_de_pie", 20.0, (0.0, 0.0, 0.0), "random", None),
    ("brazos_aleatorios_caminando", 20.0, (0.35, 0.0, 0.2), "random", None),
    ("empujones_caminando", 15.0, (0.4, 0.0, 0.0), None, 3.0),
]


# Escenarios difíciles (--duro): para elegir entre checkpoints que ya superan los normales.
SCENARIOS_HARD = [
    ("empujones_fuertes_200N", 15.0, (0.4, 0.0, 0.0), None, 2.5),
    ("brazos_aleatorios_rapido", 20.0, (0.7, 0.0, 0.0), "random", None),
    ("brazos_aleatorios_girando", 20.0, (0.0, 0.0, 0.6), "random", None),
    ("brazos_aleatorios_girando_der", 20.0, (0.0, 0.0, -1.0), "random", None),
    ("brazos_aleatorios_lateral", 20.0, (0.0, 0.3, 0.0), "random", None),
    ("poses_girando", 22.0, (0.3, 0.0, 0.5), "poses", None),
    ("brazos_aleatorios_y_empujones", 20.0, (0.3, 0.0, 0.0), "random", 3.0),
]


def evaluate(policy: str, policy_path: Path | None, seed: int = 0, only=None, hard: bool = False) -> list[Result]:
    eng = SimEngine(scene="vacia", policy=policy, policy_path=policy_path)
    eng.heading_hold = False
    rng = np.random.default_rng(seed)
    out = []
    for name, dur, cmd, arms, push in (SCENARIOS_HARD if hard else SCENARIOS):
        if only and name not in only:
            continue
        force = 200.0 if "200N" in name else 120.0
        out.append(run_scenario(eng, name, dur, cmd, arms, push, rng, push_force=force))
    return out


TURN_RATES = (0.6, 0.8, 1.0)


def turn_curve(policy_path: Path | None, mirror: bool = False) -> dict[float, float]:
    """Giro medido [rad/s] en el sitio, brazos quietos, para ±TURN_RATES. ``mirror=False`` mide la policy tal
    cual (sin el espejo de ``preferred_turn``): sirve para decidir qué lado domina."""
    eng = SimEngine(scene="vacia", policy="brazos_libres", policy_path=policy_path)
    eng.heading_hold = False
    if not mirror:
        eng.policy.preferred_turn = 0
    out = {}
    for wz in TURN_RATES + tuple(-w for w in TURN_RATES):
        out[wz] = run_scenario(eng, f"giro_{wz}", 8.0, (0.0, 0.0, wz)).yaw_rate
    return out


def print_turn_curve(policy_path: Path | None) -> None:
    raw, mirrored = turn_curve(policy_path), turn_curve(policy_path, mirror=True)
    rates = sorted(raw, key=lambda w: (w < 0, abs(w)))
    print(f"{'orden wz [rad/s]':24s} " + " ".join(f"{w:+6.1f}" for w in rates))
    print(f"{'policy tal cual':24s} " + " ".join(f"{raw[w]:+6.2f}" for w in rates))
    print(f"{'con espejo (simulador)':24s} " + " ".join(f"{mirrored[w]:+6.2f}" for w in rates))
    left = sum(raw[w] for w in TURN_RATES)
    right = -sum(raw[-w] for w in TURN_RATES)
    print(f"giro izq. {left:.2f} · der. {right:.2f} → preferred_turn sugerido: {1 if left >= right else -1} "
          "(campo de policy.json; o `python -m x2_ultra.rl.train --export <ckpt> --preferred-turn N`)")


def print_table(results: list[Result]) -> None:
    print(f"{'escenario':30s} {'¿cae?':>6s} {'t[s]':>6s}  {'orden (vx,vy,wz)':>20s}  {'medida vx,vy':>14s} "
          f"{'wz':>6s} {'altura':>7s}")
    for r in results:
        print(f"{r.scenario:30s} {'SÍ' if r.fell else 'no':>6s} {r.time_s:6.1f}  "
              f"{str(tuple(round(c, 2) for c in r.cmd)):>20s}  {str(r.v_meas):>14s} {r.yaw_rate:6.2f} "
              f"{r.height_mean:7.3f}")
    falls = sum(r.fell for r in results)
    print(f"caídas: {falls}/{len(results)}")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--policy", default="brazos_libres", choices=("brazos_libres", "agibot"))
    ap.add_argument("--policy-path", type=Path)
    ap.add_argument("--json", type=Path)
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--duro", action="store_true", help="escenarios difíciles (empujones de 200 N, brazos + giro…)")
    ap.add_argument("--giro", action="store_true", help="solo la curva de giro en el sitio, con y sin espejo")
    args = ap.parse_args(argv)
    if args.giro:
        print_turn_curve(args.policy_path)
        return
    results = evaluate(args.policy, args.policy_path, args.seed, args.only, args.duro)
    print_table(results)
    if args.json:
        args.json.write_text(json.dumps([asdict(r) for r in results], indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
