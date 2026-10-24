# 10 · Simulación y gemelo digital

Antes de mover el robot real, todo se probó en simulación. El repositorio del robot (`robot/`) incluye
el simulador completo del **AgiBot X2 Ultra-N con manos OmniHand** en MuJoCo, el **gemelo digital** con
el controlador oficial de AgiBot y el **escenario 3D del Club N**. Documentación detallada en
`robot/README.md` y `robot/docs/04-simulacion-mujoco.md`, `05-simulador-oficial.md`, `08-gemelo-digital.md`,
`11-reto-2.md` y `16-reto-2-vr.md`.

## Qué hay

| Simulación | Para qué | Comando (desde `robot/`) |
|---|---|---|
| **Simulador MuJoCo** | El X2 Ultra-N con OmniHand: marcha con una policy de RL entrenada en el proyecto (W/A/S/D, Q/E), brazos por sliders o cinemática inversa, 10 poses, manos con tacto simulado | `scripts/run_sim.sh` |
| **Recepción del Club N (reto 2)** | Club 3D estilo hotel con el robot en el mostrador y el perro D1; saluda según el visitante y lo guía por tres rutas; modo huésped en primera persona y conversación por voz | `scripts/run_club.sh` (`--hri` con reconocimiento facial y LLM local) |
| **Club N con gafas** | Con una Meta Quest 3, quien las lleva es el visitante que entra al club (check-in facial en la webcam) | `scripts/run_club_vr.sh` |
| **Escenario del reto 1** | Dos robots en una tarima con pantalla de vídeo | `scripts/run_show.sh` |
| **Simulador oficial de AgiBot** | El controlador de movimiento (MC) oficial + MuJoCo en Docker | `official_sim/sim.sh` (`scripts/setup.sh --official`) |
| **Gemelo digital** | El MC oficial manejando nuestro Ultra-N con la misma interfaz que el robot real (articulaciones, IMU, cámaras, LiDAR, audio): el backend del robot se valida aquí antes del X2 | `official_sim/twin.sh` |

## Cómo se usó para validar el sistema real

- **Backend del robot en el gemelo**: rampas de entrada y salida de gestos, gesto co-verbal, señalamiento y
  modos de cintura, sin caídas en ninguna corrida (`robot/docs/reto-2/REPORT_DEPLOY_X2.md`,
  `robot/out_diag/twin_v4/COMPARE.md`, `robot/out_diag/twin_waist2/COMPARE.md`).
- **Emotes propios**: 0 autocolisiones en el modelo oficial del robot, con un control positivo que demuestra
  que el detector sí detecta choques (`robot/scripts/check_emotes_mujoco.py`).
- **Recepción completa** en el club 3D: identidad → saludo → conversación → guía, headless y con vídeo
  (`robot/docs/reto-2/README_HRI.md`).

## Instalación

Linux (probado en Ubuntu 22.04/24.04) con Python 3.10+; GPU opcional (render y entrenamiento).

```bash
git clone --recursive <url-de-este-repo>      # --recursive trae el URDF oficial (submódulo)
cd sabanahack-entrega/robot
scripts/setup.sh                              # .venv + URDF + MJCF exportado
scripts/run_sim.sh                            # H en la ventana muestra los controles
```

Si clonaste sin `--recursive`, `scripts/setup.sh` inicializa el submódulo del URDF por ti.

| Recurso | Dónde | Cómo se obtiene |
|---|---|---|
| URDF y mallas oficiales del X2 (Mulan PSL v2) | `robot/third_party/agibot_x2_urdf` | Submódulo fijado a `575cc6b` de [AgibotTech/agibot_x2_urdf](https://github.com/AgibotTech/agibot_x2_urdf) |
| Perro guía AgiBot D1 | `robot/third_party/agibot_d1` | `scripts/fetch_d1.sh` (lo llama `setup.sh`) |
| Bailes de LinkCraft (opcional) | `robot/assets/motions/` | `scripts/fetch_linkcraft_dance.py` — no se redistribuyen |
| Binarios del simulador oficial (~600 MB) | `~/robots/x2_simulation` | `scripts/setup.sh --official` |
| Policies de marcha | `robot/assets/policies/` | Incluidas (la oficial con su licencia Apache 2.0) |
