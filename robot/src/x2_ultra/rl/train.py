"""Entrena la policy de marcha "brazos libres" (PPO de rsl_rl, misma configuración que AgiBot).

    python -m x2_ultra.rl.train --iterations 3000            # ~2-3 h en el portátil (32 hilos + RTX 5070 Ti)
    python -m x2_ultra.rl.train --resume runs/<fecha>/model_1500.pt --iterations 1000
    python -m x2_ultra.rl.train --export runs/<fecha>/model_3000.pt   # solo exportar a ONNX

Exporta ``policy.onnx`` + ``policy.json`` (contrato de observación) en la carpeta del run y, con
``--install``, copia la policy a ``assets/policies/x2_arms_free_walk.onnx`` (la que usa el simulador).
"""
from __future__ import annotations

import argparse
import json
import shutil
import time
from datetime import datetime
from pathlib import Path

import torch

from x2_ultra import paths
from x2_ultra.control import obs_spec as S
from x2_ultra.robot import joints as J

TRAIN_CFG = {
    "num_steps_per_env": 24,
    "save_interval": 100,
    "obs_groups": {"actor": ["policy"], "critic": ["critic"]},
    "logger": "tensorboard",
    "algorithm": {
        "class_name": "PPO",
        "value_loss_coef": 1.0, "use_clipped_value_loss": True, "clip_param": 0.2, "entropy_coef": 0.01,
        "num_learning_epochs": 5, "num_mini_batches": 4, "learning_rate": 1.0e-3, "schedule": "adaptive",
        "gamma": 0.99, "lam": 0.95, "desired_kl": 0.01, "max_grad_norm": 1.0,
    },
    "actor": {
        "class_name": "MLPModel", "hidden_dims": [512, 256, 128], "activation": "elu",
        "obs_normalization": False,
        "distribution_cfg": {"class_name": "GaussianDistribution", "init_std": 1.0},
    },
    "critic": {"class_name": "MLPModel", "hidden_dims": [512, 256, 128], "activation": "elu",
               "obs_normalization": False},
}


def policy_metadata(preferred_turn: int = 0) -> dict:
    return {
        "name": "x2_arms_free_walk",
        "robot": "AgiBot X2 Ultra-N + OmniHand (X2_URDF-v1.4.0)",
        "obs_terms": [list(t) for t in S.OBS_TERMS],
        "history": S.HISTORY,
        "obs_dim": S.OBS_DIM,
        "obs_joints": list(S.OBS_JOINTS),
        "action_joints": list(S.ACTION_JOINTS),
        "default_pose": {k: J.DEFAULT_POSE[k] for k in S.OBS_JOINTS},
        "ang_vel_scale": S.ANG_VEL_SCALE, "joint_vel_scale": S.JOINT_VEL_SCALE,
        "action_scale": S.ACTION_SCALE, "action_clip": S.ACTION_CLIP, "policy_dt": S.POLICY_DT,
        "cmd_limits": S.CMD_LIMITS,
        "kp": {k: J.KP[k] for k in S.OBS_JOINTS}, "kd": {k: J.KD[k] for k in S.OBS_JOINTS},
        # Lado al que gira bien en el sitio (+1 izquierda, -1 derecha, 0 ambos): el simulador usa la policy
        # reflejada para el otro (ver ArmsFreePolicy). Se mide con `python -m x2_ultra.tools.evaluate --giro`.
        "preferred_turn": preferred_turn,
    }


def export(runner, out_dir: Path, install: bool, preferred_turn: int = 0) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    runner.export_policy_to_onnx(str(out_dir), filename="policy.onnx")
    # El exportador de torch puede dejar los pesos en policy.onnx.data: se embeben para tener un solo archivo.
    import onnx
    model = onnx.load(str(out_dir / "policy.onnx"), load_external_data=True)
    onnx.save_model(model, str(out_dir / "policy.onnx"), save_as_external_data=False)
    (out_dir / "policy.onnx.data").unlink(missing_ok=True)
    meta = policy_metadata(preferred_turn)
    (out_dir / "policy.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False))
    if install:
        paths.POLICIES_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy(out_dir / "policy.onnx", paths.default_policy())
        shutil.copy(out_dir / "policy.json", paths.default_policy().with_suffix(".json"))
    return out_dir / "policy.onnx"


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--iterations", type=int, default=3000)
    ap.add_argument("--num-envs", type=int, default=4096)
    ap.add_argument("--nthread", type=int, default=32)
    ap.add_argument("--resume", type=Path)
    ap.add_argument("--export", type=Path, help="solo exportar este checkpoint a ONNX")
    ap.add_argument("--install", action="store_true", help="copiar la policy exportada a assets/policies/")
    ap.add_argument("--preferred-turn", type=int, default=0, choices=(-1, 0, 1),
                    help="al exportar: lado al que la policy gira bien en el sitio (+1 izq., -1 der.); el otro se "
                         "resuelve reflejándola. Mídelo con `python -m x2_ultra.tools.evaluate --giro`")
    ap.add_argument("--run-dir", type=Path)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--bc-iters", type=int, default=150, help="iteraciones de clonado DAgger del profesor antes de PPO")
    ap.add_argument("--critic-warmup", type=int, default=40, help="iteraciones de PPO con el actor congelado")
    ap.add_argument("--ppo-std", type=float, default=0.2, help="desviación inicial de exploración tras el clonado")
    ap.add_argument("--difficulty", type=float, default=0.0,
                    help="dificultad inicial del currículo (al reanudar, la que tenía el run; 1 = brazos en todo su rango)")
    args = ap.parse_args(argv)

    from rsl_rl.runners import OnPolicyRunner
    from x2_ultra.rl.env import EnvCfg, X2ArmsFreeEnv

    device = "cuda" if torch.cuda.is_available() else "cpu"
    n_envs = 64 if args.export else args.num_envs
    env = X2ArmsFreeEnv(EnvCfg(num_envs=n_envs, nthread=args.nthread, device=device, seed=args.seed,
                               difficulty_init=args.difficulty))
    run_dir = args.run_dir or (paths.REPO_ROOT / "runs" / datetime.now().strftime("%Y%m%d_%H%M%S"))
    cfg = json.loads(json.dumps(TRAIN_CFG))
    runner = OnPolicyRunner(env, cfg, log_dir=None if args.export else str(run_dir), device=device)
    if args.resume or args.export:
        ckpt = args.export or args.resume
        runner.load(str(ckpt))
        print(f"checkpoint cargado: {ckpt}")
    if args.export:
        out = export(runner, Path(args.export).parent / "exported", args.install, args.preferred_turn)
        print(f"exportado: {out}")
        return
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "policy_meta.json").write_text(json.dumps(policy_metadata(), indent=2, ensure_ascii=False))
    t0 = time.time()
    try:
        first = True
        if args.bc_iters > 0 and not args.resume:
            from x2_ultra.rl.distill import behavior_clone
            actor = runner.alg.actor
            behavior_clone(env, actor, iterations=args.bc_iters)
            actor.distribution.std_param.data.fill_(args.ppo_std)
            runner.save(str(run_dir / "model_bc.pt"))
            export(runner, run_dir / "exported_bc", install=False)
            print(f"clonado terminado: {(time.time() - t0) / 60:.1f} min", flush=True)
            if args.critic_warmup > 0:
                # El crítico empieza sin entrenar: se calienta con el actor congelado y luego se baja el lr.
                for p in actor.parameters():
                    p.requires_grad_(False)
                runner.learn(num_learning_iterations=args.critic_warmup, init_at_random_ep_len=True)
                first = False
                for p in actor.parameters():
                    p.requires_grad_(True)
                runner.alg.learning_rate = 3e-4
                for g in runner.alg.optimizer.param_groups:
                    g["lr"] = 3e-4
        runner.learn(num_learning_iterations=args.iterations, init_at_random_ep_len=first)
    finally:
        out = export(runner, run_dir / "exported", args.install)
        print(f"entrenamiento: {(time.time() - t0) / 3600:.2f} h · policy: {out}")
        env.close()


if __name__ == "__main__":
    main()
