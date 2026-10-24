# 02 · Manos OmniHand

Las manos del robot son **AgiBot OmniHand Dynamic Edition 2025**, la variante con dedos articulados del X2 Ultra y
el Ultra-N; la alternativa es la pinza OmniPicker. Fuentes: URDF `X2-Ultra_omnihand.urdf` (v1.4.0), AimDK v1.1.0
§5.1.5 ("Upper Body Control") y §5.1.8 ("End Effector Control"), y los ejemplos `py_examples` de AimDK.

## 1. Cinemática

Cada mano tiene **10 ejes activos** (motores), **6 acoplados** (`<mimic>` en el URDF: se mueven solidarios con un
eje activo) y un eje fijo (la separación del corazón). La palma cuelga de `*_wrist_roll_link` con una brida de
0,081 kg. La mano pesa 0,447 kg: palma 0,159 kg y pulgar 0,100 kg; cada dedo largo, 0,047 kg.

### Ejes activos, en el orden del SDK

El índice es el orden de `HandCommand[]`/`HandState[]` y de `hand_pos` (izquierda 10 + derecha 10). AimDK pide
**identificar los ejes por índice**, no por el campo `name`.

| Índice | Izquierda | Derecha | Qué mueve | Rango izq. [rad] | Rango der. [rad] |
|---|---|---|---|---|---|
| 0 | `L_thumb_roll_joint` | `R_thumb_roll_joint` | giro de la base del pulgar (oposición) | −0,873 … +0,175 | −0,175 … +0,873 |
| 1 | `L_thumb_abad_joint` | `R_thumb_abad_joint` | abducción del pulgar (0 = pulgar fuera, máx. = cruzado sobre la palma) | 0 … +1,745 | −1,745 … 0 |
| 2 | `L_thumb_mcp_joint` | `R_thumb_mcp_joint` | flexión del pulgar (arrastra pip ×1,33 y dip ×1,30) | −0,855 … 0 | 0 … +0,855 |
| 3 | `L_index_abad_joint` | `R_index_abad_joint` | separación del índice | 0 … +0,209 | −0,209 … 0 |
| 4 | `L_index_pip_joint` | `R_index_pip_joint` | flexión del índice (arrastra dip ×1,097) | 0 … +1,571 | 0 … +1,571 |
| 5 | `L_middle_pip_joint` | `R_middle_pip_joint` | flexión del corazón (arrastra dip ×1,097) | 0 … +1,571 | 0 … +1,571 |
| 6 | `L_ring_abad_joint` | `R_ring_abad_joint` | separación del anular | −0,175 … 0 | 0 … +0,175 |
| 7 | `L_ring_pip_joint` | `R_ring_pip_joint` | flexión del anular (arrastra dip ×1,097) | 0 … +1,571 | 0 … +1,571 |
| 8 | `L_pinky_abad_joint` | `R_pinky_abad_joint` | separación del meñique | −0,175 … 0 | 0 … +0,175 |
| 9 | `L_pinky_pip_joint` | `R_pinky_pip_joint` | flexión del meñique (arrastra dip ×1,097) | 0 … +1,571 | 0 … +1,571 |

Ejes acoplados (en MuJoCo, restricción `<equality joint>`, `q_mimic = k · q_activo`):

| Eje acoplado | Sigue a | k |
|---|---|---|
| `*_thumb_pip_joint` | `*_thumb_mcp_joint` | 1,33 |
| `*_thumb_dip_joint` | `*_thumb_mcp_joint` | 1,30 |
| `*_{index,middle,ring,pinky}_dip_joint` | `*_{…}_pip_joint` | 1,097 |

Marco de la palma (`L_palm`/`R_palm`): **+z** hacia la punta de los dedos y **+x** hacia el lado interior, donde se
flexionan los dedos y queda el pulgar, en las dos manos. Con el brazo colgando, la palma mira hacia el cuerpo.

## 2. Cómo se mandan en el robot real (AimDK)

### a) Recomendado: control de tren superior (el MC sigue llevando el equilibrio)

1. `SetMcAction` → `STAND_DEFAULT`, después `UPPERBODY_REMOTE_SPLIT` (`set_mc_action SD`, luego `URS`).
2. Publicar `UpperBodyCommandArray` en `/mc/upper_body_command` (BEST_EFFORT, ~50 Hz) con `header.stamp` a menos de
   200 ms del reloj del MC; si no, se descarta. Con `stamp = 0`, el MC usa la hora de recepción.

| `hand_sub_mode` | `hand_pos` | Significado |
|---|---|---|
| 1 `HAND_CLAW_OPEN_CLOSE` | 2 valores | `[izq_abrir, der_abrir]` 0-1 (pinza) |
| 2 `HAND_DEXTEROUS_JOINT` | **20 valores** | izquierda 10 + derecha 10, en radianes, orden de la tabla anterior |
| 3 `HAND_DEXTEROUS_GESTURE` | 4 valores | `[gesto_izq, abrir_izq, gesto_der, abrir_der]` |

El mismo mensaje lleva `arm_pos[14]` (izquierdo 7 + derecho 7). `head_pos[2]` solo se aplica en `HEAD_ONLY`. El
ejemplo oficial es `ros2 run py_examples upper_body_control joint`, que lleva cada eje a la mitad de su rango.

### b) Bajo nivel: HAL directo (hay que parar el MC)

- `aima em stop-app mc` en PC1 (si no, el MC retoma la mano) y publicar `HandCommandArray` en
  `/aima/hal/joint/hand/command`.
- `left_hand_type`/`right_hand_type` = 1 (`NIMBLE_HANDS`, la OmniHand). Con 0 o 0xFF la orden se descarta en silencio.
- Cada `HandCommand` lleva `name`, `position` [rad], `velocity`, `acceleration`, `deceleration` y `effort`. En la
  OmniHand **solo se usa `position`**; el control híbrido fuerza-posición va por el modo a).
- Este topic, como el resto de `/aima/hal/joint/*/command`, **no tiene timeout**: si el emisor calla, la mano mantiene
  la última orden.

### Estado y tacto

`/aima/hal/joint/hand/state` (`HandStateArray`, BEST_EFFORT + TRANSIENT_LOCAL, ~200 Hz, baja cuando hay carga):

- `HandState[]` con `position` [rad], `velocity` y `effort` en bruto, `state` (sin uso) y `faultcode`.
- `left_touch_sensors`/`right_touch_sensors` (`HandTouchSensorData`), códigos **uint8** sin calibrar:

| Campo | Tamaño | Uso en la OmniHand |
|---|---|---|
| `palm_touch_data` | 36 | 5 × 5 = 25 primeras celdas |
| `back_of_hand_touch_data` | 36 | — |
| `thumb_touch_data`, `index_finger_touch_data`, `middle_finger_touch_data`, `ring_finger_touch_data`, `little_finger_touch_data` | 16 cada uno | las 16 celdas de la yema |

- Con tipo de mano 0 (no conectada) o 0xFF (error), los arrays **conservan el último valor válido**: no indican cero.
- `GetHandType` (`/aimdk_5Fmsgs/srv/GetHandType`) devuelve `left_hands_type`/`right_hands_type`. El campo `reponse` no
  está habilitado, así que no hay que fiarse de su código.
- Cuidado: el ejemplo `omnihand_touch` de AimDK **publica una orden de mano cada segundo** (posición 0 = abrir). Para
  solo leer el tacto, usa un suscriptor sin publicador: `x2_ultra.robot.tactile` hace exactamente eso.

## 3. Las manos en este simulador

- **Modelo** (`src/x2_ultra/model/builder.py`): palma, dedos, mallas, inercias y ejes salen del URDF y se injertan
  en el MJCF oficial; la brida de muñeca se sustituye por la de la variante OmniHand. Los 12 mimic son `equality joint`.
- **Actuadores**: 20 actuadores de posición (PD interno), con `kp = 2 N·m/rad`, `kv = 0,06`, par limitado a ±1,5 N·m,
  armadura 0,001 y amortiguamiento 0,02. Son valores de simulación elegidos para seguimiento estable a 500 Hz; no
  vienen de una identificación de la mano real.
- **Colisiones**: las del URDF (cilindros y cajas por falange, caja y cilindro en la palma). Los dedos chocan con el
  cuerpo, la mesa y los objetos, pero no entre sí (`contype` 2 / `conaffinity` 1), para que el puño cerrado no genere
  contactos espurios. Fricción 1,2 y `condim` 4 para poder agarrar.
- **Contacto para agarrar**: con el contacto blando por defecto de MuJoCo los objetos resbalaban. El modelo del
  simulador usa cono de fricción elíptico, `impratio` 10 y 5 iteraciones *noslip* (el mismo arreglo que en el proyecto
  GARDIAN). En la prueba "objeto soltado dentro de la mano" (botella de 250 g, cubo de 80 g y pelota de 7 cm, cada uno
  en 3 posiciones), el agarre cilíndrico pasa de 4/9 a **9/9** y la pinza de 5/9 a 8/9. La marcha no cambia y el
  simulador baja de ~12× a ~5× tiempo real (`tests/test_engine.py::test_power_grasp_holds_objects`).
- **Tacto simulado**: un sensor `touch` de MuJoCo (fuerza normal en N) por región: palma y las 5 yemas de cada mano.
  Son las mismas regiones que en AimDK, pero en newtons, no en códigos uint8. Se ven en la pestaña **Manos** y en
  `SimEngine.info.touch`.
- **Punto de agarre**: un sitio `L_grasp`/`R_grasp` en (0,045, 0, 0,09) del marco de la palma, delante de ella y a
  media altura de los dedos. Es lo que mueve la IK.

### Unidades normalizadas y poses

En la interfaz cada eje va de **0 % (abierta)** a **100 %**, el extremo del rango más alejado de 0: dedo flexionado,
pulgar cruzado o separación máxima. Así la misma pose vale para las dos manos aunque los signos estén espejados
(`omnihand.to_radians` / `to_normalized`).

| Pose | Rotación pulgar · abd. · flexión | Separaciones (índice, anular, meñique) | Flexión (índice, corazón, anular, meñique) |
|---|---|---|---|
| `abierta` | 0 · 0 · 0 | 0 | 0 |
| `puño` | 30 · 90 · 80 % | 0 | 100 % |
| `pinza` | 50 · 0 · 50 % | 0 | 50, 10, 10, 10 % |
| `agarre` (cilindro) | 90 · 0 · 40 % | 0 | 70, 75, 80, 85 % |
| `señalar` | 30 · 90 · 80 % | 0 | 0, 100, 100, 100 % |
| `ok` | 50 · 0 · 50 % | 0, 50, 80 % | 50, 0, 0, 0 % |
| `pulgar_arriba` | 0 · 0 · 0 | 0 | 100 % |
| `separar` | 0 · 0 · 0 | 100 % | 0 |

La pinza no se ajustó a ojo: es el mínimo de una búsqueda por cinemática de la distancia entre los sitios
`*_thumb_tip` e `*_index_tip`. Queda en **13 mm** entre puntas, es decir, las yemas en contacto.

### Controles

- Teclas **G** (puño ↔ abrir) y **T** (pinza ↔ abrir) sobre la mano del brazo activo (**Tab** cambia de brazo).
- Pestaña **Manos**: 10 sliders por mano, "cierre global" (mezcla de abierta hacia la última pose cerrada) y barras
  de tacto.
- Pestaña **Poses**: las 8 poses para la mano izquierda, la derecha o ambas.
- Desde código: `SimEngine.set_hand_pose("LR", "pinza")` o `set_hand_targets("L", q10)` en radianes y orden SDK.

## 4. Lo que había en g1_teleop y qué se ha traído

| En g1_teleop | Aquí |
|---|---|
| `robots/x2/profile.py`: orden SDK de las manos, topics y modos | `x2_ultra/robot/omnihand.py` y `joints.py` (mismos órdenes, verificados contra AimDK) |
| `contracts/tactile.py` + `adapters/robots/x2/tactile.py`: decodificador táctil de solo lectura | `x2_ultra/robot/tactile.py` (sin dependencias del teleop) |
| `retargeting/omnihand.py`: dedos a partir del esqueleto de mano de las gafas XR | **no se trae**: depende del visor VR |
| `robots/descriptors/x2_ultra_n_omnihand.json` | sustituido por el modelo MuJoCo generado |
