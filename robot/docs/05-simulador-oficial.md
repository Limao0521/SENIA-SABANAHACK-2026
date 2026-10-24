# 05 · Simulador oficial de AgiBot (MC + sim_mujoco, Docker)

AgiBot reparte, junto con AimDK, un simulador en el que el **control de movimiento es el del robot real (MC)** y
MuJoCo hace de cuerpo (capa HAL). Sirve para probar programas AimDK sin el robot. Es complementario al simulador
de este repo: aquí el MC es el de verdad, pero no se pueden mover brazos ni manos (ver §5).

| Componente | Versión local | Papel | Arranque |
|---|---|---|---|
| `sim_mujoco` | x86 v1.0.0 (2026-05-22), robot `lx2501_3_t2d5` | cuerpo simulado: publica `/aima/hal/joint/*/state`, IMU y odometría, y aplica `/aima/hal/joint/*/command` | `start_sim.sh` |
| `mc` | x86 v1.0.0 (2026-05-22) | control de movimiento: policies RL, máquina de estados, arbitraje | `em_run.sh` |
| `aimdk` | v1.1.0 | mensajes, ejemplos y tu programa | `ros2 run py_examples …` |

```
tu programa / teclado ──SetMcAction, McLocomotionVelocity──▶ MC ──/aima/hal/joint/*/command──▶ sim_mujoco
                                                             ◀── /aima/hal/joint/*/state, imu, odom ──┘
```

Los binarios no van en git (unos 3,5 GB): `official_sim/fetch_assets.sh` los baja de x2-aimdk.agibot.com a
`$X2_SIM_ROOT` (por defecto `~/robots/x2_simulation`, donde ya están en este PC). El espejo limita cada conexión a
~8 KB/s, así que el script descarga por rangos en paralelo.

## 1. Cómo lo lanzamos

`official_sim/sim.sh` envuelve la guía de AgiBot ("X2 MuJoCo Motion-Control Simulation") en un contenedor ROS 2
Humble, porque este PC tiene Jazzy:

- **Aislado del robot real**: `--network none`, `ROS_LOCALHOST_ONLY=1` y `X2_SIM_ONLY=1`. Las herramientas propias
  (`keyboard_teleop.py`, `official_sim_probe.py`) se niegan a correr fuera de ese contenedor.
- Imagen `x2-ultra-sim:humble` y contenedor `x2-ultra-sim`, distintos de los de `g1_teleop` (`x2-sim`), para no
  pisarse. La imagen parte de una base ROS 2 Humble local (`g1-ros2-humble:local`); en otro PC:
  `docker build --build-arg BASE_IMAGE=ros:humble-ros-base -t x2-ultra-sim:humble official_sim/docker`.
- El `/agibot` del robot (donde el MC guarda su estado) se sustituye por `~/robots/x2_simulation/agibot`, así que
  no hace falta sudo.
- **Pórtico**: la escena de AgiBot deja la pelvis libre y el MC tarda 3-4 min en arrancar en `PASSIVE`, así que
  el robot se cae antes de que el MC pueda levantarlo. `sim.sh up` genera una copia de la configuración con un
  *weld* `gantry` que sujeta la pelvis (como el arranque colgado del robot real), sin tocar los archivos de AgiBot.
  Hay que soltarlo a mano una vez de pie: en la ventana de MuJoCo, **Equality → gantry** (desactivar).
  `start_sim.sh -s` de la guía oficial solo borra la caché; no cuelga el robot.

## 2. Paso a paso

```bash
official_sim/sim.sh image        # una vez (añade python3-tk para la ventana de teclado)
official_sim/sim.sh up
official_sim/sim.sh sdk          # una vez: colcon build de aimdk_msgs + py_examples
official_sim/sim.sh start        # ventana MuJoCo + MC (logs/x2_sim.log, logs/x2_mc.log)
official_sim/sim.sh stand        # reintenta STAND_DEFAULT hasta que el MC lo acepta (3-4 min)
#   → en la ventana de MuJoCo: Equality → gantry (desactivar)
official_sim/sim.sh teclado      # ventana con foco: W/S A/D Q/E (mantener), Mayús, Espacio, 1/2/0 modos
official_sim/sim.sh stop && official_sim/sim.sh down
```

Otros: `sim.sh check` (el MC responde), `sim.sh ex set_mc_action LD` (cualquier ejemplo de AimDK),
`sim.sh probe --forward 0.2 --duration 3` (marcha medida y acotada), `sim.sh autotest` (teclado simulado: mantiene W 3 s) y `sim.sh shell`.

### Teclado (`official_sim/keyboard_teleop.py`)

- Ventana Tk dentro del contenedor (usa el X11 del host, como la ventana de MuJoCo). Mientras una tecla está
  pulsada se publica la velocidad a 50 Hz y al soltarla se para. Los falsos soltar/pulsar de la autorrepetición de
  X11 se filtran con una confirmación de 40 ms.
- Registra la fuente `sdk_x2_ultra_keyboard` (prioridad 40) con `SetMcInputSource` y reintenta los servicios como
  los ejemplos oficiales. Al salir manda velocidad cero y deshabilita la fuente.
- Respeta los umbrales de arranque del MC: |vx| y |vy| nunca por debajo de 0,2 m/s ni |wz| por debajo de 0,1 rad/s
  cuando hay tecla pulsada. Velocidades por defecto: 0,4 adelante, 0,3 atrás, 0,5 lateral y 0,4 rad/s, ×1,8 con Mayús.
- `1` pide `STAND_DEFAULT`, `2` `LOCOMOTION_DEFAULT` y `0` `DAMPING_DEFAULT`. Se muestra el modo del MC.
- `--terminal`: modo consola por pasos (como el ejemplo `keyboard` de AimDK), sin ventana.

## 3. Lo que se ha verificado (sesión de g1_teleop, 2026-10-04)

Con el pórtico suelto, `probe --forward 0.2 --duration 3` midió **1,186 m** de desplazamiento y 0,38 rad de
movimiento máximo de pierna (1500 muestras de odometría, 3000 de piernas). Con orden cero, 0,0007 m de deriva. El MC
aceptó `LD`, pero `GetMcAction` siguió diciendo `STAND_DEFAULT`, lo que tiene explicación (§4).

**Teclado de este repo (2026-10-11)**: `official_sim/teleop_selftest.py` usa la misma clase `Teleop` que la ventana
y simula "mantener W" 3 s con el pórtico puesto. Resultado:

```
{"registered": true, "mode": "STAND_DEFAULT (100)", "keys": "w", "seconds": 3.0,
 "displacement_m": 0.0247, "max_leg_motion_rad": 0.2558}
```

La fuente `sdk_x2_ultra_keyboard` se registró, las piernas se movieron hasta 0,26 rad mientras la pelvis seguía sujeta
(el MC intenta andar) y después el MC informó `LOCOMOTION_DEFAULT`. Falta la prueba con el pórtico suelto y las
teclas físicas, que necesita interacción con las ventanas. Con la CPU libre, el MC aceptó `STAND_DEFAULT` en
segundos; los 3-4 minutos anteriores se midieron con el PC cargado.

## 4. Qué hace el MC por dentro (`mc/bin/mc_param/src/robot/lx2501_3_t2d5/`)

`action_setting.yaml` asigna cada modo a sus "runners":

| Modo | Runner | Policy (`rl_models/`) | Frecuencia |
|---|---|---|---|
| `STAND_DEFAULT` | `rl_cpgtelecon` + `transition_retarget_injector`, `only_head` | `cpgtelecon_v3_fix.onnx` (obs 85×10, 14 acciones: piernas + cintura pitch/roll) | 100 Hz |
| `LOCOMOTION_DEFAULT` | `rl_foundationwalk` | `foundationwalk_v1.onnx` (obs 111×6, 29 acciones) | 50 Hz |
| `VR_REMOTE_CONTROLLER` | `vr_remote_controller` + `rl_cpgtelecon` | `cpgtelecon_v3_fix.onnx` | 500 Hz / 100 Hz |
| `WHOLE_BODY_TELEOP` | `rl_foundationplus` | `exp59_20260320_policy.onnx` (obs 640, 29 acciones) | 50 Hz |
| `LOCOMOTION_STEP` (todoterreno) | `rl_cpgwalk` | `cpgstep_v1.onnx` | 50 Hz |

Consecuencias:

- `STAND_DEFAULT` ya camina: `cpgtelecon` acepta órdenes de velocidad (máx. vx 0,5, vy 0,5, wz 1,0 en su
  configuración). Por eso el robot avanzó aunque el modo informado siguiera siendo `STAND_DEFAULT` (AimDK: "desde
  v0.8.0 el modo de pie y la locomoción están unificados").
- `cpgtelecon` es la policy de teleoperación: no controla los brazos (`disable_arm: true`) y observa sus objetivos
  (`arm_target_pos`). Es la que permite mover los brazos con equilibrio en el robot real, la misma idea que nuestra
  policy "brazos libres". Su observación (fase CPG, órdenes de pose) no está documentada, así que este repo no la usa.
- El paquete trae además policies de baile, saltos y levantarse del suelo (`policy_*.onnx`, entradas 151 + paso de
  tiempo).

## 5. Limitaciones de este paquete (MC x86 v1.0.0)

- `UPPERBODY_REMOTE_SPLIT` no está registrado (`can not find action`) y nadie se suscribe a
  `/mc/upper_body_command`, aunque AimDK v1.1.0 los documenta. Con este MC **no se pueden mandar brazos ni manos**
  por la interfaz oficial. En `STAND_DEFAULT` el MC ignora las órdenes de brazo, como comprobó `upper_body_demo.py`.
  `official_sim/upper_body_demo.py` + `make_arm_demo.py` quedan listos para un MC que sí lo tenga.
- **Las manos no se simulan**: `/aima/hal/joint/hand/state` tiene 0 publicadores y `/command` 0 suscriptores; se
  ven en la ventana, pero no tienen ejes ni HAL.
- El modelo es el `lx2501_3_t2d5` (X2 T2.5, en `mc_param` como `x2_ultra_fixed_head.urdf`), no un Ultra-N con
  OmniHand. Sirve para la lógica de AimDK, no para validar la dinámica de nuestro robot.
- No hay cámaras. Girar en el sitio solo con velocidad angular no está garantizado (guía oficial). El MC tarda 3-4 min
  en arrancar.
- Mandar los brazos por HAL (`/aima/hal/joint/arm/command`) con el MC activo compite con su autoridad y no tiene
  timeout. Solo tendría sentido como diagnóstico, con el MC parado y el pórtico puesto.

### Comportamiento del MC descubierto con el gemelo ([08](08-gemelo-digital.md))

- Con cualquier orden de velocidad, el MC pasa solo de `STAND_DEFAULT` a `LOCOMOTION_DEFAULT` y vuelve al parar
  (`action_state.yaml`: estados "STABLE" y "MOVE" con `auto_transition`). La policy de LD (`foundationwalk`) mueve
  también los brazos.
- `VR_REMOTE_CONTROLLER` se acepta desde STAND_DEFAULT y anda con `cpgtelecon`, pero ignora
  `/aima/mc/locomotion/velocity`: su marcha viene de `VRData`, un tipo interno.
- `remapping_rule` (renombrar servicios en la configuración de AimRT) no se aplica a los servidores en este runtime
  (AimRT 0.8.24): el gemelo pone el MC en otro dominio DDS.
- En STAND_DEFAULT, el MC manda los brazos con kp = kd = 0 (pasivos).

## 6. Problemas frecuentes

| Síntoma | Causa / solución |
|---|---|
| `sim.sh stand` agota los reintentos | el MC aún arranca o se cayó: mirar `logs/x2_mc.log`; `stop` + `start` |
| El robot cae al soltar el pórtico | no estaba en `STAND_DEFAULT` RUNNING: `sim.sh check` |
| El teclado no mueve al robot | la ventana Tk no tiene el foco; la fuente no se registró (MC sin arrancar); velocidad lateral bajo el umbral |
| No aparece la ventana de MuJoCo | `xhost +si:localuser:root` (lo hace `up`) y `DISPLAY` correcto |
| `colcon build` falla en `sdk` | el volumen `aimdk` debe ser escribible; repetir `sim.sh sdk` |
