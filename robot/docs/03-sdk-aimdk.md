# 03 · SDK: AimDK v1.1.0

AimDK es el SDK oficial del X2: mensajes y servicios ROS 2 (`aimdk_msgs`) más ejemplos en C++ y Python. Versión
local: `aimdk v1.1.0-gfaf4c94` en `~/robots/x2_simulation/aimdk` (documentación HTML en `docs/cn/_build/html`, se
sirve con `sh run_docs.sh 5789`; en línea en <https://x2-aimdk.agibot.com>). **La versión del SDK tiene que
coincidir con el firmware del robot.**

## 1. Arquitectura

```
 tu programa (PC2 en contenedor, o tu PC por cable)
      │  ROS 2 Humble · DDS FastRTPS (RMW_IMPLEMENTATION=rmw_fastrtps_cpp)
      ▼
 servicios /aimdk_5Fmsgs/srv/*  ·  topics /aima/* y /mc/*
      ▼
 PC1 · MC (control de movimiento: policies RL, máquina de estados, arbitraje de fuentes)
      ▼
 HAL (/aima/hal/joint/*/command|state, IMU) ──> motores, manos, sensores
```

- Paquetes de `src/`: `aimdk_msgs` (396 mensajes y 99 servicios; vienen precompilados para x86_64 y aarch64),
  `examples` (C++), `py_examples` (Python), `robotop`, `ruckig` y `topics_and_services`.
- Compilar: `source /opt/ros/humble/setup.bash && cd aimdk && colcon build && source install/local_setup.bash`.
- Ejecutar un ejemplo: `ros2 run py_examples get_mc_action` (o `ros2 run examples …` para C++).
- Entorno: Ubuntu 22.04 + ROS 2 Humble + Python ≥ 3.8 + C++17. Este PC tiene Ubuntu 24.04 + Jazzy, así que AimDK
  corre en un contenedor Humble (`official_sim/`).
- Dónde desarrollar: en **PC2** (Orin NX) dentro de un contenedor (en v1.1.0 aún se permite sin contenedor; desde
  v1.2.0 es obligatorio) o en un PC propio conectado por cable a 10.0.1.41. **Nunca en PC1** (10.0.1.40).

## 2. Modos de movimiento (§5.1.1)

| Modo (`action_desc`) | Atajo del ejemplo | Qué hace |
|---|---|---|
| `PASSIVE_DEFAULT` | PD | par cero (libre), salvo efectores |
| `DAMPING_DEFAULT` | DD | amortiguado |
| `JOINT_DEFAULT` | JD | de pie en control de posición (preparación) |
| `STAND_DEFAULT` | SD | de pie con equilibrio activo; desde v0.8.0 unificado con la locomoción |
| `LOCOMOTION_DEFAULT` | LD | andar/correr |
| `HEAD_ONLY` | HO | solo cabeza (Ultra/Ultra-N) |
| `UPPERBODY_REMOTE_SPLIT` | URS | cabeza, brazos y manos los manda el usuario; el MC mantiene equilibrio y marcha |
| `LOCOMOTION_STEP` | — | offroad ("越野"); no está en la tabla de la guía pero `SetMcAction` lo acepta (robot real, 20/10) |

El enum `McAction` y la configuración del MC (`action_ruler.yaml`) tienen más acciones que la guía:
`SIT_DOWN_DEFAULT → SIT_JOINT_DEFAULT → SIT_UP_DEFAULT` (sentarse en una silla de 35–40 cm con respaldo; sin ella
el robot cae), `LIE_DOWN_DEFAULT`, `GET_UP_DEFAULT`, `RUN_DEFAULT`… Este repo sólo usa `LOCOMOTION_STEP`.

Transiciones típicas: `DAMPING → PASSIVE → JOINT_DEFAULT → STAND_DEFAULT ↔ LOCOMOTION_DEFAULT`. Antes de
`STAND_DEFAULT` el robot debe estar de pie con los dos pies en el suelo.

- Consultar: `/aimdk_5Fmsgs/srv/GetMcAction` → `info.action_desc` e `info.status.value` (0 IDLE, 100 RUNNING,
  200 TRANSITION).
- Cambiar: `/aimdk_5Fmsgs/srv/SetMcAction` con `header`, `source` (no vacío) y `command.action_desc`.
  `response.header.code` 0 = aceptado. Rechazos frecuentes: 4 `InvalidPosture`, 6 `SecureLevelForbid` (protección
  activa, por ejemplo tras una caída), 7 `Starting` (el MC aún arranca), 9 `MotionBusy`, 10 `NoTransitionPath`.
  Medido: justo después de andar el MC pasa ≈2–4 s en FSM `MOVING` (aunque ya informe `STAND_DEFAULT`) y rechaza
  cualquier cambio con `status=2` "Current state is moving, can not set to unstable action"; hay que esperar a FSM
  `STABLE` (4). Publicar velocidades cero alarga esa espera (≈3,8 s frente a ≈1,9 s sin publicar).
  Desde URS, `STAND_DEFAULT` fue rechazado con code 9 `MotionBusy` ("当前动作正在播放…请稍后重试", hay una acción
  reproduciéndose) con `motion_status` libre y la animación `3038` de la app como último movimiento (robot, 20/10).
  La guía de URS sólo explica cómo entrar (`STAND_DEFAULT` → URS); el 19/10 se salió siempre con el mando o la app.
- **Protección de seguridad**: tras una anomalía se rechaza todo salvo `PASSIVE`/`DAMPING` (siempre permitidos y
  que la rearman) y `JOINT_DEFAULT` (con restricciones de postura). El SDK no puede activarla ni borrarla.

## 3. Fuentes de entrada y arbitraje (§5.1.3)

Las órdenes continuas (velocidad, pose) pasan por un árbitro de fuentes. **Antes de mandar velocidad hay que
registrar una fuente propia**:

- `/aimdk_5Fmsgs/srv/SetMcInputSource`: `action.value` 1001 ADD, 1002 MODIFY, 1003 DELETE, 2001 ENABLE,
  2002 DISABLE; `input_source` con `name`, `priority` (0-100) y `timeout` [ms].
- Fuentes del sistema (no tocarlas ni usar sus nombres): `linkswarm` 90 (exclusiva), `rc` mando 80, `vr` 70,
  `app_proxy` 60, `interaction` 50, `pnc` 40.
- Nombres propios con prefijo **`sdk_`** (p. ej. `sdk_x2_ultra_keyboard`). Prioridad recomendada 20-39; hay que subir
  a 40+ para quitarle el control a la app o a 80+ para el mando.
- Reglas: una fuente nueva toma el control si está registrada y habilitada, manda una orden no nula, y además no hay
  fuente actual, la actual ha caducado o la nueva tiene más prioridad. Las órdenes nulas no reinician el temporizador.
- `/aimdk_5Fmsgs/srv/GetCurrentInputSource` dice quién manda.

## 4. Locomoción (§5.1.2)

`/aima/mc/locomotion/velocity` · `McLocomotionVelocity` · BEST_EFFORT + VOLATILE · recomendado 50 Hz:

| Campo | Unidad | Signo |
|---|---|---|
| `header` | — | sello de tiempo |
| `source` | — | el nombre de tu fuente registrada |
| `forward_velocity` | m/s | + adelante |
| `lateral_velocity` | m/s | + izquierda |
| `angular_velocity` | rad/s | + antihorario (izquierda) |

- Es control de velocidad en lazo abierto: el robot no sigue la velocidad con precisión ni garantiza trayectorias.
  Para posicionarse con precisión, usar la navegación.
- Límites del modo de velocidad (en `/aima/mc/common/state` → `speed_status`). LOW (por defecto): adelante −1,0 a
  +0,8 m/s, lateral ±1,0, giro ±1,0. MEDIUM sube el avance a +1,2 y HIGH a +1,8.
- **Timeouts**: a los 200 ms sin orden la velocidad pasa a 0; a los 2 s andando sin orden vuelve a `STAND_DEFAULT`.
- **Umbral de arranque** desde parado, aproximado y dependiente del firmware: avance ~0,1 m/s, lateral ~0,5-0,7 m/s,
  giro ~0,03-0,05 rad/s. El ejemplo `mc_locomotion_velocity` pide 0 o 0,2-1,0 m/s y 0 o 0,1-1,0 rad/s. En el
  simulador oficial, girar en el sitio solo con velocidad angular no está garantizado. **Medido en el robot (v1.1.4,
  20/10)**: adelante 0,2–0,3 m/s y atrás 0,2 arrancan; lateral 0,375 m/s y giro 0,45 rad/s no dieron ni un paso;
  0,5 m/s y 0,6 rad/s sí.
- **Modos de velocidad** (LOW de fábrica, MEDIUM, HIGH; STEP sólo en offroad): `SetMcLocomotionSpeedMode` no está en
  el AimDK público (la app sí lo usa); se leen en `speed_status` del estado o con `GetMcLocomotionSpeedStatus`.
- **Offroad** (`LOCOMOTION_STEP`): al entrar el MC pasa solo al modo de velocidad STEP. Pedirlo deja memorizado el
  estilo de marcha: en reposo el MC sigue informando `STAND_DEFAULT` y la siguiente marcha sale en `LOCOMOTION_STEP`.
  `STAND_DEFAULT` no lo quita; `LOCOMOTION_DEFAULT` sí (y deja el MC en ese modo en reposo, desde donde no acepta
  `LOCOMOTION_STEP`: antes hay que volver a `STAND_DEFAULT`). Visto en el gemelo y confirmado en el robot.
- Ejemplos: `mc_locomotion_velocity` (5 s a la velocidad que pidas), `keyboard` (curses, por pasos de 0,2) y
  `set_mc_input_source`.

## 5. Estado del MC (§5.1.4)

`/aima/mc/common/state` (`McCommonState`, 10 Hz, BEST_EFFORT + TRANSIENT_LOCAL): fuente actual, acción y su estado,
FSM (`fsm_state.current_state`: 0 UNKNOWN, 1 PREPARE, 2 START, 3 FINISH, 4 STABLE, 5 MOVING, 6 SAFE, 7 SPECIAL,
8 TEST), postura (`body_status`: 0 desconocida, 1 de pie, 2/3 tumbado boca arriba/abajo, 4 sentado, 5 en cuclillas,
6 cayendo), estado de la cuclilla (`servo_status.squat_enable`, `squat_offset` 0 = de pie … −0,5 = abajo,
`squat_limit`), reproductor de movimientos (`motion_status`), tipo de mano a cada lado (1000 OmniHand, 2000 OmniPicker), offsets de cabeza y cintura y el modo de
velocidad con sus límites.

### Postura del cuerpo y cuclillas: `/aima/mc/body_pose` (fuera del SDK público)

El changelog de AimDK da por retirado el control de postura (`McBodyPose`), pero el robot (v1.1.4) lo mantiene: lo
publican el mando (`rc`, ceros) y la app (`app_proxy`) y lo escucha el MC, con el mismo arbitraje de fuentes que la
velocidad. Campos: `header`, `source`, `stand_height`, `bend_forward`, `bend_sideways`, `twist_waist`, `raise_head`,
`turn_head`. Grabado mientras se agachaba con la app (20/10): **`stand_height` es una velocidad**: −0,5 mantenido baja
`squat_offset` a 0,1/s hasta −0,5 (≈5 s); +0,5 lo sube hasta 0; 0 lo deja donde está. El MC sigue en `STAND_DEFAULT`
/ `STABLE`, pero `body_status` va pasando por 1 → 4 → 5 → 0 según la altura. Para usarlo desde el PC:
`scripts/build_aimdk_overlay.sh` (aimdk_msgs con la definición copiada del robot). Los otros campos no se han probado.

### Gestos, LinkCraft y bailes de fábrica (§5.1.6, §5.1.7 y fuera del SDK)

- **Gestos** (`SetMcPresetMotion`): `motion` + `area` (1 izq., 2 der., 3 ambas, 11 cuerpo superior) entre los 29
  combinados de la guía; sólo desde `STAND_DEFAULT`; sin arbitraje de prioridad: con `interrupt=false` el segundo se
  rechaza mientras se reproduce el primero. En este servicio `header.stamp` es la hora de reproducción (0 = ya).
- **LinkCraft** (`GetRobotResources`, `ExecuteActionResource`, servidos por app_proxy): la clave con `onnx` es de
  cuerpo entero (`meta` `{"resource_type": "BODY_MONTION"}`), si no de brazos (`ARM_MONTION`). El éxito se lee en
  `header.message == "Play success"`: `header.status` es siempre FAILURE en este firmware. El robot tenía 18 (20/10).
- **Bailes de fábrica** (`GetMcMotions`, `SetMcMotion`, `McMotion`; no están en el SDK, sí en el robot): la lista
  trae tags de tipo MIMIC (cuerpo entero: `M_CHARLESTON_DANCE`, `M_FAN_DANCE`, `M_NIUNIU_DANCE`, `M_KUAILECHONGBAI`,
  `M_DINGDING`, `M_BOXING`, `M_FOOTBALL`, `M_ROLL_FORWARD`, `M_LONG_JUMP`, `M_HIGH_JUMP` y los LinkCraft `onnx`) y
  126 identificadores de tipo ANIMATION (los gestos y otros sin documentar, p. ej. `6001`, que reproduce el agente de
  voz). `SetMcMotion` lleva `input_source`, `motion` (`tag` + tipo), `interrupt` y `play_timestamp` (0 = ya).
- Lo que esté reproduciendo el MC se ve en `motion_status` de `/aima/mc/common/state` (`player_state` 0 libre,
  2 reproduciendo; `motion` = tag o identificador).

## 6. Tren superior (§5.1.5)

En modo `UPPERBODY_REMOTE_SPLIT`: `/mc/upper_body_command` · `UpperBodyCommandArray` · BEST_EFFORT · ~50 Hz.

```
MessageHeader header      # stamp a menos de 200 ms del reloj del MC (0 = usar hora de recepción)
string source             # cualquier texto no vacío
uint8 hand_sub_mode       # 1 pinza (2 valores), 2 OmniHand articular (20), 3 OmniHand gestos (4)
float64[2] head_pos       # solo se aplica en HEAD_ONLY
float64[14] arm_pos       # izquierdo 7 + derecho 7 [rad]
float64[] hand_pos
```

Orden de brazo: `shoulder_pitch, shoulder_roll, shoulder_yaw, elbow, wrist_yaw, wrist_pitch, wrist_roll` (la
muñeca es yaw-pitch-roll, al revés que en el G1). Ejemplo: `ros2 run py_examples upper_body_control
{head|claw|joint|gesture}`. Las manos, en detalle, en [02-omnihand.md](02-omnihand.md).

## 7. Control articular de bajo nivel (HAL, §5.1.9)

- `/aima/hal/joint/{leg,waist,arm,head}/command` (`JointCommandArray`): por eje `position` [rad], `velocity`,
  `effort` [N·m], `stiffness` (kp) y `damping` (kd).
- `/aima/hal/joint/{…}/state` (`JointStateArray`, 500 Hz; la cabeza a 333 Hz): `position`, `velocity`, `effort`,
  `error_code` y el estado del grupo (1 DAMPING, 2 POWERDOWN, 3 DISABLE, 4 DISCONNECT).
- Orden: pierna `hip_pitch, hip_roll, hip_yaw, knee, ankle_pitch, ankle_roll` (izquierda 6 + derecha 6); cintura
  `yaw, pitch, roll`; brazo como arriba; cabeza `yaw, pitch` (en el Ultra original solo el yaw está activo).
- **Sin timeout**: si el emisor deja de publicar, el robot mantiene la última orden. Hay que publicar a frecuencia
  fija y mandar una postura segura al salir.
- Compite con el MC: varios ejemplos piden `aima em stop-app mc` en PC1. Es lo que hace el despliegue RL de AgiBot
  (`mc-rl/x2_deploy_pkg/deploy_x2.py`), que manda los 29 ejes por HAL a 200 Hz con su propia máquina de estados.
- `GetAllJointState` devuelve todo de una vez; su campo `reponse` no está habilitado.

## 8. Sensores (HAL)

| Topic | Tipo | Frecuencia |
|---|---|---|
| `/aima/hal/imu/torso/state` (IMU de cadera) · `/aima/hal/imu/chest/state` | `sensor_msgs/Imu` | 500 Hz |
| `/aima/hal/sensor/rgbd_head_front/{rgb_image,depth_image,…}` | `Image`/`CameraInfo` | 30 Hz |
| `/aima/hal/sensor/stereo_head_front_{left,right}/rgb_image[/compressed]` | `Image` | 10 Hz |
| `/aima/hal/sensor/rgb_head_rear/rgb_image[/compressed]` | `Image` | 10 Hz |
| `/aima/hal/sensor/lidar_chest_front/lidar_pointcloud` | `PointCloud2` | 10 Hz |
| `/aima/hal/sensor/touch_head` | `TouchState` | 100 Hz |
| `/aima/hal/sensor/gnss` | `NavSatFix` | 1 Hz |
| `/aima/hal/joint/hand/state` | `HandStateArray` (posición + tacto) | ~200 Hz |
| `/aima/hal/odom/state` (simulador oficial) | `nav_msgs/Odometry` | — |

## 9. Otros módulos

Movimientos predefinidos (`SetMcPresetMotion`, con prioridad/interrupción propias), acciones LinkCraft, voz
(TTS, micrófonos), pantalla, luces, SLAM y navegación, mapas, diagnóstico (FASM, códigos de fallo) y estado del
sistema. Los ejemplos están en `py_examples` (`play_tts`, `play_emoji`, `navigation`, `slam`, `preset_motion_client`…).

## 10. Normas de seguridad de AgiBot

- No desarrollar ni ejecutar en PC1. No modificar el sistema de PC2 (SO, drivers, DDS) fuera del contenedor.
- **No lanzar muchos nodos ROS de golpe** con el robot de pie o andando (≤ 2 nodos/s): el descubrimiento DDS
  congestiona la red y el robot puede perder el equilibrio. Mejor lanzarlos en `PASSIVE`/`DAMPING`.
- Los servicios ROS entre máquinas no son fiables: los ejemplos reintentan cada llamada (hasta 8 veces con 0,25 s);
  hay que copiar ese patrón (lo hacen `official_sim/keyboard_teleop.py` y `official_sim_probe.py`).
- Probar primero en el simulador. No usar modos no documentados.
- Datos propios en `$HOME` (`/home/agi`), salvo `~/aimdk*`. Un cambio de firmware formatea el resto, y el reset de
  fábrica borra también `$HOME`.

## 11. Este repo y AimDK

| Pieza | Para qué |
|---|---|
| `x2_ultra/robot/joints.py`, `omnihand.py` | órdenes de arrays SDK (pierna, cintura, brazo, cabeza, manos), rangos y límites |
| `x2_ultra/robot/tactile.py` | lectura del tacto OmniHand (`HandStateArray`) sin publicar nada |
| `x2_ultra/real/` + `scripts/robot_real.sh` | robot real: marcha WASD, niveles, offroad y cuclillas (`andar`), brazos en URS (`brazos`), `check` de solo lectura ([09](09-robot-real.md)) |
| `scripts/build_aimdk_overlay.sh` | `aimdk_msgs` con lo que el robot tiene y el SDK no (`McBodyPose`, `SetMcMotion`, `GetMcMotions`, `McMotion`) |
| `x2_ultra/real/motions.py` | `scripts/robot_real.sh movimientos`: gestos, LinkCraft y bailes de fábrica |
| `official_sim/keyboard_teleop.py` | teclado WASD/QE → `McLocomotionVelocity` con fuente `sdk_` registrada, y modos SD/LD/DD (sólo simulador) |
| `official_sim/official_sim_probe.py` | marcha acotada y medida (odometría + piernas) en el simulador oficial |
| `official_sim/upper_body_demo.py` + `make_arm_demo.py` | trayectoria de brazos por `/mc/upper_body_command` (para un MC con URS) |
| `assets/policies/agibot_mcrl_policy.onnx` | policy de marcha de `mc-rl` (la que AgiBot despliega por HAL con el MC parado) |
