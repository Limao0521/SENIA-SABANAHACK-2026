# 06 · Marcha por RL: la policy de AgiBot y la nuestra ("brazos libres")

## 1. Por qué hace falta una policy propia

AgiBot publica en AimDK (`aimdk/extra/mc-rl`, Apache-2.0) todo el flujo de su policy de marcha: URDF → Isaac Lab →
PPO → ONNX → sim2sim MuJoCo → despliegue en el robot. Su `policy.onnx`:

- obs 500 = 5 pasos × (gyro 3, gravedad 3, orden 3, posición 31, velocidad 31, última acción 29);
- **29 acciones: piernas, cintura y brazos** (todo menos la cabeza), con objetivo `default + 0,25·a` y PD a 200 Hz;
- validada en el robot con vx −0,3…0,5 m/s, vy ±0,3 y wz ±0,2 rad/s;
- entrenada con una penalización por desviar los brazos (`joint_deviation_arms`): los brazos se quedan cerca de la
  postura por defecto.

En el modelo Ultra-N + OmniHand de este repo camina bien (0,34 m/s cuando se le piden 0,4; pelvis a 0,66 m). El
problema llega al mover los brazos: como son parte de su salida, si el usuario los sobrescribe la red observa
posturas que nunca vio y el centro de masas se desplaza. Pruebas con el modelo v1.4.0 (`walk_probe2.py`, rampa de 2 s
hasta la postura, 10 s):

| Postura de brazos | De pie (vx = 0) | Andando (0,3 m/s) | Con "máscara" (la red ve sus propios brazos) |
|---|---|---|---|
| al frente | cae a 2,6 s | cae a 2,5 s | cae a 4,9 s / 4,6 s |
| arriba | cae a 2,3 s | cae a 2,2 s | cae a 4,1 s / 4,2 s |
| saludar | cae a 4,5 s | cae a 3,5 s | aguanta |
| cruz | cae a 3,9 s | cae a 3,4 s | aguanta |

Para "mover los brazos como quieras" hace falta una policy que no controle los brazos y que aprenda a mantener el
equilibrio con cualquier postura de ellos. Es la misma idea que la `cpgtelecon` del MC oficial (ver
[05](05-simulador-oficial.md)), pero entrenada aquí y con un contrato documentado.

## 2. Diseño

| | AgiBot mc-rl | Nuestra "brazos libres" |
|---|---|---|
| Acción | 29 (piernas, cintura, brazos) | **15 (piernas 12 + cintura 3)** |
| Observación | 500 | **430** (misma estructura; la última acción baja a 15) |
| Brazos y cabeza | los mueve la red | objetivos aleatorios suaves durante el entrenamiento; en el simulador, los del usuario |
| Simulador de entrenamiento | Isaac Lab 2.3 (PhysX), 4096 entornos | **MuJoCo 3.15 en CPU** con `mujoco.rollout` (32 hilos), 4096 entornos |
| Modelo | X2 Ultra v1.3.0 con colisiones simplificadas | **Ultra-N v1.4.0 + OmniHand** (manos rígidas; colisiones de malla → cápsulas ajustadas por MuJoCo) |
| PD / pasos | 200 Hz, decimación 4 (50 Hz) | igual (dt 5 ms, PD dentro de MuJoCo) |
| PPO | rsl_rl 5.0, MLP 512-256-128 ELU | rsl_rl 5.5, misma red y mismos hiperparámetros |

Por qué MuJoCo en CPU y no Isaac Lab: el entrenamiento de AgiBot pide Isaac Sim 5.1 + Isaac Lab 2.3.2 y en este PC
están Isaac Lab 3.0 / Isaac Sim 6.0.1 y un entorno 2.2 de Unitree, que no se tocan. MuJoCo es además el motor del
simulador interactivo, así que se entrena con el mismo modelo que se usa (menos salto sim2sim). Rendimiento medido:
~36 000 pasos de policy por segundo con 4096 robots → 2,7 s por iteración de PPO (98 304 muestras).

Comprobación de la física de entrenamiento: la policy oficial de AgiBot, ejecutada en el modelo de entrenamiento
(dt 5 ms, actuadores de posición, cápsulas), camina 2,6 m en 10 s a 0,3 m/s y 4,5 m a 0,5 m/s sin caerse.

### Brazos aleatorios

Cada 0,5-3 s, y por cada robot, se elige un objetivo nuevo para los 14 ejes de brazo y los 2 de cabeza: con
probabilidad 0,25 la postura por defecto y, si no, un punto uniforme en el rango articular escalado por la
dificultad. A veces un brazo se queda quieto mientras el otro se mueve. El paso al objetivo dura 0,3-1,5 s (curva
suave) y el PD de los brazos es el del robot. Los brazos chocan con el cuerpo si llegan, como en la realidad.

### Recompensas (v2, `rl/env.py`)

Todas se multiplican por dt = 0,02 s, como en Isaac Lab.

| Término | Peso | Qué mide |
|---|---|---|
| `track_lin_vel_xy` | 1,5 | exp(−‖v_cmd − v‖² / 0,1) en el marco de rumbo |
| `track_ang_vel_z` | 1,0 (v6: 2,0; v7: 2,0 con 0,1) | exp(−(ω_cmd − ω_z)² / 0,25) (v7: / 0,1) |
| `termination` | −200 | caída (pelvis < 0,3 m o inclinación > 0,8 rad) |
| `feet_air_time` | 0,75 | tiempo en apoyo simple (máx. 0,4 s) si se pide avanzar o ir de lado; el término "biped" de Isaac Lab |
| `gait` | 0,5 | contactos alineados con un reloj de marcha de 0,8 s con pies en contrafase (AgiBot) |
| `feet_clearance` | 0,5 | pie en vuelo a ~3,5 cm de la suela (AgiBot); solo si hay orden |
| `stand_still` | −0,5 | con orden nula, desviación de las piernas respecto a la postura por defecto |
| `base_height` | −10 | (altura de pelvis − 0,66)² |
| `flat_orientation` | −2 | inclinación del tronco |
| `lin_vel_z`, `ang_vel_xy` | −1, −0,1 | rebote y balanceo |
| `action_rate` | −0,03 | cambios bruscos de acción |
| `joint_vel`, `joint_acc`, `energy` | −1e-3, −2,5e-7, −2e-5 | suavidad y consumo (piernas y cintura) |
| `joint_pos_limits` | −2 | fuera del 90 % del rango |
| `hip_deviation`, `waist_deviation` | −0,15, −0,3 | roll/yaw de cadera y cintura lejos de 0 |
| `feet_slide`, `undesired_contacts` | −0,2, −1 | pies que patinan; rodillas, pelvis o manos tocando el suelo |
| `imitation` | 1,0 × (1 − dificultad/0,6) | exp(−media((a − a_profesor)²)/1,0): parecido con las acciones de piernas y cintura de la policy de AgiBot (solo al principio) |

### Aleatorización y currículo

- Órdenes: vx ∈ [−0,6, 1,0] m/s, vy ∈ [−0,4, 0,4], wz ∈ [−1,0, 1,0] rad/s desde el principio. Se cambian cada
  4-8 s. El 12 % de los robots reciben orden nula y el 30 % (en v6, el 20 %) solo girar en el sitio, con
  |wz| ≥ 0,2 hacia un lado u otro al azar.
- Un único **nivel de dificultad** (0 → 1) que sube 0,05 cada 20 s simulados si se cae menos del 20 % y baja si se
  cae más del 50 %. Escala:
  - amplitud de brazos (0 % → 100 % del rango);
  - empujones (cambios de velocidad de hasta ±0,5 m/s cada 3-6 s);
  - retardo de mando (5 → 30 ms);
  - fricción (1,0 → 0,3-1,0);
  - masa extra en el torso (−1…+3 kg), desplazamiento del centro de masas (±3 cm) y ganancias PD (±10 %).
- Ruido de observación como AgiBot (gyro ±0,2, gravedad ±0,05, posición ±0,01, velocidad ±1,5) y sesgo de IMU de
  ±0,05 por episodio.

### Historia: v1 → v2 → v3 (profesor)

v1 usaba los pesos de AgiBot tal cual (bonus por vivir, `action_rate` −0,1, desviación de cadera −1,
`feet_clearance` positivo también parado) y su currículo de órdenes, que arranca en ±0,2 m/s y solo crece si el
seguimiento supera el 80 %. A la iteración 500 (~25 min) aprendió a **quedarse de pie** pero no a andar: velocidad
medida ≈ 0 con cualquier orden y el 16 % caía en 5 s incluso sin empujones. Con órdenes tan pequeñas, quedarse
quieto ya cobra el 67 % del seguimiento, y cada paso torpe arriesga una caída. v2 da todo el rango de órdenes
desde el principio, castiga la caída en lugar de premiar seguir vivo, añade el tiempo de vuelo y suaviza las
penalizaciones que frenaban la exploración. Los checkpoints de v1 siguen en `runs/arms_free_v1/`.

v2 sobrevivía antes (episodios completos de 20 s hacia la iteración 100), pero a la iteración 200 seguía **sin dar un
paso**: doble apoyo el 100 % del tiempo y velocidad 0 con cualquier orden. Con la penalización por caída, explorar
pasos sale caro y PPO prefiere renunciar al seguimiento. Es un mínimo local conocido en humanoides.

v3 añadió un **profesor**: la policy oficial de AgiBot (`rl/teacher.py`, el MLP del ONNX en torch y en lote para los
4096 robots, idéntico al ONNX hasta 6·10⁻⁶). Antes se comprobó que sus acciones de piernas y cintura, con los brazos
movidos por el entorno, caminan en nuestra física (0,49 m/s con orden 0,5; lateral 0,23 con 0,3; sin caídas). No gira
en el sitio. Como recompensa de imitación (núcleo exp(−ECM/0,25)) no bastó: lejos del profesor la recompensa valía
≈0,04, casi sin gradiente, y a la iteración 200 seguía quieta.

**v4 → v5 → v6 → v7 (la instalada es v7, iteración 2100)**: primero **clonado de comportamiento con DAgger** (`rl/distill.py`, 150 iteraciones,
~10 min). La red aprende por regresión supervisada las acciones de piernas y cintura del profesor en los estados
que visita; al principio conduce el profesor y al final conduce la red (β de 1 a 0). Después:

1. desviación de exploración fijada en 0,3;
2. 40 iteraciones de PPO con el actor congelado, para que el crítico aprenda los valores;
3. PPO normal, con lr 3·10⁻⁴ adaptativo, la imitación como guía suave que se apaga a dificultad 0,6, y el
   currículo de dificultad (brazos hasta todo su rango, empujones, retardo, aleatorización).

Las tareas que el profesor no sabe hacer, como girar en el sitio o mantener el equilibrio con los brazos lejos de la
postura por defecto, las aprende PPO con las recompensas de la tarea.

Lección de v4: el profesor **también se cae cuando los brazos se mueven**, incluso con solo el 15 % de amplitud: 1,5
caídas por robot cada 10 s. Clonarlo así copiaba también las caídas (≈340 por tanda de 0,5 s). En v5 la amplitud de
brazos es igual a la dificultad, así que en dificultad 0 los brazos están quietos. El clonado aprende entonces una
marcha limpia: error 0,002, 1-2 caídas por tanda y error de velocidad de 0,05-0,1 m/s. Los brazos solo empiezan a
moverse cuando PPO ya no se cae. Además, la exploración arranca en 0,2 (en v4, 0,3 tiraba al robot).

Lección de v5: a la iteración 500 ya aguantaba cualquier postura de brazos (12/12 escenarios sin caída), pero
**no giraba en el sitio** (−0,02 rad/s con orden 0,8), porque el profesor tampoco sabe y las órdenes de solo giro eran
raras. v6 reanuda v5 desde la iteración 600 conservando la dificultad (`--resume … --difficulty 0.65`). Sube el peso
de `track_ang_vel_z` a 2 y añade un 20 % de órdenes de girar en el sitio (|wz| ≥ 0,2). En 100 iteraciones ya giraba a
0,66 rad/s con orden 0,8 sin perder la robustez con los brazos. El profesor solo existe en el entrenamiento: la
policy exportada es una red propia con el contrato de `obs_spec.py` y 15 salidas.

Lección de v6: el giro era frágil. Con órdenes por debajo de ~0,6 rad/s apenas giraba, y al seguir entrenando lo
perdió (la iteración 1400 y la final, ~1530, ya no giraban). Se instaló la iteración 1000. v7 reanuda esa
iteración en dificultad 1,0 durante 1200 iteraciones, con dos cambios:

- núcleo de giro más estrecho: exp(−err²/0,1) en vez de /0,25, porque con el ancho, quedarse quieto ante una orden
  de 0,5 rad/s ya cobraba el 37 % del término;
- un 30 % de órdenes de girar en el sitio, frente al 20 % de v6.

v7 sigue mejor las velocidades (lateral 0,27 m/s con orden 0,3 frente a 0,21; avanzar 0,88 con orden 0,9 frente a
0,84), pero la zona muerta del giro en el sitio no desapareció. Al medir también los giros a la derecha apareció lo
importante: **cada checkpoint gira en el sitio solo hacia un lado** (sección 4). Se resolvió en ejecución con el
espejo izquierda-derecha.

## 3. Cómo entrenar, exportar e instalar

```bash
scripts/setup.sh --train                                    # torch cu128, rsl-rl-lib, tensorboard, onnx
.venv/bin/python -m x2_ultra.rl.train --iterations 3500 --run-dir runs/mi_run   # clonado + PPO
.venv/bin/tensorboard --logdir runs                         # curvas (Episode_Reward/*, Curriculum/*)
.venv/bin/python -m x2_ultra.rl.train --resume runs/mi_run/model_3000.pt --iterations 2000
.venv/bin/python -m x2_ultra.tools.evaluate --giro --policy-path runs/mi_run/exported/policy.onnx  # lado que domina
.venv/bin/python -m x2_ultra.rl.train --export runs/mi_run/model_5000.pt --install --preferred-turn 1
.venv/bin/python -m x2_ultra.tools.evaluate                 # evaluar la instalada en el modelo completo (--duro)
```

`--preferred-turn` escribe en `policy.json` el lado al que la red gira bien en el sitio (+1 izquierda, −1 derecha,
0 si gira bien a ambos). El simulador usa la red reflejada para el otro lado ([04](04-simulacion-mujoco.md)).

`--install` copia `policy.onnx` (con los pesos embebidos) y `policy.json` (el contrato de `obs_spec.py`) a
`assets/policies/x2_arms_free_walk.*`, que es lo que carga `x2-sim`. Siempre hay que ejecutar con
`env -u PYTHONPATH` o con los scripts de `scripts/`, porque el perfil del usuario exporta el PYTHONPATH de ROS Jazzy.

## 4. Resultados

La tabla completa del checkpoint instalado frente a la policy de AgiBot está en
[07-origen-y-estado.md](07-origen-y-estado.md#resultados-de-la-policy): 13 escenarios de `tools/evaluate.py` más 7
difíciles. La policy "brazos libres" (v7, iteración 2100, con espejo) no se cae en ninguno de los 20, que incluyen:

- todas las poses y brazos aleatorios en todo su rango, parada y andando;
- empujones de 120 N y de 200 N;
- girar con brazos aleatorios.

Avanza de −0,29 a 0,88 m/s, va de lado a 0,27 m/s y gira en el sitio a ±0,72 rad/s con orden ±0,8 y a ±0,94 con
±1,0. La de AgiBot no deja mover los brazos y, en el sitio, con ±0,8 gira −0,42 rad/s a la derecha y nada a la
izquierda.

### Elección del checkpoint

Primero se evaluó cada candidato en los 18 escenarios normales y difíciles; ninguno se cae. El error de seguimiento
de la tabla suma |error vx| + |error vy| + 0,5·|error wz| en los 7 escenarios de velocidad. Después se midió el giro
en el sitio hacia los dos lados (brazos quietos, partiendo de parado, sin espejo, rad/s):

| Candidato | Error de seguimiento | +0,6 | +0,8 | +1,0 | −0,6 | −0,8 | −1,0 |
|---|---|---|---|---|---|---|---|
| v6 it. 1000 (instalada antes) | 0,53 | 0,51 | 0,76 | 0,98 | 0,00 | −0,01 | 0,00 |
| v6 it. ~1530 (final) | — | 0,00 | 0,00 | 0,00 | 0,00 | 0,00 | 0,00 |
| v7 it. 1200 | 0,67 | 0,35 | 0,69 | 0,99 | 0,00 | 0,00 | 0,00 |
| v7 it. 1600 | — | 0,00 | 0,71 | 0,95 | 0,00 | 0,00 | −0,58 |
| v7 it. 1800 | **0,36** | 0,00 | 0,79 | 0,99 | 0,00 | −0,01 | 0,00 |
| v7 it. 2000 | 0,74 | 0,00 | 0,00 | 0,92 | −0,36 | −0,61 | −0,84 |
| **v7 it. 2100** | 0,46 | 0,44 | 0,72 | 0,94 | 0,00 | 0,00 | −0,86 |
| v7 it. 2199 (final) | 0,43 | 0,00 | 0,74 | 0,95 | −0,01 | 0,00 | −0,85 |

Todas tienen un lado bueno, que cambia de un checkpoint a otro. Desde parado, la orden de giro sola no basta para
romper la simetría y empezar a dar pasos; con los brazos moviéndose sí giraban, porque la perturbación hace que el
robot pise. Las evaluaciones anteriores solo pedían giros a la izquierda, así que no lo detectaron; ahora
`evaluate.py` incluye `girar_der_0.8` y `brazos_aleatorios_girando_der`.

Con el espejo ([04](04-simulacion-mujoco.md)), cada red gira a la derecha como lo hace a la izquierda: v7 it. 2100
pasa a ±0,44 / ±0,72 / ±0,94 y v6 it. 1000 a ±0,51 / ±0,76 / ±0,98. Se instaló la **2100** con `preferred_turn = 1`:

- gira a ±0,92-0,95 rad/s con brazos aleatorios (v7 it. 1800: ±0,82-0,90);
- sigue bien las velocidades de las teclas: 0,6 → 0,57; −0,4 → −0,43; lateral −0,3 → −0,29.

La 1800 sigue algo mejor la velocidad, pero en el sitio no gira nada con 0,6 rad/s. La v6 it. 1000 gira
algo mejor despacio, pero va más lenta de lado (0,21 con orden 0,3) y hacia atrás.

Las policies tienen además un **umbral de arranque**: con 0,15-0,2 m/s apenas dan pasos y en lateral no responden.
Con el núcleo lineal exp(−err²/0,1), quedarse quieto ante 0,15 m/s ya cobra el 80 % del término. La demo de la mesa
se acerca por eso a ≥ 0,28 m/s.

| Run | Qué cambia | Resultado |
|---|---|---|
| v1 | receta de AgiBot tal cual + currículo de órdenes | de pie sin andar |
| v2 | recompensas estilo Isaac Lab, todo el rango de órdenes, penalización por caída | de pie sin andar |
| v3 | + recompensa de imitación del profesor | de pie sin andar (núcleo demasiado estrecho) |
| v4 | clonado DAgger + PPO | el clonado camina, pero copia también las caídas del profesor con brazos en movimiento |
| v5 | clonado con los brazos quietos; amplitud de brazos = dificultad | aguanta cualquier postura de brazos; no gira |
| v6 | reanuda v5 con más peso de giro y órdenes de girar en el sitio | brazos libres + giro (solo a la izquierda y con órdenes ≥ 0,6) |
| **v7** | reanuda v6 it. 1000 en dificultad 1 con núcleo de giro 0,1 y 30 % de giros en el sitio | **instalada (it. 2100)** + espejo en ejecución: mejor seguimiento; giro en el sitio a ambos lados |

## 5. Pasos siguientes posibles

- Más iteraciones (AgiBot entrena 40-50 k en una RTX 5090). Si hace falta, se reanuda desde el último checkpoint.
- Simetría en el entrenamiento: aumentado de datos con el espejo, o la pérdida de espejo de rsl_rl, con las mismas
  funciones de `control/locomotion.py`. Así la propia red sería simétrica y sobraría el espejo en ejecución.
- Bajar el umbral de arranque: núcleo lineal más estrecho y más órdenes pequeñas (|v| < 0,3) en el muestreo.
- Terreno irregular, que AgiBot usa con un currículo de rugosidad.
- Despliegue en el robot real: haría falta el camino HAL de `deploy_x2.py` (con el MC parado) y adaptar las
  ganancias reales. **No validado**; la policy es para el simulador.
