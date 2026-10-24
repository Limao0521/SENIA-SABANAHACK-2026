# 08 · Gemelo digital: el MC oficial de AgiBot sobre nuestro Ultra-N

## 1. Qué es

Un robot simulado que, visto desde un programa, funciona como el X2 Ultra-N real. Tiene dos piezas:

- **El MC oficial de AgiBot** (`mc` x86 v1.0.0, el binario tal cual): máquina de estados, `STAND_DEFAULT`,
  marcha, fuentes de entrada y servicios de AimDK.
- **Nuestro MuJoCo** (Ultra-N + OmniHand), que hace de HAL del robot. Publica y escucha los mismos topics y
  servicios que el robot real, con sus mensajes, QoS, frecuencias y `frame_id`: articulaciones, IMU, manos con
  tacto, cámaras, LiDAR y audio/voz.

```
programas del usuario: py_examples de AimDK, tu código, app Qt (twin.sh gui)      dominio DDS 0 (público)
        │   topics y servicios públicos de AimDK v1.1.0
        ▼
gemelo (python3 -m x2_ultra.twin.run)
        ├─ HAL: articulaciones 500/333 Hz, IMU 500 Hz, manos 200 Hz, cámaras, LiDAR, audio
        ├─ UPPERBODY_REMOTE_SPLIT y HEAD_ONLY emulados (el MC v1.0.0 no los trae)
        └─ puente de los topics y servicios públicos del MC
        ▼   la misma HAL, publicada también aquí
MC oficial (em_run.sh)                                                            dominio DDS 77 (privado)
```

Es el camino "MC oficial + nuestro MuJoCo como HAL". El robot real (v1.1.4) sí trae URS ([09](09-robot-real.md)),
pero un MC x86 con URS para simular no se puede conseguir hoy (sección 8), así que el camino actual es **MC oficial v1.0.0 + HAL propia + URS
emulado**. `--sin-urs` reenvía al MC las solicitudes URS/HEAD_ONLY y `/mc/upper_body_command`; para usarlo
hará falta un MC compatible y validarlo en marcha. El MC x86 v1.0.0 actual rechaza esas acciones.

## 2. Uso

Todo corre en el contenedor `x2-twin` (ROS 2 Humble, GPU, pantalla y audio del PC, sin red):

```bash
official_sim/twin.sh image     # una vez: imagen x2-twin:humble (encima de x2-ultra-sim:humble)
official_sim/twin.sh up        # contenedor
official_sim/twin.sh start     # gemelo + MC (el MC tarda 1-4 min); ventana MuJoCo de solo visualización
official_sim/twin.sh start --mc-profile P1  # experimento aislado; P0 es la configuración original
official_sim/twin.sh profile-info            # verifica YAML fuente, SIM generado y carga real del MC
official_sim/twin.sh stand     # STAND_DEFAULT con el pórtico puesto
official_sim/twin.sh suelta    # quitar el pórtico (portico = ponerlo; reset = reiniciar)
official_sim/twin.sh gui       # la app de control: WASD/QE, IK, poses y manos por la interfaz de AimDK
official_sim/twin.sh teclado   # o la ventana de teclado del simulador oficial (solo marcha)
official_sim/twin.sh ex set_mc_action URS          # cualquier ejemplo oficial: ros2 run py_examples ...
official_sim/twin.sh ex upper_body_control joint
official_sim/twin.sh urs-test --pose al_frente --mano agarre --segundos 5   # prueba medida del URS
python3 official_sim/urs_matrix.py           # matriz P0/P1/P2: poses, cambios y tres repeticiones
official_sim/twin.sh test      # pruebas que necesitan ROS (tests_twin/)
official_sim/twin.sh stop | down
```

Opciones de `start` (las de `x2_ultra.twin.run`):
- `--mc-profile P0|P1|P2`: P0 es el MC original; P1 desactiva la transición automática de `STAND_DEFAULT`;
  P2 prueba `rl_cpgwalk` en `LOCOMOTION_DEFAULT`. Cada perfil corre desde un árbol privado bajo
  `runs/mc_profiles/current`; `sim/` se genera allí. El MC del proveedor se monta solo para lectura;
- `--escena vacia`;
- `--camaras real|media|reducida|no`: por defecto, reducida;
- `--sin-lidar`, `--sin-audio`, `--sin-visor`;
- `--sin-urs`: reenvía URS/HEAD_ONLY y órdenes de tren superior al MC, conservando la respuesta de este.
  El MC v1.0.0 disponible hoy responde "can not find action";
- `--sin-portico`.

Tu programa corre dentro del contenedor (`twin.sh shell`), en el dominio por defecto, igual que en el PC2 del
robot. `twin.sh gui` hace lo mismo que harías con el robot: STAND_DEFAULT con el pórtico, lo quita, pide URS y
manda a 50 Hz la velocidad (fuente `sdk_x2_ultra_gui`) y el tren superior. En la pestaña Marcha, las órdenes por
debajo de 0,2 m/s o 0,1 rad/s se suben a ese mínimo (umbral de arranque del MC).

## 3. Interfaz implementada

| Topic / servicio | Tipo | QoS | Frecuencia | Quién |
|---|---|---|---|---|
| `/aima/hal/joint/{leg,waist,arm}/state` | `JointStateArray` | BEST_EFFORT + TRANSIENT_LOCAL | 500 Hz | HAL |
| `/aima/hal/joint/head/state` | `JointStateArray` | igual | 333 Hz | HAL |
| `/aima/hal/joint/*/command` | `JointCommandArray` | BEST_EFFORT | lo que llegue | HAL (PD a 1 kHz, sin timeout) |
| `/aima/hal/joint/hand/state` · `/command` | `HandStateArray` (con tacto) · `HandCommandArray` | BEST_EFFORT | 200 Hz | HAL |
| `/aima/hal/imu/{torso,chest}/state` | `sensor_msgs/Imu`, `frame_id` base_link | BEST_EFFORT + TRANSIENT_LOCAL | 500 Hz | HAL |
| `/aima/hal/sensor/rgbd_head_front/{rgb_image[/compressed], depth_image, rgb_camera_info, depth_camera_info, imu}` | `Image` rgb8 / 16UC1 mm, `CompressedImage` JPEG, `CameraInfo`, `Imu` | RELIABLE + VOLATILE | 30 Hz (IMU 200) | sensores |
| `/aima/hal/sensor/stereo_head_front_{left,right}/{rgb_image[/compressed], camera_info}` | `Image` rgb8, `CameraInfo` | RELIABLE + TRANSIENT_LOCAL | 10 Hz (info 1 Hz) | sensores |
| `/aima/hal/sensor/rgb_head_rear/{rgb_image[/compressed], camera_info}` | igual | igual | 10 Hz | sensores |
| `/aima/hal/sensor/lidar_chest_front/{lidar_pointcloud, imu}` | `PointCloud2` PointXYZIRT, `Imu` | RELIABLE + TRANSIENT_LOCAL | 10 Hz (IMU 200) | sensores |
| `/aima/hal/audio/{playback, play_state, focus_response, capture}` · `/agent/process_audio_output` | `AudioPlayback`, `PlayStateChange`, `FocusResponse`, `AudioCapture` (16 kHz, 6 canales), `ProcessedAudioOutput` | como `voice.html` | 33,3 Hz la captura | audio |
| `PlayTts`, `PlayAudioFile`, `StopAudioPlay`, `Get/SetVolume`, `Get/SetMute`, `Request/AbandonAudioFocus`, `Get/SetMicSourceRequest` | `aimdk_msgs/srv` | | | audio |
| `GetAllJointState`, `GetHandType` | `aimdk_msgs/srv` | | | HAL |
| `SetMcAction`, `GetMcAction` | `aimdk_msgs/srv` | | | URS y HEAD_ONLY emulados por defecto; con `--sin-urs`, al MC |
| `/mc/upper_body_command` | `UpperBodyCommandArray` | BEST_EFFORT | ~50 Hz | emulador por defecto; con `--sin-urs`, al MC |
| `/aima/mc/locomotion/velocity` · `/aima/mc/common/state` · `/aima/mc/leg_odometry` · `/tf` · `/tf_static` | | como el MC | | MC, por el puente (`common/state` con el modo emulado) |
| `SetMcInputSource`, `GetCurrentInputSource`, `SetMcPresetMotion`, `GetMcPresetMotionState`, `GetMcLocomotionSpeedStatus` | | | | MC, por el puente |
| `/aima/hal/odom/state` · `/aima/sim/option/command` | `Odometry` · `String` ("gantry on/off", "reset", "push fx fy") | | 500 Hz | solo simulación (como sim_mujoco) |

Los valores salen de la documentación de AimDK v1.1.0 (`aimdk/docs`, `aimdk/topics_and_services`). Donde no dice
nada, de lo que hace el simulador oficial con el MC, grabado con `official_sim/capture_reference.sh`. Ejemplo:
la odometría, con `frame_id` odom, `child_frame_id` base_link y la pose de la IMU de pelvis.

## 4. URS y HEAD_ONLY emulados

AimDK v1.1.0 documenta `UPPERBODY_REMOTE_SPLIT`, pero el binario del MC x86 v1.0.0 no tiene ese código: no hay
ninguna cadena `upper_body` y su `aimdk_msgs` ni siquiera trae `UpperBodyCommandArray`. Tampoco trae `HEAD_ONLY`. El
gemelo los emula con el contrato público de `upper_body_control.html`:

- `SetMcAction(URS | HO)` se acepta desde `STAND_DEFAULT`. `GetMcAction` y `/aima/mc/common/state` muestran el
  modo emulado; cualquier otro modo se le pide al MC y termina la emulación.
- `/mc/upper_body_command`: se descartan las órdenes con `header.stamp` a más de 200 ms (con stamp 0 se usa la hora
  de llegada). En URS mandan `arm_pos` (14) y `hand_pos`, con `hand_sub_mode` 1 pinza, 2 articular (20) o 3 gesto,
  y la cabeza se ignora. En HEAD_ONLY manda solo `head_pos`.
- Se aplica sustituyendo la orden HAL de brazos del MC por un PD hacia el objetivo:
  - rampa de 2 rad/s y compensación de gravedad;
  - ganancias kp 40 / kd 2, las de `classic/manipulate.yaml` del propio MC;
  - en STAND_DEFAULT, el MC deja los brazos pasivos (kp = 0), así que no hay conflicto;
  - las manos son servos de posición.

**Por qué dos dominios DDS.** Para emular URS hay que atender `SetMcAction`/`GetMcAction` con sus nombres
públicos, pero el MC también los sirve. El runtime del MC (AimRT 0.8.24, fork de AgiBot) lee la opción
`remapping_rule`, pero no la aplica a los servidores: llegó en AimRT 0.9. Además, dos servidores con el mismo nombre
competirían por responder. Por eso el MC va en el dominio 77: el gemelo publica la HAL en los dos dominios y
puentea los topics y servicios del MC que están en el `aimdk_msgs` público. Los tipos internos (`McBodyPose`,
`VRData`…) no se pueden usar desde el SDK, así que no se puentean.

**Límite de la marcha en URS.** Con velocidad, el MC v1.0.0 pasa solo de STAND_DEFAULT a LOCOMOTION_DEFAULT
(`action_state.yaml`: estados "STABLE" y "MOVE" con transición automática). La policy de LD (`foundationwalk`) mueve
también los brazos, y aquí se sustituye su orden de brazos. En las mediciones exploratorias anteriores a la
matriz de la sección 7 se observó:

| Brazos (URS) | Marcha | Resultado |
|---|---|---|
| reposo | W 5 s | 2,9 m, inclinación ≤ 9,4° |
| al frente | W 5 s | **0,4-0,6 m**, estable (la policy apenas avanza) |
| saludar | W 4 s | 0,2 m, estable |
| arriba | W 4 s | **se cae** |

De pie (STAND_DEFAULT, policy `cpgtelecon`, que observa los brazos) aguanta cualquier pose. Un MC con URS real
probablemente andaría con `cpgtelecon`, como hace en su modo VR, que no salta a LD solo. Pero en el v1.0.0 la
marcha de ese modo viene de `VRData`, un tipo interno, y no se ha reconstruido. Nuestra policy "brazos libres"
(simulador local, [06](06-locomocion-rl.md)) sí anda con los brazos arriba.

## 5. Cámaras y LiDAR

Las cámaras van montadas en las poses que publica el MC en `/tf_static`, así que imágenes y árbol TF coinciden.

| Cámara | Real (Ultra-N) | Gemelo por defecto ("reducida") | Notas |
|---|---|---|---|
| RGB-D | 1280×720 rgb8 + profundidad 16UC1 en mm, 30 Hz | 640×360 | estenopeica de 55° (color) / 58,7° (profundidad); profundidad 0,26-6 m; color y profundidad alineadas |
| Estéreo izq./der. | 2048×1536, 10 Hz, 156°×120° | 512×384 | estenopeica de 120° en vertical (MuJoCo no simula ojo de pez); línea base 65 mm (estimada del URDF) |
| Trasera | 2048×1536, 10 Hz, girada 90° | 512×384 | girada 90° como en el Ultra-N; se ve el asa de detrás del cuello |

- `--camaras real` da la resolución del robot, a costa de frecuencia.
- Las cámaras solo se dibujan si alguien está suscrito; `camera_info` se publica siempre.
- La RGB-D sale girada 180° porque su marco óptico en el URDF y en el TF del MC lo está (cámara montada al revés).
  El gemelo respeta el TF. **Comprobar con el robot** si el driver real endereza la imagen.
- **LiDAR** (RoboSense E1R): 120°×90° a 0,625° (192×144 haces), 10 Hz, zona ciega de 0,1 m, en el marco propio del
  sensor (x adelante, y izquierda, z arriba), montado girado como en el TF del MC.
  - Ve las superficies visibles, incluidos los propios brazos.
  - Puntos PointXYZIRT (x, y, z, intensity, ring, timestamp; 32 bytes), la disposición de `rslidar_sdk`; la
    intensidad sale del color.

## 6. Audio y voz

- `PlayTts` usa espeak-ng, por defecto en español (`X2_TWIN_TTS_VOICE` para cambiarlo). Sigue las prioridades del
  robot: una de mayor prioridad interrumpe; si ya suena una mayor, responde `ERROR_HIGHER_PRIORITY_BUSY`.
- `PlayAudioFile` acepta WAV/PCM S16LE y gestiona el foco solo. `/aima/hal/audio/playback` reproduce en streaming
  (cambiar `pkg_name` o `token_id` vacía el búfer).
- Volumen, silencio, foco y micrófono se comportan como en `voice.html`.
- `/aima/hal/audio/capture` usa el micrófono del PC: 16 kHz, 6 canales entrelazados, los 4 de micrófono con la
  misma señal y los 2 de referencia a cero.
- `/agent/process_audio_output` hace VAD por energía, sin palabra de activación (el robot real sí la exige).
- **El micrófono solo se abre mientras hay suscriptores.**

## 7. Verificado (2026-10-12)

| Prueba | Resultado |
|---|---|
| Frecuencias con el MC conectado | articulaciones 500,0 Hz, cabeza 333,3 Hz, IMU 500 Hz; física a ×1,00 de tiempo real (paso de 1 ms) |
| MC oficial sobre el Ultra-N | `STAND_DEFAULT` con pórtico y sin pórtico 15 s: pelvis a 0,694 m, inclinación 3,5° estable |
| Marcha oficial (teclado, W 5 s) | 2,1-2,3 m, inclinación ≤ 1,9°; atrás y lateral más lentas (0,22 y 0,10 m en 4 s); en el sitio, con solo velocidad angular, apenas gira (AgiBot avisa de ello en su guía) |
| URS emulado (`twin.sh urs-test`) | aceptado y visible en `GetMcAction`; en mediciones exploratorias de pie: brazos a 0,013-0,027 rad y manos a 0,003-0,005 rad; la matriz de marcha de abajo no aprobó |
| Ejemplos oficiales sin cambios | `set_mc_action` SD/URS, `get_mc_action`, `upper_body_control joint` (la mano queda en sus valores), `echo_camera_rgbd`, `echo_camera_stereo`, `echo_lidar_data`, `play_tts`, `play_audio` |
| App Qt sobre el gemelo (`tests_twin/gui_smoke.py`) | llega a URS y W mueve el robot; el smoke actual falla por 0,199 rad de error máximo de brazos |
| Pruebas automáticas | `tests/test_twin.py` (9, venv) y `tests_twin/` (CDR frente a rclpy, contenedor) |

La matriz nueva (`official_sim/urs_matrix.py`) ejecuta por perfil tres corridas de `reposo`, `al_frente`,
`saludar`, `arriba` y `cargar`, más los cambios `reposo→al_frente`, `al_frente→arriba` y `arriba→cargar`.
Cada corrida reinicia el gemelo con pórtico, pide `STAND_DEFAULT` y lo suelta antes de aplicar velocidad
frontal de 0,4 m/s durante 5 s. La referencia `reposo` usa el mismo agarre de OmniHand que las demás
poses. `saludar` se evalúa como pose fija; el gesto animado queda fuera de esta matriz.

El informe JSON registra por separado avance **durante** la orden y deriva **después** de parar, estado
real del MC en DDS 77, altura, inclinación, caídas, seguimiento de brazos/manos, factor de tiempo real y
frecuencias. Para aprobar exige avance frontal ≥1,5 m y ≥70 % de la referencia de reposo del mismo perfil,
altura de pelvis >0,45 m, inclinación <30°, cero caídas y error máximo de brazos ≤0,10 rad. El RTF y
las frecuencias son diagnóstico, sin umbral de aceptación fijado. El JSON se actualiza tras cada corrida
en `logs/`, y el perfil solo se elige si el MC cargó su configuración privada y todas las corridas aprueban.

`tests_twin/full_stack_smoke.py` comprueba imágenes y `CameraInfo` de las cinco cámaras, contenido y
campos del LiDAR, TF estática, servicios de audio, TTS y captura de micrófono cuando se solicita.
`tests_twin/gui_smoke.py` comprueba la ruta de la GUI y guarda una captura.

Resultado de la matriz del 12 de octubre, con [métricas por corrida](metrics/urs_matrix_20261012.json):

| Perfil | Corridas | Reposo, avance en 5 s | `al_frente`, avance en 5 s | `arriba` | Resultado |
|---|---:|---:|---:|---|---|
| P0, MC original | 24/24 | 2,33–2,44 m | −0,61–0,03 m | caída 3/3 | No aprueba |
| P1, STAND sin transición automática | 24/24 | 2,36–2,45 m | −0,63–0,27 m | caída 3/3 | No aprueba; al andar aún entra en `LOCOMOTION_DEFAULT` |
| P2, `rl_cpgwalk` | 2 medidas y un reinicio fallido | 1,61 m | −1,52 m | sin prueba válida | No aprueba; después de la segunda medida no volvió de `LOCOMOTION_DEFAULT` a `STAND_DEFAULT` |

Los tres perfiles cargaron la configuración privada prevista, comprobada en el YAML fuente, en el YAML
`sim/` generado y en el log de arranque del MC. La matriz detuvo P2 al fallar su preparación, y restauró P0.
En P0/P1 el factor de tiempo real rondó 1,00 y las órdenes se publicaron a 50 Hz; la falta de avance con
poses de brazos no se explica por una ralentización del simulador. Ningún perfil alcanza aún la aceptación
de locomoción oficial con brazos independientes para todas las poses.

La prueba de integración de 20 s con P0 andando en reposo aprobó imágenes/`CameraInfo`, LiDAR con puntos
válidos, TF, audio y micrófono. Se observaron 28,6 Hz en RGB-D, 9,7 Hz en estéreo/trasera/LiDAR y 33,2 Hz
en micrófono; otra corrida confirmó `PlayTts`. La marcha simultánea avanzó 2,44 m con factor de tiempo
real ×1,00, aunque falló el límite de brazos por 0,105 rad. La GUI entró en URS, movió el robot y guardó
una captura, pero su smoke falló por un pico de seguimiento de 0,199 rad. Estos resultados están en
[las métricas de integración](metrics/integration_20261012.json).

La ruta experimental `VR_REMOTE_CONTROLLER` recibió `VRData` en el DDS privado a ~50 Hz y entró en el
modo del MC. Tras 4 s de inicialización neutra, dos pruebas de 2 s con eje Y izquierdo +0,2 y −0,2
desplazaron solo 0,022 m cada una. No constituyen marcha; aún no está confirmada la semántica interna
de los botones/ejes de `VRData`. El MC volvió a `STAND_DEFAULT` después de cada prueba.

Con `--sin-urs`, el ejemplo oficial `set_mc_action URS` llegó al MC v1.0.0 y este respondió
`can not find action: UPPERBODY_REMOTE_SPLIT` (código −1). `GetMcAction` siguió en `STAND_DEFAULT`.
La prueba ROS con un MC falso compatible verifica el reenvío de URS/HEAD_ONLY y de
`/mc/upper_body_command`, pero falta repetirlo con un MC auténtico que implemente URS.

## 8. Diferencias con el robot real y pendientes

- **URS y HEAD_ONLY son emulados.** El MC x86 v1.0.0 es el único publicado (`sdk-downloads.json`: SDK v0.8.1 a
  v1.1.0 y `mc-x86-v1.0.0-20260522`). Un MC con URS habría que pedírselo a AgiBot o sacarlo del firmware de un robot.
- El MC está empaquetado para el `lx2501_3_t2d5` (T2.5). Mantiene en pie y hace andar al Ultra-N, pero no está
  afinado para él.
- Cámaras estenopeicas y sin distorsión. Profundidad y color alineados. La RGB-D, girada como su TF (sección 5).
- No se simulan: batería/PMU, LEDs, táctil de cabeza, GNSS, pantalla, navegación/SLAM, diagnóstico (HDS) ni estado
  del sistema.
- Los servicios internos del MC (los que no están en el `aimdk_msgs` del SDK) no se puentean.
- `effort` de los estados articulares = par real del actuador (el simulador oficial publica 0).
- El contenedor no tiene red: los programas corren dentro, como en el PC2. Para conectar programas del host
  haría falta arrancarlo con red de host y el mismo `ROS_DOMAIN_ID`.

## 9. Cómo se consiguió tiempo real en Python

Con objetos de rclpy, la HAL no pasaba de 0,4× tiempo real: ~3 900 mensajes/s publicados y ~1 600 órdenes/s del
MC. Lo que lo resolvió:

- **Plantillas CDR** (`twin/cdr.py`): cada mensaje de alta frecuencia es un bloque de bytes con los nombres ya
  escritos, donde solo se copian los números. Las órdenes se leen en crudo (`raw=True`). Los tests comprueban que
  rclpy los lee igual.
- **Visor, cámaras y LiDAR en otros procesos**, leyendo la postura de memoria compartida: con el visor en el mismo
  proceso, la física caía a 0,77×.
- **`array('B')` en los campos `uint8[]`**: con `bytes`, rclpy valida byte a byte (42 ms por imagen de 640×360).
- numpy 1.26 en el contenedor: las extensiones de mensajes de ROS Humble no admiten numpy 2.
