# 09 · El X2 Ultra real: marcha WASD y brazos por articulación

Dos programas para mover el **robot real** por la interfaz pública de AimDK, más una comprobación de solo lectura:

| Programa | Qué mueve | Modo del MC | Lanzar |
|---|---|---|---|
| `check` | nada (solo lectura) | cualquiera | `scripts/robot_real.sh check` |
| `andar` | el robot entero con la locomoción oficial del MC: marcha, offroad y cuclillas | `STAND_DEFAULT` / `LOCOMOTION_DEFAULT` / `LOCOMOTION_STEP` | `scripts/robot_real.sh andar` |
| `movimientos` | gestos, movimientos de LinkCraft y bailes de fábrica | `STAND_DEFAULT` | `scripts/robot_real.sh movimientos` |
| `brazos` | cada brazo por separado, articulación a articulación | `UPPERBODY_REMOTE_SPLIT` (URS) | `scripts/robot_real.sh brazos` |

El código está en `src/x2_ultra/real/`: `control.py` (lógica sin ROS, probada en `tests/test_real_control.py`),
`link.py` (ROS 2 / AimDK), `walk.py`, `arms.py`, `motions.py`, `check.py` y `ui.py` (ventanas Tk). Parten de lo aprendido con el
robot en g1_teleop el 19/10/2026 (`docs/x2-physical-arms-2026-10-19.md` de ese repo): ese gateway es más general
(IPC, supervisor, XR); estos programas son independientes y solo usan el teclado.

> **Estado (20/10/2026): los dos programas funcionan en el robot real.** `andar`: marcha W/S/A/D/Q/E, niveles de
> velocidad, offroad y cuclillas (sección 9). `brazos`: los dos brazos por separado, articulación a articulación, en
> URS con la locomoción del MC activa (sección 10). También probados en el gemelo digital (sección 8); la lógica tiene
> 39 pruebas sin ROS en `tests/test_real_control.py`. `movimientos` (sección 4) lista desde el robot sus gestos,
> LinkCraft y bailes; pendiente de reproducirlos en el robot (12 pruebas sin ROS en `tests/test_real_motions.py`).

## 1. Antes de cada sesión

1. Batería cargada (los programas se niegan a armar por debajo del 30 %; `andar` se desarma al 20 %).
2. Parada inalámbrica conectada. Una persona con el **mando** a mano (prioridad 80: siempre retoma el control) y
   otra al lado del robot. Espacio libre alrededor para `andar`.
3. Cable a develop0: el FastDDS del robot solo escucha en 10.0.1.41 y por Wi-Fi no hay descubrimiento. El PC necesita
   una IP 10.0.1.x; en este PC el perfil "G1 robot" la lleva como secundaria (desde el 20/10), así que el mismo cable
   sirve para el G1 y el X2. En otro PC: `nmcli con up "X2 robot"` (10.0.1.2/24).
4. Robot de pie en `STAND_DEFAULT` con el mando o la app. Nunca Zero-Torque ni Damping con el robot de pie.
5. `scripts/robot_real.sh check`: imprime lo que ve el PC (modo, fuente, de pie, batería, reloj del robot, manos,
   posturas de brazo, margen al tope) y si cada programa podría armarse. Sale con 0 si los dos podrían.

**Preparación del PC (una vez):**

1. Imagen `x2-ultra-sim:humble` (`official_sim/sim.sh image`) y AimDK compilado en `~/robots/x2_simulation/aimdk`
   (`official_sim/fetch_assets.sh`).
2. `scripts/build_aimdk_overlay.sh` (≈1 min; comprobar con `--check`): un `aimdk_msgs` con `McBodyPose`, que el AimDK
   público retiró, para las cuclillas. Sin él `andar` funciona igual pero F / R quedan desactivadas. Qué hace y cómo
   actualizarlo: `scripts/aimdk_overlay/README.md`.
3. Perfil de red `X2 robot` en `enp4s0` (10.0.1.2/24, sin ruta por defecto, `autoconnect=no`).

Los programas corren en un contenedor Humble con la red del PC, el repo en solo lectura y `logs/real/` escribible;
la ventana se abre en la pantalla del PC.

## 2. Marcha: `scripts/robot_real.sh andar`

| Tecla | Acción |
|---|---|
| Intro | armar (comprueba el estado y registra la fuente de entrada) |
| W / S | avanzar / retroceder (mantener) |
| A / D | paso lateral izquierda / derecha |
| Q / E | girar a la izquierda / derecha |
| Mayús | ×1,5 (sin pasar de los topes) |
| + / − | subir / bajar el nivel de velocidad (7 niveles; empieza en el 3) |
| O | offroad sí / no (`LOCOMOTION_STEP`), con el robot parado |
| F / R | agacharse / levantarse en cuclillas (mantener; parado) |
| Espacio | parar y desarmar |
| 1 | pedir `STAND_DEFAULT` (solo parado) |
| Esc | salir (si se usó offroad, lo quita antes) |

La ventana se pone roja mientras está armado. Cómo manda: registra la fuente `sdk_x2_ultra_andar_<id>` con
`SetMcInputSource` (prioridad 65 por defecto, más que la app, 60, y menos que el mando, 80) y publica
`McLocomotionVelocity` a 50 Hz, con ceros cuando no hay tecla. Cada eje vale 0 o al menos el umbral de arranque del
MC y sube en rampa (0,5 m/s², 1 rad/s²) hasta la velocidad del nivel; al soltar, 0 en el acto.

**Niveles de velocidad.** Factor ×0,6 · 0,8 · **1,0** · 1,25 · 1,5 · 1,75 · 2,0 sobre las velocidades elegidas, sin
pasar de los topes (los límites del modo LOW del MC: adelante 0,8, atrás 0,5, lateral 0,8 m/s, giro 1,0 rad/s).
Bajando, lateral y giro no bajan de lo que el robot necesitó para arrancar desde parado (0,5 m/s y 0,6 rad/s):

| Nivel | Adelante | Atrás | Lateral | Giro |
|---|---|---|---|---|
| 1 | 0,20 | 0,20 | 0,50 | 0,60 |
| 3 (inicio) | 0,30 | 0,20 | 0,50 | 0,60 |
| 5 | 0,45 | 0,30 | 0,75 | 0,90 |
| 7 | 0,60 | 0,40 | 0,80 | 1,00 |

Los modos de velocidad del propio MC (LOW, MEDIUM, HIGH) los cambia la app con `SetMcLocomotionSpeedMode`, que no
está en el AimDK público: el programa trabaja dentro de LOW, el de fábrica.

**Offroad (O).** Es el modo `LOCOMOTION_STEP` ("越野"); al entrar, el MC pasa solo al modo de velocidad STEP y lo
deshace al salir. Visto en el MC del gemelo y confirmado en el robot: pedirlo deja memorizado el estilo de marcha (en reposo el MC sigue
informando `STAND_DEFAULT` y la próxima marcha sale en `LOCOMOTION_STEP`); pedir `STAND_DEFAULT` no lo quita y pedir
`LOCOMOTION_DEFAULT` sí. Tras andar, el MC pasa ≈2–4 s en `MOVING` y rechaza cualquier cambio de modo ("Current state
is moving"), así que el programa espera a que esté `STABLE` (hasta 6 s) y mientras tanto no deja andar. Activar =
[`STAND_DEFAULT`] + `LOCOMOTION_STEP`; desactivar y salir = `LOCOMOTION_DEFAULT` + `STAND_DEFAULT`. La línea
"última marcha vista" de la ventana dice en qué modo anduvo de verdad el MC.

| Opción | Por defecto | Rango |
|---|---|---|
| `--vx` / `--vx-atras` | 0,3 / 0,2 m/s (nivel 3) | 0,2–0,8 / 0,2–0,5 |
| `--vy` | 0,5 m/s (desde parado el MC pide ≈0,5–0,7) | 0,2–0,8 |
| `--wz` | 0,6 rad/s (0,45 no arrancó en el sitio) | 0,1–1,0 |
| `--rapido` | 1,5 | 1–3 |
| `--prioridad` | 65 | 20–69 |
| `--sello` | `robot` (hora del robot estimada, como los ejemplos de AimDK que corren en él) | `robot`, `cero`, `pc` |
| `--duracion` | 600 s armado | 5–3600 |
| `--ignorar-bateria` | — | armar aunque no llegue `/aima/hal/pmu/state` |

**Se desarma solo** (y manda ceros) si: se pierde el estado del MC más de 0,5 s; el MC sale de
`STAND_DEFAULT`/`LOCOMOTION_DEFAULT`/`LOCOMOTION_STEP` (caída, amortiguamiento, otro modo); entra en su protección de
seguridad (`fsm` SAFE); deja de informar de pie (`body_status`≠1, salvo durante una cuclilla: ver abajo); arranca el
reproductor de movimientos (gestos del agente de voz); tras 1 s de marcha el MC no
selecciona nuestra fuente o pasa a otra (p. ej. alguien toca el mando); batería < 20 %; se pierde la hora del robot;
o se acaba `--duracion`. Si la ventana se congela (>0,25 s sin refrescar) o pierde el foco, se manda 0 sin desarmar.
Al salir: ceros durante 0,3 s, levantarlo si quedó en cuclillas, quitar el offroad si se usó y `DELETE` de la fuente. Los únicos modos que puede pedir
son `STAND_DEFAULT`, `LOCOMOTION_STEP` y `LOCOMOTION_DEFAULT`: nunca `DAMPING`, `PASSIVE` ni `JOINT_DEFAULT`.

**Cuclillas (F / R).** No están en el AimDK público: el control de postura (`McBodyPose`) figura en su changelog
como retirado. Pero el robot (v1.1.4) sigue teniendo `/aima/mc/body_pose` (`McBodyPose`: `source`, `stand_height`,
`bend_forward`, `bend_sideways`, `twist_waist`, `raise_head`, `turn_head`), lo publican el mando (`rc`) y la app
(`app_proxy`) y lo escucha el MC, con el mismo arbitraje de fuentes que la velocidad. Grabado el 20/10 mientras se
agachaba con la app (`docs/metrics/robot_real_20261020/body_pose_app-*.log.gz`): **`stand_height` es una velocidad, no una altura**; −0,5
mantenido baja `servo_status.squat_offset` a 0,1/s hasta −0,5 (abajo del todo, ≈5 s, `squat_limit=1`); +0,5 lo sube
hasta 0 (`squat_limit=2`); 0 lo deja donde está. El MC siguió en `STAND_DEFAULT`/`STABLE` todo el rato.

`scripts/build_aimdk_overlay.sh` compila en `~/robots/x2_simulation/aimdk_overlay` un `aimdk_msgs` con todas las
definiciones del SDK más `McBodyPose` copiado del robot (`scripts/aimdk_overlay/`), sin tocar el AimDK instalado;
`robot_real.sh` lo carga si existe. Comprobado: el PC recibe así el `McBodyPose` del mando. Sin overlay, la ventana
dice "cuclillas no disponibles" y lo demás funciona igual.

F / R mandan ∓0,5 mientras se mantienen (como la app) y 0 al soltar. Sólo con el robot parado en `STAND_DEFAULT`
estable; agachado no anda ni cambia de modo. Al bajar, el MC reclasifica la postura por la altura: `body_status`
pasa de 1 a 4 (sentado), 5 (cuclilla) y 0 (desconocido) (robot, 20/10); durante la cuclilla esos valores no desarman,
pero tumbado (2, 3), cayendo (6) o el MC en su protección (`fsm` SAFE) sí. **Al salir se levanta** antes de soltar
el control (hasta 8 s, con el MC en `STAND_DEFAULT`; en el robot, de −0,41 a 0 en 4,3 s). `brazos` no arma con el
robot agachado.

## 3. Brazos: `scripts/robot_real.sh brazos`

| Tecla | Acción |
|---|---|
| Intro | armar: desde `STAND_DEFAULT` pide URS y espera a que el MC lo confirme |
| Tab o ← / → | brazo activo: izquierdo / derecho |
| 1 – 7 | articulación: hombro pitch, hombro roll, hombro yaw, codo, muñeca yaw, muñeca pitch, muñeca roll |
| ↑ / ↓ (o W / S) | mover la articulación activa en sentido + / − mientras se mantiene |
| H | volver a la postura inicial (la medida al armar) |
| Espacio | parar |
| R | reanclar tras un fallo de seguimiento |
| P | pedir `STAND_DEFAULT` (solo en la postura inicial y parado) |
| Esc | salir |

La ventana muestra, por articulación, el valor medido, el objetivo, el error, el ancla y los límites permitidos.
Signos útiles (FK del URDF, marco del torso): **hombro pitch negativo lleva la mano hacia delante**; codo más
negativo lo dobla más; el hombro roll izquierdo se abre con valores positivos y el derecho con negativos.

Cómo manda: `/mc/upper_body_command` a 50 Hz desde que se arma, con la fuente `sdk_x2_ultra_brazos_<id>`
registrada. El objetivo parte de la postura medida al armar y solo se mueve la articulación activa, a
`--velocidad`. Cada articulación queda dentro de su rango URDF menos 0,05 rad y a no más de `--envolvente` del ancla;
si al armar ya está dentro de ese margen (el hombro roll izquierdo suele quedar a ≈0,02 rad de su tope), solo puede
alejarse del tope. La cabeza se queda en la postura medida al armar. Sin manos instaladas se manda
`hand_sub_mode=1` con `[0, 0]` (con 0 el MC descarta el mensaje entero, brazos incluidos); con OmniHand, la postura
de las manos medida al armar (`hand_sub_mode=2`).

| Opción | Por defecto | Rango |
|---|---|---|
| `--velocidad` | 0,15 rad/s | hasta 0,35 |
| `--envolvente` | 0,4 rad desde el ancla | hasta 0,6 |
| `--seguimiento` | 0,12 rad | 0,05–0,3 |
| `--prioridad` | 65 (la que funcionó el 19/10) | 20–69 |
| `--sello` | `cero` (la configuración con la que los brazos respondieron el 19/10) | `cero`, `robot`, `pc` |
| `--ignorar-bateria` | — | — |

**Protecciones.** Si un eje se aparta del objetivo más de `--seguimiento` durante 0,5 s (el hundimiento normal bajo
gravedad es ≈0,03 rad) o dejan de llegar estados de brazo frescos, el objetivo **se congela**: se sigue mandando quieto
y no se mueve hasta pulsar R (el objetivo pasa a la postura medida, dentro de los mismos límites). Si el MC sale de
URS, deja de informar de pie, arranca el reproductor de movimientos o se pierde su estado, **se deja de mandar**: en
URS los brazos se quedan en su última postura (observado el 19/10). Antes de armar se exige que no haya otro
publicador en `/mc/upper_body_command` (el tren superior no tiene arbitraje de fuentes).

**Salir.** Esc manda diez órdenes quietas y borra la fuente; el MC sigue en URS con los brazos donde estén. Para
terminar limpio: H (volver a la postura inicial) y después P (`STAND_DEFAULT`), o volver con el mando/la app. Empezar
cada sesión desde `STAND_DEFAULT`: si no, cada armado parte de la postura hundida y baja un escalón.

## 4. Movimientos: `scripts/robot_real.sh movimientos`

Gestos, movimientos de LinkCraft y bailes de fábrica, elegidos de una lista. `--listar` sólo imprime el catálogo
(consulta, no mueve nada).

| Tecla | Acción |
|---|---|
| ↑ / ↓ o ratón | elegir |
| Intro o doble clic | reproducir; los de **cuerpo entero** piden Intro otra vez sobre el mismo en 5 s |
| 1 | pedir `STAND_DEFAULT` (sin nada reproduciéndose; p. ej. después de `brazos`) |
| Esc | salir |

| Lista | Servicio | Qué hay en el robot (20/10) |
|---|---|---|
| Gestos | `SetMcPresetMotion` (AimDK §5.1.6) | los 29 combinados movimiento + zona documentados: saludar, dar la mano, beso, aplaudir, saludo militar, corazón, abrazo, animar, levantar manos, adiós, ola, chocar los cinco, cruzar brazos, reverencia… (brazos y tronco; las piernas no se mueven) |
| LinkCraft | `GetRobotResources` + `ExecuteActionResource` (§5.1.7) | 18 recursos: 5 de cuerpo entero (`onnx`: Baile clásico, Gangnam style f/m, Kung fu, Peina al niño) y 13 de brazos (Saludo, Venia, Despedida, Agradecimiento, la demo Cosco 0–7…) |
| Bailes de fábrica | `GetMcMotions` + `SetMcMotion` (fuera del SDK; overlay) | tipo MIMIC, cuerpo entero: Boxeo, Charlestón, Dingding, Abanico, Fútbol, Kuaile Chongbai (y su versión vídeo), Niuniu |

**No se ofrecen** `M_ROLL_FORWARD` (voltereta: rueda por el suelo), `M_LONG_JUMP` ni `M_HIGH_JUMP` (saltos): el robot
sale del suelo o acaba en él. Tampoco los 126 identificadores de animación de `GetMcMotions` que la guía no documenta.

**Cuándo deja reproducir:** MC en `STAND_DEFAULT` estable (`fsm` 4, acción `RUNNING`), de pie (`body_status`=1), sin
cuclilla, con el reproductor de movimientos libre y batería ≥30 %. Al primer movimiento registra la fuente
`sdk_x2_ultra_movimientos_<id>` (prioridad 65) y la usa en las peticiones que la llevan; la borra al salir.

**Cómo se pide cada uno.** Gestos: `motion` + `area`, `interrupt=false` y sello 0 (en este servicio el sello es la
hora de reproducción; 0 = ya). LinkCraft: clave y versión del recurso y `meta` `{"resource_type": "BODY_MONTION"}` si
la clave contiene `onnx`, si no `ARM_MONTION`; el éxito se lee en `header.message == "Play success"` porque, según la
guía, `header.status` es siempre FAILURE en este firmware. Bailes: `SetMcMotion` con `tag` y tipo MIMIC (2),
`interrupt=false`, `play_timestamp` 0. Todas las peticiones se reenvían como los ejemplos (la respuesta puede
perderse); con `interrupt=false` una copia que llegue con el robot ya en marcha se rechaza.

**No hay tecla de parar.** Cortar un movimiento de cuerpo entero a medias puede dejar el robot en una postura
inestable, así que termina solo; ante un problema, el mando. La ventana muestra lo que está reproduciendo el MC
(`motion_status`) y avisa si informa caída (`body_status` 2, 3, 6) o protección (`fsm` SAFE). El programa nunca pide
`DAMPING`, `PASSIVE` ni `JOINT_DEFAULT`; el único modo que pide es `STAND_DEFAULT` (tecla 1). Durante un baile de
cuerpo entero el MC usa su propio controlador de imitación en vez del de marcha; al terminar vuelve a mantenerse de
pie (comprobar en las primeras pruebas).

Hace falta el overlay (`scripts/build_aimdk_overlay.sh`) sólo para los bailes de fábrica; sin él la lista no aparece y
gestos y LinkCraft funcionan igual.

## 5. Cómo empezar una sesión de `brazos`

1. `check` en verde. Robot en `STAND_DEFAULT` y de pie (no en cuclillas); si se acaba de usar `andar`, el programa
   espera solo a que el MC esté estable antes de pedir URS.
2. Armar y empezar con un movimiento corto (p. ej. codo ↓ ~0,3 s ≈ 0,05 rad) mirando que "medido" siga a "objetivo".
3. Movimientos mayores de uno en uno; para acabar, H y P (vuelve a `STAND_DEFAULT`). Con Esc el robot queda de pie en
   URS y `andar` no arma hasta volver a `STAND_DEFAULT`.

## 6. Problemas frecuentes

| Síntoma | Causa y solución |
|---|---|
| `check`: "no llega el estado del MC" | sin cable o sin IP 10.0.1.x en el PC: revisar el cable y `ip -4 addr`; si falta, `nmcli con up "X2 robot"` |
| no arma: "el reproductor de movimientos del MC no está libre (2, '6001')" | el MC está haciendo un gesto (del agente de voz o la app); esperar a que termine o pararlo en la app |
| no arma: "batería al 29 %" | por debajo del 30 % no se arma: cargar. `--ignorar-bateria` es sólo para cuando la batería no llega |
| se desarma: "el MC no sigue nuestra fuente: manda app_proxy" | la app u otra fuente con más prioridad está activa: cerrarla o `--prioridad 69` |
| se desarma: "sin hora del robot" | la estimación del reloj exige ≥20 estados de brazo en 2 s con dispersión ≤50 ms (14–38 ms por cable): revisar el cable o `--sello cero` |
| A/D o Q/E no dan un paso desde parado | por debajo de ≈0,5 m/s lateral y ≈0,6 rad/s de giro el MC no arranca: no bajar `--vy`/`--wz` |
| O tarda unos segundos | tras andar el MC pasa 2–4 s en `MOVING` y rechaza cambios de modo; el programa espera solo |
| F / R no hacen nada | robot andando o recién parado (esperar), fuera de `STAND_DEFAULT`, o falta el overlay (`scripts/build_aimdk_overlay.sh`) |
| `andar` no arma: "MC en UPPERBODY_REMOTE_SPLIT" | se salió de `brazos` con Esc: relanzar `brazos` y H + P, o `STAND_DEFAULT` con el mando o la app |
| `brazos` congela el objetivo ("seguimiento") | el brazo va >0,12 rad por detrás durante 0,5 s; en movimiento el retraso normal llega a ≈0,10: con `--velocidad` alta, subir `--seguimiento` (hasta 0,3) |
| tecla 1 / P: "el MC está ocupado con una acción o movimiento (code=9)" | `MotionBusy` al pedir `STAND_DEFAULT` desde URS (visto el 20/10 con la app conectada y su animación `3038` como último movimiento, aunque el reproductor decía libre). El programa reintenta 12 s; si sigue, volver a `STAND_DEFAULT` con el mando o la app. Salir de URS por el SDK aún no se ha conseguido en el robot |
| `movimientos`: "hace falta STAND_DEFAULT" | el MC sigue en URS (después de `brazos`) o en otro modo: tecla 1 |
| `movimientos`: no aparece la lista de bailes de fábrica | falta el overlay (`scripts/build_aimdk_overlay.sh`, que trae `SetMcMotion`/`GetMcMotions`) |
| `movimientos`: LinkCraft responde "The robot is playing" / "not in ready posture" | otro movimiento en curso, o el robot no está de pie en `STAND_DEFAULT` |
| la línea `odom` sale con "—" | `/aima/hal/odom/state` no llega al PC desde el robot; no afecta al control |

## 7. Registros

Cada sesión escribe `logs/real/<programa>-<fecha>.jsonl` (fuera de git; las del 20/10 están copiadas en
`docs/metrics/robot_real_20261020/`): argumentos, armado/desarmado con motivo, peticiones de modo,
y a 10 Hz la orden, el estado del MC, la batería y (en brazos) objetivo, medido y motivo de congelación.

## 8. Probar sin robot: el gemelo digital

```bash
official_sim/twin.sh up && official_sim/twin.sh start      # MC oficial + nuestro MuJoCo (1-4 min)
official_sim/twin.sh stand && official_sim/twin.sh suelta  # de pie y sin pórtico
scripts/robot_real.sh check  --gemelo
scripts/robot_real.sh andar  --gemelo --ignorar-bateria     # el gemelo no publica batería
scripts/robot_real.sh brazos --gemelo --ignorar-bateria
```

Con `--gemelo` el mismo programa corre dentro del contenedor del gemelo (sin red), con `X2_REAL=gemelo`; sin él,
`X2_REAL=robot` y la red del PC. Los programas se niegan a arrancar sin una de las dos.

Resultado del 20/10/2026 en el gemelo, manejando los controladores sin ventana (y las dos ventanas abiertas aparte):

- `andar`: el MC seleccionó nuestra fuente, pasó solo a `LOCOMOTION_DEFAULT` y W (0,3 m/s) dio 0,45 m/s de odometría;
  al desarmar volvió a `STAND_DEFAULT` y la fuente se borró. Q/E en el sitio y S apenas movieron el robot: es la
  limitación conocida del MC v1.0.0 en simulación (docs/08, sección 7), no de las órdenes, que llegaron con nuestra
  fuente seleccionada. En el robot era el umbral de arranque (sección 9). El offroad se probó también aquí: pedir
  `LOCOMOTION_STEP` lo deja memorizado, `LOCOMOTION_DEFAULT` lo quita, y en `MOVING` el MC rechaza los dos.
- `brazos`: URS pedido y confirmado; codo izquierdo −0,30 rad (seguido con 0,012 rad de error), hombro pitch derecho
  −0,30 rad (mano al frente), el hombro roll izquierdo pegado a su tope no se movió, H volvió a la postura inicial
  (error ≤0,02 rad), P devolvió el MC a `STAND_DEFAULT` y la fuente se borró. El gemelo tiene OmniHand, así que la
  rama "sin manos" (`hand_sub_mode=1`) solo está cubierta por las pruebas sin ROS y por el ensayo del 19/10.

Diferencias con el robot que estas pruebas no cubren: el reloj del robot (por delante del PC: ≈6,8 s el 19/10,
1,5–1,8 s el 20/10; en el gemelo el desfase es 0), las cuclillas (el gemelo no carga el overlay de `McBodyPose`), la pérdida de respuestas de servicio entre equipos (el código reenvía como los ejemplos), la batería
(`/aima/hal/pmu/state`, sin probar desde el PC) y el MC v1.1.4 con URS nativo.

## 9. `andar` en el robot (20/10/2026)

Robot de pie en el suelo, por cable, MC lx2501 v1.1.4 en velocidad LOW (lateral y giro ±1,0). Registros (copia
comprimida en el repo, con índice): [`docs/metrics/robot_real_20261020/`](metrics/robot_real_20261020/README.md).

| Sesión | Batería | Resultado |
|---|---|---|
| 1 | 35 → 32 % | Al principio el MC reproducía el movimiento `6001` (`player_state=2`, no está en la lista pública) y no armó hasta que terminó. El MC seleccionó nuestra fuente (prioridad 65) en el primer W y la mantuvo; pasó solo a `LOCOMOTION_DEFAULT` y volvió a `STAND_DEFAULT` al parar. **Adelante (0,3 y 0,45 m/s) y atrás (0,2) funcionaron. Lateral (0,25 y 0,375 m/s) y giro (0,3 y 0,45 rad/s, hasta 7 s) no dieron ni un paso**, con las órdenes llegando y la fuente seleccionada |
| 2 | 28 → 31 % | Con lateral 0,5 m/s y giro 0,6 rad/s, **lateral y giro funcionaron**. Quedan como valores por defecto y como mínimo al bajar de nivel. Dos veces el MC arrancó por su cuenta el gesto `1011` y el programa se desarmó solo, como debe (la protección del reproductor de movimientos funcionó en el robot) |
| 3 | 61 → 58 % | **Niveles de velocidad y offroad funcionaron**: tras O el MC anduvo en `LOCOMOTION_STEP`. Se grabó una cuclilla hecha con la app para implementar F / R |
| 4 | 54 % | F / R: el robot se agachó, pero el programa se desarmó dos veces porque `body_status` pasó a 4 y 0 al bajar. Esc lo levantó solo de −0,41 a 0 en 4,3 s |
| 5 | 54 % | Con la corrección, **cuclilla completa** (0 → −0,50 → 0) sin desarmarse; `body_status` pasó por 1, 4, 5 y 0, siempre en `STAND_DEFAULT` |

En ninguna sesión el robot cayó ni entró en damping o par cero; todas las salidas fueron limpias (ceros, fuente
borrada, robot de pie). `/aima/hal/odom/state` no llega al PC, así que la ventana no muestra la velocidad medida.

## 10. `brazos` en el robot (20/10/2026)

Primera sesión del programa en el robot real, de pie en el suelo, por cable, sin manos (`hand_sub_mode=1`, `[0, 0]`),
batería 58 %, valores por defecto (0,15 rad/s, envolvente 0,4 rad, sello `cero`, prioridad 65). Registro:
`docs/metrics/robot_real_20261020/brazos-20261020-174005.jsonl.gz`.

- Desde `STAND_DEFAULT` el MC aceptó URS y siguió las órdenes durante los 2 min de sesión, siempre en
  `UPPERBODY_REMOTE_SPLIT`, de pie (`body_status`=1) y sin congelaciones. **La locomoción del MC siguió activa** (piernas,
  cintura y equilibrio); en ningún momento hubo damping ni par cero.
- Brazo izquierdo: hombro pitch, roll y yaw y muñeca yaw hasta el límite de la envolvente (0,4 rad), codo 0,20, muñeca
  pitch 0,39 y roll 0,34 rad. Brazo derecho: hombro pitch 0,4 rad. Cada eje se movió por separado.
- Error de seguimiento (objetivo − medido, el peor eje): medio 0,064 rad, máximo 0,10 rad, por debajo del umbral de
  congelación (0,12). Mayor que el hundimiento en reposo (≈0,03) porque incluye el retraso del brazo en movimiento.
- Salida con Esc: diez órdenes quietas, fuente borrada; el robot quedó de pie en URS, estable, con los brazos quietos.
