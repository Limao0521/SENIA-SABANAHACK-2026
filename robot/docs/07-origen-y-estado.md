# 07 · Origen, estado verificado y pendientes

## 1. Qué se trajo de `g1_teleop` y qué no

El trabajo previo con el X2 está en `~/projects/g1_teleop`, rama `feature/agibot-x2-sim` (commiteado desde el 19/10).
Este repo **no toca** `g1_teleop`; copia o reescribe lo útil y deja fuera todo lo que depende de gafas VR.

| En g1_teleop | En x2_ultra | Notas |
|---|---|---|
| `robots/x2/sim.sh`, `docker/Dockerfile`, `fetch_assets.sh`, `parallel_download.py` | `official_sim/` | Rutas del repo, imagen y contenedor propios (`x2-ultra-sim`) y comando `teclado` nuevo; Dockerfile con `python3-tk` |
| `robots/x2/official_sim_probe.py`, `upper_body_demo.py` | `official_sim/` | iguales |
| `robots/x2/make_arm_demo.py` (Pinocchio) | `official_sim/make_arm_demo.py` | reescrito con la IK de MuJoCo de este repo |
| `robots/x2/profile.py` (contrato AimDK) | `src/x2_ultra/robot/joints.py`, `omnihand.py` | mismos órdenes y topics, verificados otra vez contra AimDK |
| `robots/x2/kinematics.py` (IK con Pinocchio) | `src/x2_ultra/control/arm_ik.py` | reimplementado con MuJoCo: sin dependencia, mismo modelo que el simulador |
| `contracts/tactile.py`, `adapters/robots/x2/tactile.py` | `src/x2_ultra/robot/tactile.py` | lector de tacto de solo lectura, sin contratos del teleop |
| `docs/x2-official-simulator.md` y README de `robots/x2` | `docs/05-simulador-oficial.md` | ampliado con lo que hace el MC por dentro |
| `simulation/x2_dynamic.py` + `apps/x2_live_sim.py` ("simulación dinámica asistida") | — | **sustituido**: allí la marcha era una trayectoria guionizada **con una fuerza de soporte en la pelvis** (hasta 650 N) y el agarre se hacía con un *weld* activado por contacto; aquí la marcha la hace una policy RL sin ayudas y el agarre es por fricción |
| `simulation/x2_preview.py`, `apps/x2_sim_preview.py` | — | vista cinemática para el visor XR |
| `retargeting/omnihand.py`, `xr/*`, `presentation/xr_hud.py`, TeleVuer, gateway v2 | — | **fuera por requisito**: dependen de gafas VR / teleop |
| `adapters/robots/x2/aimdk_gateway.py`, `aimdk_ros_transport.py` | — | parte del gateway de seguridad del teleop; se puede retomar si se teleopera el X2 real |

## 2. Verificado en esta sesión

| Qué | Resultado |
|---|---|
| Modelo Ultra-N + OmniHand (MJCF oficial + manos del URDF) | compila; 45,18 kg, 87 GDL, 51 actuadores; solo se solapaban cabeza y torso (excluido) |
| Manos | 10 ejes por mano siguen las órdenes (error < 0,01 rad en 1 s); 12 mimic; 8 poses renderizadas y revisadas; pinza a 13 mm entre puntas |
| Poses de brazo | 10 poses revisadas en vistas frontal y lateral; jarras y aplaudir resueltas por IK (error ≤ 4 mm) |
| IK | 96 % de éxito en posición y 75 % en pose completa con objetivos aleatorios alcanzables desde una sola semilla; 1-3 ms por solución |
| Motor sin ventana | ~12× tiempo real con el modelo completo |
| App Qt (en modo `offscreen`) | teclas simuladas: W hace andar (0,52 m/s medidos con orden 0,6), Q gira, 1 saluda, I/U mueven la mano por IK, G cierra la mano; capturas de las 5 pestañas revisadas |
| App en el escritorio X11 real (no solo `offscreen`) | abre la ventana y corre; requería `libxcb-cursor0`, que faltaba en el sistema (se precarga una copia local) |
| Policy de AgiBot en este simulador | 0/13 caídas; avanza bien (0,45 m/s con orden 0,5); en el sitio, con ±0,8 rad/s gira −0,42 a la derecha y nada a la izquierda; no deja mover los brazos |
| Policy de AgiBot con brazos sobrescritos | se cae con brazos al frente o arriba, incluso parada (tabla en [06](06-locomocion-rl.md)) |
| Física del entorno de entrenamiento | la policy de AgiBot camina en él 4,5 m en 10 s a 0,5 m/s |
| MJCF exportado (`models/`) | carga con MuJoCo desde su carpeta |
| Tarea completa (`tools/demo_mesa.py`) | girar, ir a la mesa, IK hasta la botella (error 5-10 mm), agarre, levantarla 14 cm y retroceder con ella sin caerse, en 20 s (test `test_table_demo_picks_up_bottle`) |
| Espejo de la policy (`tests/test_mirror.py`) | la observación reflejada coincide con la del estado reflejado en MuJoCo; red siempre reflejada: 0 caídas en 18 escenarios; 0/16 caídas en pruebas de 30 s alternando el sentido de giro |
| Q / E en el sitio (test `test_turns_in_place_both_ways`) | gira hacia los dos lados: ±0,94 rad/s con orden ±1 |
| Agarre (objeto soltado dentro de la mano) | agarre cilíndrico 9/9, pinza 8/9 con cono elíptico + `impratio` + noslip; desde arriba no, porque el pulgar toca la mesa |
| Simulador oficial + teclado (`teleop_selftest.py`, pórtico puesto) | fuente `sdk_` registrada; con W 3 s las piernas se mueven hasta 0,26 rad y el MC pasa a `LOCOMOTION_DEFAULT` |
| Imagen Docker `x2-ultra-sim:humble` | construida (Tk 8.6 + rclpy); `apt` con `Check-Date=false` porque el reloj del PC va ~2 h 47 min atrasado |
| Repo | subido como **privado** a <https://github.com/juanulloa2050/x2_ultra> |
| Gemelo digital (2026-10-12) | el MC oficial mantiene de pie y hace andar a nuestro Ultra-N; HAL a 500/333 Hz y ×1,00 de tiempo real; cámaras, LiDAR y audio con los topics reales; URS emulado; los ejemplos oficiales de AimDK funcionan sin cambios ([08](08-gemelo-digital.md)) |
| Robot real: `andar` (2026-10-20) | en el X2 físico, por cable: marcha W/S/A/D/Q/E con la locomoción oficial del MC, 7 niveles de velocidad, offroad (`LOCOMOTION_STEP`) y cuclillas (`/aima/mc/body_pose`); cinco sesiones sin caídas ni damping ([09](09-robot-real.md)) |
| Robot real: `movimientos` | lista desde el robot 29 gestos, 18 LinkCraft (5 de cuerpo entero) y 8 bailes de fábrica (sin voltereta ni saltos); 12 pruebas sin ROS; **aún sin reproducir en el robot** |
| Robot real: `brazos` | en el X2 físico (20/10): los dos brazos por separado, articulación a articulación, en URS, hasta 0,4 rad por eje, error de seguimiento medio 0,064 y máximo 0,10 rad, con la locomoción del MC activa y sin damping; también en el gemelo y con pruebas sin ROS |

<a id="resultados-de-la-policy"></a>
## 3. Resultados de la policy "brazos libres"

Evaluación en el modelo completo del simulador (dedos articulados, dt 2 ms) con `python -m x2_ultra.tools.evaluate`.
En los escenarios de brazos, la policy de AgiBot no los mueve (los controla ella), así que su "no cae" no es
comparable.

| Escenario | Orden (vx, vy, wz) | AgiBot oficial: medida | **Brazos libres (v7, it. 2100, con espejo)**: medida |
|---|---|---|---|
| de pie 10 s | 0, 0, 0 | sin caída | sin caída |
| avanzar | 0,5, 0, 0 | 0,45 m/s | 0,46 m/s |
| avanzar rápido | 0,9, 0, 0 | 0,79 m/s | 0,88 m/s |
| retroceder | −0,3, 0, 0 | −0,23 m/s | −0,29 m/s |
| lateral | 0, 0,3, 0 | 0,27 m/s | 0,27 m/s |
| girar a la izquierda en el sitio | 0, 0, 0,8 | 0,00 rad/s | **0,72 rad/s** |
| girar a la derecha en el sitio | 0, 0, −0,8 | −0,42 rad/s | **−0,72 rad/s** |
| avanzar girando | 0,4, 0, 0,5 | 0,37 m/s · 0,44 rad/s | 0,37 m/s · 0,47 rad/s |
| las 10 poses de brazos, de pie (22 s) | 0, 0, 0 | (brazos de la policy) | **sin caída** |
| las 10 poses, caminando (22 s) | 0,4, 0, 0 | (brazos de la policy) | **sin caída**, 0,35 m/s |
| brazos aleatorios en todo su rango, de pie (20 s) | 0, 0, 0 | (brazos de la policy) | **sin caída** |
| brazos aleatorios, caminando (20 s) | 0,35, 0, 0,2 | (brazos de la policy) | **sin caída**, 0,27 m/s · 0,18 rad/s |
| empujones de 120 N cada 3 s, caminando | 0,4, 0, 0 | sin caída | sin caída |

Escenarios difíciles (`--duro`), solo para la policy "brazos libres":

| Escenario | Orden (vx, vy, wz) | Medida |
|---|---|---|
| empujones de 200 N cada 2,5 s, caminando | 0,4, 0, 0 | sin caída, 0,33 m/s |
| brazos aleatorios, andando rápido | 0,7, 0, 0 | sin caída, 0,68 m/s |
| brazos aleatorios, girando a la izquierda en el sitio | 0, 0, 0,6 | sin caída, 0,31 rad/s |
| brazos aleatorios, girando a la derecha en el sitio | 0, 0, −1,0 | sin caída, −0,91 rad/s |
| brazos aleatorios, de lado | 0, 0,3, 0 | sin caída, 0,26 m/s |
| las 10 poses, avanzando y girando | 0,3, 0, 0,5 | sin caída, 0,25 m/s · 0,48 rad/s |
| brazos aleatorios y empujones de 120 N | 0,3, 0, 0 | sin caída, 0,26 m/s |

Los datos completos están en `docs/media/eval_agibot.json`, `eval_brazos_libres.json` y
`eval_brazos_libres_duro.json`. El giro se mide integrando la velocidad de rumbo paso a paso; una primera versión
de la herramienta envolvía el rumbo a ±180° y daba valores falsos en giros de más de media vuelta. Los valores de
AgiBot difieren algo de la versión anterior de esta tabla: desde entonces el modelo usa cono de fricción elíptico
con *noslip* (para agarrar) y compensación de gravedad en brazos y cabeza.

Como referencia, la policy de AgiBot con los brazos sobrescritos se caía en 2-5 s (ver [06](06-locomocion-rl.md)).
Historia de la policy instalada:

1. clonado DAgger del profesor y PPO hasta la iteración 600 con el currículo de brazos (v5);
2. v6: más peso al seguimiento de giro y órdenes de girar en el sitio;
3. v7: núcleo de giro más estrecho y un 30 % de giros en el sitio, iteración 2100;
4. en ejecución, el espejo izquierda-derecha para girar hacia el lado que la red no domina.

La elección entre checkpoints está en [06](06-locomocion-rl.md#elección-del-checkpoint). Puntos débiles:

- en el sitio gira poco con órdenes pequeñas (0,44 rad/s con 0,6);
- desde parado no arranca con órdenes lineales por debajo de ~0,25 m/s.

## 4. Pendiente / no verificado

- **Ventana de teclado del simulador oficial con el robot suelto**: el camino de órdenes está probado (autotest con
  el pórtico puesto, arriba), pero falta probar con el pórtico suelto y pulsando las teclas en la ventana Tk. Pasos:
  `sim.sh up && sim.sh start && sim.sh stand`, soltar el pórtico (Equality → gantry), `sim.sh teclado` y mantener W.
- **La policy "brazos libres" no se ha probado en el robot real**: es para el simulador; desplegarla exigiría el camino
  HAL con el MC parado, como `deploy_x2.py` de AgiBot, y validar ganancias, retardos e IMU. En el robot se usa la
  locomoción oficial del MC (`scripts/robot_real.sh andar`, que sí funciona, [09](09-robot-real.md)).
- **URS**: en el robot (MC lx2501 v1.1.4) funciona para los brazos ([09](09-robot-real.md)); falta andar en URS (brazos
  y marcha a la vez), las OmniHand (el robot no las tiene montadas) y salir de URS por el SDK (hoy solo con mando o
  app). Para simularlo no hay MC x86 con URS publicado: el gemelo lo emula ([08](08-gemelo-digital.md)).
- **`movimientos` sin reproducir en el robot**: gestos, LinkCraft y bailes de fábrica solo se han listado.
- En el robot real, `/aima/hal/odom/state` no llega al PC (la ventana de `andar` no muestra la velocidad medida) y
  los modos de velocidad MEDIUM/HIGH del MC sólo se pueden cambiar con la app (`SetMcLocomotionSpeedMode` no está en
  el AimDK público).
- Discrepancias en las fuentes de AgiBot: masa (39 kg en AimDK, ~45 en el README del URDF) y roll de tobillo (±17° en
  el URDF, ±15° en AimDK para el Ultra-N).
- Tacto real de la OmniHand: códigos uint8 sin calibración de fábrica; la equivalencia con las fuerzas del simulador
  está por medir.

## 5. Correcciones de esta sesión

- Durante un tiempo se dio por bueno el giro con Q/E midiendo solo giros a la izquierda. Al medir los dos sentidos
  apareció el fallo: la policy instalada entonces (v6 it. 1000) no giraba a la derecha en el sitio, así que E no
  hacía nada partiendo de parado. Se corrigió con el espejo, y ahora la evaluación y los tests miden los dos
  sentidos.

- En la conversación se dijo por un momento que el Ultra y el Ultra-N tenían cuello de 2 ejes "según los URDF". Es
  **incorrecto**: el URDF v1.3.0 trae `head_pitch_joint`, pero en el Ultra original no está activo. Ultra = 1 eje,
  **Ultra-N = 2 ejes** (AimDK §1.2 y §5.1.9). El robot del usuario es el Ultra-N, como se había dicho.
