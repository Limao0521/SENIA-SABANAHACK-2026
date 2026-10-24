# Despliegue del runtime HRI en el X2 Ultra real (T5)

Backend: `src/x2_ultra/hri_runtime/backends/aimdk.py` (AimdkBackend). **DRY_RUN es el default**:
sin `--arm` sólo registra comandos (nunca publica). Pruebas: `PYTHONPATH=src python -m pytest
tests/test_backends.py -q` (24/24). CLI de humo: `python -m x2_ultra.hri_runtime.backends.aimdk
--seconds 2` (dry-run). **No hay modo grúa/pórtico** (el usuario no dispone de gantry): el
interlock de caída está siempre activo y el bring-up es dry-run → baja velocidad con e-stop → completo.

## 0. Red y entorno (PC ↔ robot)
1. Cable PC↔robot; `nmcli con up "X2 robot"`; verificar `ping 10.0.1.41`.
2. Contenedor ROS 2 **Humble** (como teleop): `RMW_IMPLEMENTATION=rmw_fastrtps_cpp`,
   `ROS_DOMAIN_ID` del robot, workspace aimdk + overlay `/tmp/aimdk_overlay` (McBodyPose).
3. `X2_REAL=robot` (obligatorio para armar; `require_target()` aborta sin él).
4. Batería ≥ 30 % (`MIN_BATTERY`); sólo se omite con `ignore_battery` (pruebas en banco, robot sentado).

## 1. Secuencia de bring-up (no saltar etapas)
| Etapa | Comando | Qué validar |
|---|---|---|
| **Dry-run** | CLI sin `--arm`, red del robot | logs de comandos coherentes; `ros2 topic list` ve `/mc/upper_body_command`, `/aima/mc/locomotion/velocity`, servicios `SetMcInputSource`/`SetMcAction` |
| **Baja velocidad** | `--arm` con `joint_vmax=5, emote_vmax=10`, pies en suelo, ≥2 m libres, **e-stop inalámbrico a mano y una persona dedicada** | READY stream 5 s: tracking ≤ ~0.03 rad de sag, sin `limiter_hit` espurio; 1 emote cinemático: stroke en `t_dispatch` ±50 ms; emote + point; cintura por McBodyPose sin oscilación (deadband c07) |
| **Completo** | límites del spec (10/20 rad/s, accel 40) | gesto continuo; `walk_to` sólo con `cfg.routes` (segmentos abiertos, vx 0.2–0.3) |

En cada etapa: `arm_session()` registra la fuente URS (prioridad 65 < mando 80), pide modo URS y
rearma el limitador con los brazos **medidos** (sin salto inicial).

## 2. Checklist de seguridad (antes de cada etapa armada)
- [ ] Mando/app accesible (prioridad 80 gana a nuestra fuente 65); e-stop inalámbrico probado.
- [ ] Batería ≥ 30 %; robot en STAND/STABLE (fsm 4), player idle.
- [ ] ≥ 2 m libres; 2 personas: una en el e-stop, otra en el teclado.
- [ ] ≤ 2 llamadas de servicio ROS/s (todas por el worker `Svc`, nunca en el hilo de 50 Hz).
- [ ] **Nunca** DAMPING/PASSIVE con el robot de pie.
- [ ] Interlock de caída activo **siempre** (no hay modo grúa): pelvis < 0.62 siempre; tilt > 12° salvo preset busy
      con pelvis ≥ 0.66; tilt > 25° duro. 3 ticks de debounce → DELETE fuente + STAND y rechaza
      todo hasta reset manual.
- [ ] Nunca gestos mientras camina; nunca locomoción con gesto activo (bloqueo mutuo).

## 3. Bloqueos conocidos
- **Salida de URS vía SDK**: `SetMcAction` para salir de URS devuelve MotionBusy (código 9) —
  salir con mando/app; por eso los emotes van **cinemáticos por URS** (CSV de AgiBot) y la ruta de
  presets MC es opcional (`--presets`, layout `v1.1.4` con `input_source`).
- **Head pitch**: McBodyPose sólo expone 4 campos (`twist_waist`, `bend_forward`, `bend_sideways`,
  `turn_head`) — el pitch de cabeza no es canalizable con el firmware actual; se publica yaw.
- **Versión de MC**: robot v1.1.4 (requests con `input_source`) ≠ gemelo v1.0.0 (helper
  `mc_preset_helper.py` en dominio 77, layout `mc_v1.0.0`) — `TwinBackend` ya lo encapsula.
- **Odometría**: `/aima/hal/odom/state` no llega a la PC → `walk_to` es open-loop por tramos
  temporizados (`cfg.routes`); sin ruta configurada, rechaza (`walk_refused`) — usar navegación/app.
- **Gemelo**: no arrancar `x2-twin` si `free -m` < 8 GB disponibles (`twin_memory_ok()`); al
  terminar, STAND_DEFAULT/STABLE y `twin.sh stand`. (En esta entrega: 7.5 GB disponibles → humo
  del gemelo **no ejecutado**, queda pendiente.)

## 4. Qué medir primero (etapa de baja velocidad, con e-stop a mano)
1. **READY 5 s**: error de seguimiento por articulación (esperado ~0.03 rad de sag en hombros),
   jitter del bucle 50 Hz (publicaciones perdidas → `upper_body_command` debe ir a 50 Hz fijos).
2. **Un emote (3001, área 11)**: instante real del stroke vs `t_dispatch` (±50 ms), forma del CSV
   reproducida, mezcla D 0.6 s antes / READY 0.7 s después sin saltos > 0.05 rad.
3. **Limitador**: contar `limiter_hit` en el log; si salta en READY, el vmax está mal o hay
   latencia de medida — no subir límites hasta entenderlo.
4. **Cintura**: ante referencia al bound (0.10/0.05/0.03), la medida no debe pasarse del bound
   (control de signo con compensación de inflight); sin feedback de waist, u = ceros (mantener).
5. **Interlocks**: forzar tilt/pelvis en dry-run (tests `fall_interlock`, `velocity_refused`,
   `walk_refused`) y verificar en la etapa de baja velocidad que el e-stop publica velocidad
   cero + DELETE + STAND.

## 5. Fase 1 a bordo sin actuación (conversación)

Conversación en vivo con el runtime HRI del Club N **sin mover el robot**: micrófono del robot
(`/aima/hal/audio/capture`) → VAD por energía → ASR (contenedor `x2-asr`, socket UNIX
`/tmp/x2_hri_sock/hri_asr.sock`) → FSM + x2-llm (`:8080`) → Supertonic → altavoces (AimDK:
foco de audio + `/aima/hal/audio/playback`). Runner: `src/x2_ultra/hri_runtime/live_robot.py`
(imagen `x2-hri:latest`, `deploy/compose.hri_club.yml`, contenedor `x2-hri-club`).

Seguridad (todas activas a la vez): aborta si existe `X2_REAL` o se pasa `--arm`; exige
`HRI_NO_ACTUATION=1` y `HRIEGO_NO_ACTUATION=1`; import guard que veta `x2_ultra.real`, los
backends aimdk/twin/show, el gemelo, el motor del show, `motion_online` y `mujoco`; `NullBackend`
sin servidor de movimiento (las intenciones motoras se registran como **NO EJECUTADAS**);
nodos ROS solo vía `hriego_v2.robot_io._ros_node` (lista blanca: publicar solo audio, servicios
solo foco de audio); el script se niega si corre `x2-onboard`/`x2-twin` (o HRIEGO `x2-hri`) y
audita los nodos `hriego_*`/`hri_club_*` con `deploy/hri_audit_ros.py`.

```bash
ssh agi@10.0.1.41
cd ~/x2_ultra
deploy/dockerd_x2.sh                                         # tras cada reinicio del robot
# x2-asr debe estar corriendo (o existir deploy/compose.asr.yml) con el socket en /tmp/x2_hri_sock
deploy/hri_club_sin_actuacion.sh arrancar --visit VIP        # LLM + ASR + --check-only + runner + auditoría
deploy/hri_club_sin_actuacion.sh logs                        # transcript, estado FSM, habla, intents, latencias
deploy/hri_club_sin_actuacion.sh estado                      # contenedores + auditoría ROS + últimos turnos
deploy/hri_club_sin_actuacion.sh parar                       # solo el runner (--todo: también LLM y ASR)
deploy/hri_club_sin_actuacion.sh arrancar --visit GUEST --sin-voz   # sin altavoces (mismos tiempos)
```

Turnos en `out_reto2/live/live_*.jsonl`. Ajustes por entorno (compose): `HRI_MIC_CANAL`
(0 o `mean`), `HRI_MIC_QOS`, `HRI_VAD_MARGIN_DB`/`HRI_VAD_MIN_DBFS`/`HRI_VAD_SILENCIO_MS`,
`HRI_PLAYBACK_SR` (16000 por defecto; 0 = 44,1 kHz del TTS). En el PC, sin ROS:
`PYTHONPATH=src:. python -m x2_ultra.hri_runtime.live_robot --entrada stdin --sin-voz --permitir-sin-llm`.
Tests: `PYTHONPATH=src python -m pytest tests/test_hri_live_robot.py -q --noconftest`.

Pendiente de verificar en el robot: formato real del micrófono (se lee de `msg.info` y se
registra al primer mensaje), protocolo del socket ASR dentro de `x2-asr`, contención del foco de
audio con los servicios de AgiBot y `json_schema` strict con llama.cpp b5283.
Fase 2 (identidad real, `HRI_IDENTITY_REAL=1 --camara`): cableada, no operativa todavía (ver
informe).

## 6. Fase 2 a bordo con actuación (conversación + gestos)

Guía paso a paso del deploy (PC → robot → etapas): [DEPLOY_SABANAHACK4.md](DEPLOY_SABANAHACK4.md).

Misma conversación que la fase 1, pero EMOTE/POINT se ejecutan en el robot con `AimdkBackend`
corriendo **a bordo** (en el Orin sí llega `/aima/hal/odom/state`, así que el interlock de caída
ve pelvis e inclinación). Runner: `src/x2_ultra/hri_runtime/live_robot_actuado.py` (imagen
`x2-hri:latest`, `deploy/compose.hri_club_actuado.yml`, contenedor `x2-hri-actuado`).

Alcance de esta fase: brazos por URS a 50 Hz, emotes cinemáticos desde los CSV de AgiBot,
señalamiento, READY entre gestos, cabeza `body_pose`. **No** camina (`walk_to`/velocidad se
rechazan en el runner y el backend no tiene rutas), **no** hay co-speech SONIC (el servidor de
movimiento no corre en el Orin, REPORT §8a) y el D1 es un stub. Cintura bloqueada a la fuerza en
`seco` y `lenta`.

Sincronía: el backend usa reloj monotónico; `GestureSync` retiene los gestos de cada frase y,
cuando la primera frase está sintetizada, fija el inicio del audio a ≥ 0,9 s (preparación del
señalamiento 0,8 s) y despacha el gesto en `inicio + ancla`; el audio espera con `not_before`.

```bash
ssh agi@10.0.1.41 && cd ~/x2_ultra && deploy/dockerd_x2.sh
deploy/hri_club_sin_actuacion.sh parar                       # nada más publicando ni hablando
deploy/hri_club_con_actuacion.sh seco --visit STAFF          # 1) dry-run: [motor] en logs, *_eventos.jsonl al parar
deploy/hri_club_con_actuacion.sh parar
deploy/hri_club_con_actuacion.sh prueba --etapa lenta        # 2) READY 5 s + emote 3001/11 + señal LEFT (primer plano)
deploy/hri_club_con_actuacion.sh arrancar --etapa lenta --visit STAFF   # 3) conversación con gestos a 5/10 rad/s
deploy/hri_club_con_actuacion.sh parar                       # SIGTERM ⇒ vel 0, DELETE fuente URS, STAND
deploy/hri_club_con_actuacion.sh arrancar --etapa completa --visit VIP  # 4) solo tras 3) limpio
```

Cada etapa armada pide escribir `ARMAR` tras el checklist de §2. Seguridad del runner:
`--arm` + `X2_REAL=robot` + `HRI_ACTUATION=1` a la vez; preflight antes de armar (feedback de
brazos, pose de odometría, pelvis ≥ 0,66 m, MC en STAND/URS, ningún otro publicador de brazos o
velocidad, batería); watchdog a 5 Hz (interlock, e-stop o brazos sin feedback > 1 s ⇒ e-stop y
fin); salida siempre por `stop()`. El script se niega si corren `x2-onboard`, `x2-twin`, `x2-hri`
o `x2-hri-club`.

CSV de emotes: `assets/hri/mc_classic/` (`scripts/fetch_hri_assets.sh`) o, si no está, el script
busca `animation_player.yaml` del `mc_param` del propio robot bajo `/agibot` (`MC_CLASSIC_DIR` para
forzarlo). Sin CSV, los EMOTE quedan como `emote_unavailable` y solo hay señalamiento (el
preflight lo avisa).

En el PC (sin ROS): `PYTHONPATH=src:. python -m x2_ultra.hri_runtime.live_robot_actuado --etapa seco
--entrada stdin --sin-voz --permitir-sin-llm`. Tests: `PYTHONPATH=src python -m pytest
tests/test_hri_live_robot_actuado.py -q --noconftest`.

Qué mirar en la etapa lenta: `[prueba] READY` (error medio ≲ 0,03 rad, `limiter_hits` 0), que el
stroke del emote caiga con la palabra ancla, `limiter_hit`/`fall_interlock` en `*_eventos.jsonl`
(`estado` los cuenta) y que `parar` deje el robot en STAND con la fuente URS borrada.
Pendiente de verificar en el robot: ruta real de los CSV en el `mc_param` a bordo, `McBodyPose`
en la imagen `x2-hri` (si falta, la cabeza no gira; el preflight lo dice) y que `RobotLink` y los
nodos de audio de HRIEGO convivan en el mismo proceso (el runner crea el backend primero para que
`rclpy.init()` ocurra una sola vez).

## Configuración por defecto para la primera sesión

| Ajuste | Valor por defecto | Cómo cambiarlo |
|---|---|---|
| `HRI_WAIST_MODE` | `off` (cintura bloqueada, u=0) | Variable de entorno: `HRI_WAIST_MODE=slew` o `lowpass` (con `HRI_WAIST_LP_HZ`, crítico 1.5 Hz por defecto) |
| `HRI_HEAD_CHANNEL` | `body_pose` (sólo giro) | Variable de entorno: `HRI_HEAD_CHANNEL=hal` (giro y cabeceo). Probar `hal` en la prueba de 2 min: ±0.1 rad de giro en URS, leer `/aima/hal/joint/head/state`; si falla, volver a `body_pose` |
| `HRI_IDENTITY_REAL` | `0` (simulado) | Variable de entorno: `HRI_IDENTITY_REAL=1` sólo después de probar cámara y QR reales |
| `AimdkBackend` `dry_run` | `True` (no publica) | Sin `--arm` en la CLI, según la sección 1 de este documento. No hay variable de entorno documentada para este ajuste |

Referencias: `../../out_diag/twin_waist2/COMPARE.md` (cintura), `../../out_diag/twin_v4/COMPARE.md`
(gemelo v4), `README_HRI_CONVERSACIONAL.md` y `RETOS_INTEGRACION_HRI.md` (esta carpeta).
