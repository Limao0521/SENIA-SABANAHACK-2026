# Policies de marcha

| Archivo | Origen | Qué mueve | Licencia |
|---|---|---|---|
| `x2_arms_free_walk.onnx` (+ `.json`) | Entrenada en este repo (`python -m x2_ultra.rl.train`) | Piernas + cintura (15 ejes). Brazos, cabeza y manos libres | La del repo |
| `agibot_mcrl_policy.onnx` | AgiBot `mc-rl/x2_deploy_pkg/models/policy.onnx` (AimDK v1.1.0, md5 `a9a5a1653eb77d2f3ca30a66b7d68d3b`) | Las 29 articulaciones (incluidos brazos) | Apache-2.0 (`LICENSE-agibot-mc-rl.txt`) |

`x2_arms_free_walk.json` describe el contrato de observación/acción (ver `src/x2_ultra/control/obs_spec.py`).
