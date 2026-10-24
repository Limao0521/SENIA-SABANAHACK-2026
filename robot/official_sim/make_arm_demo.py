#!/usr/bin/env python3
"""Genera una trayectoria de brazos para upper_body_demo.py, resuelta con la IK de x2_ultra (MuJoCo).

Las manos dibujan círculos lentos (radio 3 cm, una vuelta cada 8 s) en el plano YZ alrededor de la postura
de reposo, manteniendo la orientación. Salida JSON {rate_hz, arm_pos[[14]...]} (izquierdo 7 + derecho 7,
orden de /mc/upper_body_command). Se ejecuta en el host con el .venv del repo, no en el contenedor:

    .venv/bin/python official_sim/make_arm_demo.py logs/x2_arm_circles.json

Nota: el MC x86 v1.0.0 todavía no acepta UPPERBODY_REMOTE_SPLIT (ver docs/05-simulador-oficial.md); esto
queda listo para cuando AgiBot publique un MC que sí lo tenga, o para el robot real.
"""
import argparse
import json
from pathlib import Path

import numpy as np

from x2_ultra.control.arm_ik import REST, ArmIK
from x2_ultra.model.builder import BuildOptions, build_model


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("out", type=Path)
    parser.add_argument("--seconds", type=float, default=16.0)
    parser.add_argument("--rate", type=float, default=50.0)
    parser.add_argument("--radius", type=float, default=0.03)
    args = parser.parse_args()

    ik = ArmIK(build_model(BuildOptions(scene="vacia")))
    base = {s: ik.fk(s, REST[s]) for s in ("L", "R")}
    q = {s: REST[s].copy() for s in ("L", "R")}
    frames, worst = [], 0.0
    for i in range(int(args.seconds * args.rate)):
        phase = 2 * np.pi * i / (8.0 * args.rate)
        row = []
        for s, sign in (("L", 1), ("R", -1)):
            p, R = base[s]
            target = p + args.radius * np.array([0.0, sign * np.sin(phase), np.cos(phase) - 1])
            res = ik.solve(s, q[s], target, R, iters=20)
            q[s] = res.q
            worst = max(worst, res.pos_err)
            row += np.round(res.q, 5).tolist()
        frames.append(row)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({"rate_hz": args.rate, "arm_pos": frames}))
    print(f"{len(frames)} fotogramas -> {args.out}; peor error de muñeca {worst * 1000:.1f} mm")


if __name__ == "__main__":
    main()
