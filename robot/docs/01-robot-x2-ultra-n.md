# 01 · El robot: AgiBot X2 Ultra-N

Fuentes: documentación de AimDK v1.1.0 (`~/robots/x2_simulation/aimdk/docs`, en línea en
<https://x2-aimdk.agibot.com>), repo público [AgibotTech/agibot_x2_urdf](https://github.com/AgibotTech/agibot_x2_urdf)
(commit `575cc6b`) y `mc-rl` de AgiBot. Donde las fuentes no coinciden se indica.

## 1. Qué modelo es

AgiBot (智元, "Lingxi X2") vende tres variantes del X2. **La nuestra es la X2 Ultra-N**, la "X2 Ultra (new version)"
de la documentación: cuello de **2 ejes** y manos **OmniHand**.

| | X2 Ultra | **X2 Ultra-N** (new version) | X2 EDU |
|---|---|---|---|
| Placa de la nuca (línea superior termina en) | "X2 Ultra" | **"X2 Ultra -N"** | "X2 EDU" |
| Modelo URDF | `X2_URDF-v1.3.0/x2_ultra*` | **`X2_URDF-v1.4.0/X2-Ultra*`** | `X2_URDF-v1.4.0/X2-EDU*` |
| Grados de libertad actuados | 30 | **31** | 29 |
| Cuello | 1 (solo `head_yaw` activo) | **2 (`head_yaw` + `head_pitch`)** | 0 |
| Brazo / cintura / pierna | 7 / 3 / 6 | 7 / 3 / 6 | 7 / 3 / 6 |
| Manos disponibles | OmniHand, OmniPicker | **OmniHand** (URDF `X2-Ultra_omnihand.urdf`), OmniPicker | solo pinza |

> Nota: el URDF v1.3.0 también trae un `head_pitch_joint`, pero en el X2 Ultra original ese eje no está activo
> (AimDK, "Joint Control": *"X2 Ultra: only yaw is active; X2 Ultra(new version): both yaw and pitch are active"*).
> Para distinguir los modelos manda la placa de la nuca, no el URDF.

### Cambios del Ultra al Ultra-N (README del URDF v1.4.0)

| Cambio | Articulaciones |
|---|---|
| Par pico 36 → **60 N·m** | `*_ankle_pitch`, `*_shoulder_pitch`, `*_shoulder_roll` |
| Par pico 24 → **36 N·m** | `*_ankle_roll`, `*_shoulder_yaw`, `*_elbow`, `*_wrist_yaw` |
| Cintura 48 → **36 N·m** | `waist_pitch`, `waist_roll` |
| Motor de muñeca nuevo, **6 N·m** | `*_wrist_pitch`, `*_wrist_roll` |
| Estructura reforzada, masa ~42 → ~45 kg | — |

## 2. Especificaciones generales (AimDK §1.2)

| Parámetro | X2 Ultra-N |
|---|---|
| Dimensiones | 1310 (alto) × 460 (ancho) × 210 (fondo) mm |
| Masa | ≈39 kg según la tabla de AimDK; ≈45 kg según el README del URDF v1.4.0; **45,18 kg** sumando el URDF con OmniHand (el que simulamos) |
| Envergadura de brazos (sin efector) | 558 mm |
| Par pico de articulación | 120 N·m (cadera, rodilla, cintura yaw) |
| Velocidad | máx. 1,8 m/s; uso diario ≤ 0,8 m/s |
| Carga | 3 kg en postura concreta, ≤ 1 kg en todo el espacio de trabajo (sin efector) |
| Batería | ≈421 Wh, ~2 h caminando a 0,5 m/s, carga ≤ 1,5 h (cargador 54,6 V 10 A), intercambiable |
| Temperatura de trabajo | −10 a 40 °C |
| Comunicación | Wi-Fi, Bluetooth, 4G/5G, **UWB (solo Ultra-N)** |
| Interacción | matriz de micrófonos, micrófono inalámbrico, altavoces, pantalla, luces, sensor táctil en la cabeza |

## 3. Ordenadores y red

| Unidad | Hardware | Para qué | Dirección |
|---|---|---|---|
| **PC1** · control de movimiento | RK3588s | MC (control de movimiento) y HAL. **Prohibido** compilar o ejecutar desarrollo propio aquí | 10.0.1.40 |
| **PC2** · desarrollo | Jetson **Orin NX 16 GB** (157 TOPS, GPU Ampere 1024 núcleos, CPU 8×A78AE, 512 GB) | Programas propios con AimDK (en contenedor; obligatorio desde AimDK v1.2.0) | 10.0.1.41 |
| **PC3** · interacción | RK3588 | Voz, pantalla, interacción | — |

- **Cable** (recomendado): puerto Ethernet de desarrollo de la espalda; el PC propio con IP fija `10.0.1.2/24`;
  PC2 en `10.0.1.41` (credenciales SSH: soporte técnico de AgiBot).
- **Wi-Fi**: punto de acceso del robot (se activa en la app Agibot Go); PC2 accesible por el salto `192.168.88.88`.
  Solo para depurar.
- Puertos USB del Ultra-N: 3 × Type-C (2 en el Orin NX, 1 en el RK3588). Un periférico para un programa que corre en
  PC2 debe ir a un USB del lado Orin NX.
- Alimentación auxiliar: 12 V/3 A y 48 V/5 A (XT30UPB-F); 2 × RJ45 gigabit.
- No poner el robot en una red pública o compartida.

## 4. Sensores (AimDK §1.6)

| Sensor | Modelo | Datos clave |
|---|---|---|
| LiDAR de pecho | RoboSense E1R | 120° × 90°, 940 nm, nube a 10 Hz |
| Cámara RGB-D de cabeza | Orbbec Gemini 335 | profundidad hasta 1280×800, color hasta 1920×1080, 60 fps |
| Estéreo frontal | SenYun SDS23NNS1 | 2064×1552, 40 fps, 156° × 120° |
| Cámara de interacción frontal | SenYun SM5M12NJ | 2608×1960, 30 fps, 110° × 80° |
| Cámara trasera | SenYun SM3S23NS | 2064×1552, 30 fps, 156° × 120° (en algunas unidades va girada 90°) |
| IMU | FORSENSE FSS-IMU16460-DM (×2: pecho y cadera) | 6 ejes, hasta 1 kHz; `/aima/hal/imu/{chest,torso}/state` a 500 Hz |
| Táctil de cabeza | Awinic AW93208GQNR | toque, doble, triple, pulsación larga, deslizar |
| GNSS | GPS/BeiDou/GLONASS/Galileo | 1 Hz, ~2,5 m en exterior |

> El topic `/aima/hal/imu/torso/state` se llama "torso" pero la IMU está en la cadera/pelvis
> (`mc-rl/README`). Es la que usan las policies de marcha.

Calibración de fábrica (extrínsecos e intrínsecos de cada sensor, propia de cada robot) en PC2:
`/agibot/data/param/protected/factory_calib/factory_calibration_params.yml`. No modificarla.

## 5. Articulaciones

Convenio de ejes del cuerpo: **X adelante, Y izquierda, Z arriba**; giros positivos por la regla de la mano
derecha (roll = X, pitch = Y, yaw = Z). El orden de cada grupo es el de los arrays de AimDK
(`JointStateArray`/`JointCommandArray` de `/aima/hal/joint/{leg,waist,arm,head}/*`); el código lo tiene en
`src/x2_ultra/robot/joints.py`.

| Grupo · índice | Articulación | Rango [rad] | Rango [°] | Par máx. [N·m] | Kp / Kd sim |
|---|---|---|---|---|---|
| Pierna 0 | `left_hip_pitch_joint` | -2.705 … +2.557 | -155.0 … +146.5 | 120 | 120 / 5 |
| Pierna 1 | `left_hip_roll_joint` | -0.236 … +2.906 | -13.5 … +166.5 | 120 | 100 / 4 |
| Pierna 2 | `left_hip_yaw_joint` | -1.859 … +3.604 | -106.5 … +206.5 | 120 | 100 / 4 |
| Pierna 3 | `left_knee_joint` | +0.000 … +2.409 | +0.0 … +138.0 | 120 | 150 / 5 |
| Pierna 4 | `left_ankle_pitch_joint` | -0.803 … +0.454 | -46.0 … +26.0 | 60 | 40 / 2 |
| Pierna 5 | `left_ankle_roll_joint` | -0.297 … +0.297 | -17.0 … +17.0 | 36 | 40 / 2 |
| Pierna 6-11 | `right_*` (mismo orden) | roll y yaw de cadera espejados | | | |
| Cintura 0 | `waist_yaw_joint` | -3.430 … +2.121 | -196.5 … +121.5 | 120 | 40.18 / 2.56 |
| Cintura 1 | `waist_pitch_joint` | -0.314 … +0.314 | -18.0 … +18.0 | 36 | 200 / 2 |
| Cintura 2 | `waist_roll_joint` | -0.489 … +0.489 | -28.0 … +28.0 | 36 | 200 / 2 |
| Brazo 0 | `left_shoulder_pitch_joint` | -3.080 … +2.033 | -176.5 … +116.5 | 60 | 50 / 3 |
| Brazo 1 | `left_shoulder_roll_joint` | -0.061 … +3.046 | -3.5 … +174.5 | 60 | 50 / 3 |
| Brazo 2 | `left_shoulder_yaw_joint` | -2.557 … +2.557 | -146.5 … +146.5 | 36 | 50 / 3 |
| Brazo 3 | `left_elbow_joint` | -2.356 … +0.000 | -135.0 … +0.0 | 36 | 50 / 3 |
| Brazo 4 | `left_wrist_yaw_joint` | -2.557 … +2.557 | -146.5 … +146.5 | 36 | 20 / 2 |
| Brazo 5 | `left_wrist_pitch_joint` | -0.524 … +0.524 | -30.0 … +30.0 | 6 | 20 / 2 |
| Brazo 6 | `left_wrist_roll_joint` | -1.510 … +0.724 | -86.5 … +41.5 | 6 | 20 / 2 |
| Brazo 7-13 | `right_*` (mismo orden) | roll del hombro y de la muñeca espejados | | | |
| Cabeza 0 | `head_yaw_joint` | -0.489 … +0.489 | -28.0 … +28.0 | 3.13 | 20 / 1 |
| Cabeza 1 | `head_pitch_joint` | -0.401 … +0.489 | -23.0 … +28.0 | 2 | 20 / 1 |

- Los rangos son los del URDF v1.4.0 y coinciden con la tabla "garantizada" de AimDK §1.7, salvo el **roll de
  tobillo**: el URDF dice ±17°, mientras que AimDK da ±15° para el Ultra-N.
- Kp/Kd son las ganancias PD de marcha de AgiBot (`mc-rl/x2_config/joint_gains.yaml`, "simulation values, not
  hardware-measured"). Las usan el entrenamiento y el simulador. La cabeza va con 20/1, como en el arranque del MC.
- Mirando de frente, los brazos se abren con `shoulder_roll` positivo en el izquierdo y negativo en el derecho; el
  codo se flexiona con valores negativos y `shoulder_pitch` negativo adelanta o levanta el brazo.

### Postura de referencia ("por defecto")

La de las policies de marcha de AgiBot y la nuestra: cadera `pitch` −0,312, rodilla 0,669, tobillo `pitch` −0,363
(semicuclillas con los pies planos), hombro `roll` ±0,2 y codo −0,3. El resto, 0. En esa postura la pelvis queda a
~0,64 m del suelo; caminando, a 0,62-0,67 m.

### Distribución de masas del modelo (URDF con OmniHand)

| Parte | Masa |
|---|---|
| Robot completo | 45,18 kg |
| Torso y todo lo que cuelga de él (cintura arriba: brazos, cabeza) | 21,3 kg |
| Una pierna (desde `hip_pitch`) | 9,06 kg |
| Un brazo (desde el hombro, con mano) | 4,29 kg |
| Una OmniHand (palma + dedos) | 0,447 kg (+ 0,081 kg de brida de muñeca) |

## 6. Modos de movimiento (resumen; detalle en [03-sdk-aimdk.md](03-sdk-aimdk.md))

```
DAMPING_DEFAULT ──> PASSIVE_DEFAULT ──> JOINT_DEFAULT ──> STAND_DEFAULT <──> LOCOMOTION_DEFAULT
 (amortiguado)        (par cero)        (de pie en       (de pie con          (andar/correr;
                                         posición)        equilibrio activo)   unificado con STAND
                                                                               desde v0.8.0)
```

Antes de pasar a `STAND_DEFAULT` el robot debe estar de pie con los dos pies en el suelo (arranque colgado del
pórtico). `UPPERBODY_REMOTE_SPLIT` cede cabeza, brazos y manos al usuario mientras el MC mantiene el equilibrio.

## 7. Archivos del modelo

| Archivo | Uso en este repo |
|---|---|
| `X2_URDF-v1.4.0/X2-Ultra.xml` | MJCF oficial del cuerpo: base del modelo (colisiones simplificadas, 12 esferas de contacto por pie, IMU `imu_0` en la pelvis) |
| `X2_URDF-v1.4.0/X2-Ultra_omnihand.urdf` | Geometría, inercias y ejes de las OmniHand, que se injertan en el MJCF (ver [04](04-simulacion-mujoco.md)) |
| `X2_URDF-v1.4.0/X2-Ultra_omnipicker.urdf` | Variante con pinza (no usada) |
| `X2_URDF-v1.3.0/x2_ultra.xml` | Ultra original; es el que usa `mc-rl` para validar su policy |
| `models/x2_ultra_n_omnihand_{mesa,vacia}.xml` | MJCF generado por `x2-build-model` (abrir con `python -m mujoco.viewer --mjcf models/...`) |

Licencia de los URDF: Mulan PSL v2. No se copian al repo: `scripts/setup.sh` los enlaza o clona en
`third_party/agibot_x2_urdf`.
