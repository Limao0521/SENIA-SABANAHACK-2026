"""Comprobación SOLO LECTURA del X2 real: qué ve el PC y si se podría armar cada programa.

No crea publicadores ni llama a servicios: ``scripts/robot_real.sh check``. Sale con 0 si los dos programas
podrían armarse, 1 si no.
"""
from __future__ import annotations

import argparse
import json

from x2_ultra.real import control as K
from x2_ultra.real.link import RobotLink, require_target
from x2_ultra.robot.joints import ARM_JOINTS, JOINT_RANGE


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--espera", type=float, default=4.0, help="segundos escuchando antes de informar")
    args = ap.parse_args(argv)
    target = require_target()
    link = RobotLink("check")
    try:
        link.wait_ready(args.espera)
        obs = link.observed()
        arms = link.latest("arms")
        margins = None if arms is None else {
            n: round(min(arms[n] - JOINT_RANGE[n][0], JOINT_RANGE[n][1] - arms[n]), 3) for n in ARM_JOINTS}
        report = {
            "destino": target,
            "observado": vars(obs),
            "odometria": link.odometry(),
            "brazos_rad": None if arms is None else {n: round(arms[n], 4) for n in ARM_JOINTS},
            "margen_minimo_a_tope": None if margins is None else min(margins.items(), key=lambda kv: kv[1]),
            "andar": K.preflight(obs, "andar", need_clock=True) or "se puede armar",
            "brazos": K.preflight(obs, "brazos", need_clock=False) or "se puede armar",
        }
        print(json.dumps(report, indent=1, ensure_ascii=False, default=str))
        return 0 if report["andar"] == report["brazos"] == "se puede armar" else 1
    finally:
        link.close()


if __name__ == "__main__":
    raise SystemExit(main())
