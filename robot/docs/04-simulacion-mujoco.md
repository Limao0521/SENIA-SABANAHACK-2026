# 04 · Cómo funciona el simulador MuJoCo de este repo

```
 teclado / ratón / sliders (Qt)                         x2_ultra.app
          │
          ▼
 Controller ── IK de brazos (ArmIK), poses, manos, velocidad por teclas
          │ objetivos (vx, vy, wz) · brazos[14] · cabeza[2] · dedos[20]
          ▼
 SimEngine (hilo propio, tiempo real)                   x2_ultra.sim.engine
   cada 20 ms: policy ONNX (piernas+cintura) ─┐
               brazos/cabeza/dedos con límite  ├─> ctrl de actuadores de posición
               de velocidad                    ┘
   cada 2 ms:  mj_step (MuJoCo 3.15)
          │ qpos (copia con cerrojo)
          ▼
 MujocoViewport: render fuera de pantalla (EGL) → QImage + HUD
```

## 1. Modelo (`x2_ultra/model/builder.py`)

Se genera en memoria al arrancar (~1 s) a partir de los archivos oficiales de AgiBot. `x2-build-model` además lo
exporta a `models/`.

1. **Base**: `X2_URDF-v1.4.0/X2-Ultra.xml`, el MJCF oficial del Ultra-N. Se conservan colisiones, inercias, la IMU
   `imu_0` de la pelvis, los defaults de articulación (armadura 0,03, fricción seca 0,3) y los pies, que tocan el
   suelo con 12 esferas de 5 mm por pie.
2. **Muñeca de la variante OmniHand**: inercia (0,081 kg) y malla `*_wrist_roll_link{10,2}.stl` del URDF con manos.
3. **Manos**: cuerpos, articulaciones, mallas y colisiones de `X2-Ultra_omnihand.urdf` (rpy de URDF convertidos a
   cuaterniones y tensores de inercia girados). Las puntas sin masa son sitios (`*_tip`). Los 12 mimic son
   `equality joint` (ver [02](02-omnihand.md)).
4. **Actuadores de posición** (PD dentro de MuJoCo, integrador `implicitfast`): 31 del cuerpo, con Kp/Kd de AgiBot
   y el par máximo del Ultra-N como `forcerange`, más 20 de dedos. El orden de actuadores es `BODY_JOINTS` + `HAND_JOINTS`.
5. **Sensores**: IMU oficial, posición y velocidad de los pies, contacto pie-suelo y tacto de palma y yemas.
6. **Mundo**: suelo, luces y cielo. La escena `mesa` añade una mesa a la izquierda del robot (gira 90° con Q y
   avanza ~1 m) con un cubo de 80 g, una botella de 250 g y una pelota.
7. Exclusión cabeza-torso: sus colisiones se solapan en la postura por defecto.

Masa total 45,18 kg y 87 GDL (6 base + 31 cuerpo + 32 dedos + objetos). Paso de 2 ms.

## 2. Bucle de control (`SimEngine`)

Cada 20 ms (50 Hz, el periodo de la policy):

1. **Velocidad**: la orden de las teclas se suaviza con rampas (1,2 m/s² lineal, 2,5 rad/s² angular) y se recorta al
   rango de entrenamiento (vx −0,6…1,0, vy ±0,4, wz ±1,0).
   - *Velocidades por tecla* (pestaña Marcha): W 0,6 m/s, S 0,4, A/D 0,3, Q/E 1,0 rad/s; Mayús las multiplica por
     1,6 dentro del rango.
   - *Mantener rumbo*: si caminas sin pulsar Q/E, un P (1,5·error, ±0,35 rad/s) corrige la deriva de rumbo.
2. **Marcha**: la policy recibe la observación (abajo) y devuelve los objetivos de las 15 articulaciones de piernas
   y cintura.
3. **Brazos y cabeza**: siguen el objetivo del usuario con un límite de velocidad (2,5 rad/s brazos, 2 rad/s cabeza)
   y compensación de gravedad a través de sus actuadores (`gravcomp` + `actuatorgravcomp`, como el *feedforward* de
   un control real; sin ella el brazo extendido se descolgaba ~0,2 rad),
   para que un salto de slider o de pose no llegue como escalón. Las poses animadas (saludar, aplaudir) suman una
   oscilación senoidal.
4. **Dedos**: objetivo del usuario a ≤ 6 rad/s.
5. **Caída**: con la pelvis por debajo de 0,40 m o el tronco inclinado más de 57°, estado `caido`. Los actuadores del
   cuerpo pasan a amortiguamiento puro (Kp = 0, se conserva Kd), como el modo DAMPING del robot. **R** reinicia.
6. **Empujones** (flechas): 120 N durante 0,15 s sobre el torso.

La física avanza 10 pasos de 2 ms con `mj_step(nstep=10)`. Sin ventana corre a ~5× tiempo real en este PC (el
modelo completo, con dedos, objetos y el contacto para agarrar: cono elíptico, `impratio` 10, *noslip* 5); con la
app, a 1× y con margen.

## 3. Policy de marcha "brazos libres"

`assets/policies/x2_arms_free_walk.onnx`, entrenada en este repo (ver [06](06-locomocion-rl.md)). Contrato
(`control/obs_spec.py`):

| Término (historial de 5, el más antiguo primero) | Dim | Escala |
|---|---|---|
| velocidad angular de la pelvis (marco local) | 3 | 0,25 |
| gravedad proyectada en la pelvis | 3 | 1 |
| orden (vx, vy, wz) | 3 | 1 |
| posición articular − postura por defecto (31 ejes del cuerpo, **brazos y cabeza incluidos**) | 31 | 1 |
| velocidad articular | 31 | 0,05 |
| última acción | 15 | 1 |

Entrada de 430 valores. La salida son 15 acciones (piernas + cintura) y el objetivo es `postura por defecto +
0,25·acción`, con la acción recortada a ±5. La red no mueve los brazos, pero **los ve**: así compensa el centro de
masas cuando el usuario los lleva al frente, arriba o a un lado.

### Espejo izquierda-derecha para girar hacia los dos lados

La red instalada gira bien en el sitio hacia la izquierda, pero no hacia la derecha: partiendo de quieta, con
wz = −0,6 o −0,8 no da ni un paso. A todas las policies entrenadas les pasaba con un lado u otro, y también a la de
AgiBot (ver [06](06-locomocion-rl.md#4-resultados)). Como el robot es simétrico, la solución no requiere reentrenar.
Cuando se pide girar hacia el lado malo, `ArmsFreePolicy` evalúa la red **reflejada**:

1. Refleja la observación: intercambia izquierda y derecha. Pitch, rodilla y codo conservan el signo; roll y yaw lo
   cambian, porque los ejes del X2 son iguales en los dos lados. En la base, ω → (−ωx, ωy, −ωz), la gravedad
   proyectada → (gx, −gy, gz) y la orden → (vx, −vy, −wz).
2. Ejecuta la red.
3. Refleja la acción de vuelta.

El historial se guarda siempre en el marco real y se refleja entero en cada paso. Así, cambiar de lado no deja la
red con un estado incoherente. El lado bueno se declara en `policy.json` (`"preferred_turn": 1`, izquierda) y hay
una histéresis de 0,1 rad/s. Comprobaciones:

- `tests/test_mirror.py` construye en MuJoCo el estado reflejado de verdad y compara su observación con la reflejada.
- Con la red siempre reflejada, 0 caídas en los 18 escenarios de evaluación normales y difíciles.
- 0 caídas en 16 pruebas de 30 s alternando el sentido de giro: caminando, en el sitio, con brazos aleatorios y con
  empujones, con hasta 29 cambios por prueba. Con los otros dos candidatos evaluados, 0 caídas en 48.

Resultado en el sitio: ±0,44 / ±0,72 / ±0,94 rad/s con órdenes de ±0,6 / ±0,8 / ±1,0
(`python -m x2_ultra.tools.evaluate --giro`).

### Umbrales de arranque

La policy no echa a andar con órdenes muy pequeñas. El MC del robot real también tiene umbrales de arranque, aunque
distintos: avance ~0,1 m/s, lateral ~0,5-0,7 m/s y giro ~0,03-0,05 rad/s; además, la guía oficial no garantiza el
giro en el sitio ([03](03-sdk-aimdk.md)). Los de esta policy, medidos:

- con 0,2 m/s apenas se mueve (0,04 m/s) y en lateral no responde;
- desde ~0,25-0,3 m/s anda;
- al parar, frena en menos de 6 cm;
- en el sitio gira poco por debajo de ~0,6 rad/s, por eso Q/E piden 1 rad/s.

Para acercarse con precisión hay que usar órdenes de al menos 0,28 m/s y parar al llegar, como hace `demo_mesa`.

### Alternativa: la policy oficial de AgiBot (`--policy agibot`)

`assets/policies/agibot_mcrl_policy.onnx` (mc-rl, obs 500 → 29 acciones de cuerpo entero). Camina bien, pero también
mueve los brazos, así que los sliders, la IK y las poses de brazo no tienen efecto (el HUD lo avisa). Medido en este
simulador: no se cae y avanza a 0,45 m/s cuando se le piden 0,5. En el sitio, con ±0,8 rad/s gira −0,42 rad/s a la
derecha y nada a la izquierda (está validada para ±0,2). Si se sobrescriben sus brazos con posturas grandes, se cae
(ver [06](06-locomocion-rl.md)).

## 4. Brazos

### Sliders (pestaña Brazos)

Un slider por eje (en grados) para los 14 de brazo y los 2 de cabeza. La casilla *Espejo* mueve el eje simétrico
del otro brazo, con roll y yaw cambiados de signo.

### IK con teclado (pestaña IK)

`control/arm_ik.py`: cinemática inversa de 7 ejes con el propio modelo MuJoCo, sin Pinocchio ni otras dependencias.

- Objetivo: posición y orientación del punto de agarre (`L_grasp`/`R_grasp`) en el **marco del torso**, así que la
  mano conserva su postura respecto al cuerpo aunque el robot camine.
- Método: Levenberg-Marquardt con amortiguamiento adaptativo y aceptación por mejora del error. Los ejes que tocan
  su límite (con 0,03 rad de margen) se congelan, y hay un tirón en el espacio nulo hacia una postura cómoda (codo
  flexionado). La orientación pesa 0,2 frente a la posición, de modo que si no hay solución exacta se prioriza el sitio.
- Rendimiento: 1-3 ms por solución. Partiendo de lejos alcanza el 96 % de objetivos alcanzables en posición y el
  75 % con orientación completa; en uso interactivo arranca de la solución anterior y converge casi siempre.
- Teclas, mientras se mantienen: I/K, J/L y U/O mueven la mano a 25 cm/s; Z/X, C/V y B/N la giran a 1,2 rad/s; Tab
  alterna izquierdo, derecho y ambos (espejo respecto al plano sagital). Los objetivos son relativos a la postura que
  había al activar la IK (±45 cm, ±90°).
- En la vista 3D aparece una esfera amarilla con ejes RGB en cada objetivo IK activo.

### Poses (teclas 0-9, pestaña Poses)

| Tecla | Pose | Notas |
|---|---|---|
| 0 | reposo | postura por defecto, manos abiertas |
| 1 | saludar | brazo derecho de lado con antebrazo vertical; oscila el yaw del hombro (±0,3 rad, 1,2 Hz) |
| 2 | brazos al frente | horizontales |
| 3 | brazos arriba | |
| 4 | cruz (T) | |
| 5 | cargar caja | antebrazos al frente, manos en agarre |
| 6 | señalar | brazo derecho al frente, mano señalando |
| 7 | dar la mano | brazo derecho adelantado, mano en agarre |
| 8 | manos a la cintura | resuelta con IK (objetivos en la cadera) |
| 9 | aplaudir | resuelta con IK (manos juntas delante del pecho) y animada |

Las poses de brazo usan la convención del X2 (ver [01](01-robot-x2-ultra-n.md)) y se verificaron renderizando vistas
frontal y lateral.

## 5. Interfaz

- **Vista 3D**: MuJoCo renderiza fuera de pantalla (EGL en la GPU; `MUJOCO_GL=glfw` si EGL falla) y Qt la muestra
  con un HUD: estado, orden de velocidad, brazo activo, tiempo y factor de tiempo real. La flecha azul es la orden de
  velocidad. Ratón: izquierdo orbita, derecho desplaza, rueda zoom, doble clic sigue al robot.
- **Marcha**: estado detallado, velocidades máximas de las teclas, mantener rumbo, cámara, visualización de
  contactos, colisiones y sitios, velocidad de simulación (0,1-2×), reiniciar, pausa y empujón.
- **Brazos / IK / Poses / Manos**: descritas arriba y en [02](02-omnihand.md).
- El teclado se captura en toda la ventana, aunque el foco esté en un slider. Al perder el foco se sueltan todas
  las teclas, para que el robot no siga andando.

## 6. Uso sin ventana

```python
from x2_ultra.sim.engine import SimEngine
eng = SimEngine(scene="vacia")              # policy "brazos libres"
eng.play_pose("al_frente")
eng.set_hand_pose("LR", "agarre")
eng.set_velocity_target(0.4, 0.0, 0.3)
eng.run_for(5.0)                            # sin tiempo real
print(eng.info.base_pos, eng.state)
```

- `python -m x2_ultra.tools.evaluate [--policy agibot] [--duro]`: 13 escenarios (de pie, avanzar, retroceder, lateral,
  girar a izquierda y derecha, poses y brazos aleatorios de pie y andando, empujones) con caídas y velocidad medida;
  `--duro` hace 7 más (empujones de 200 N, brazos aleatorios andando rápido, girando a cada lado o de lado, poses
  girando). `--giro` mide la curva de giro en el sitio con y sin espejo y sugiere `preferred_turn`.
- `python -m x2_ultra.tools.record salida.mp4`: vídeo de demostración.
- `python -m x2_ultra.tools.demo_mesa [--video f.mp4]`: tarea completa programada con la misma API que usa la
  app: girar 90°, acercarse a la mesa con paso lateral para alinear la mano, subir la mano por delante del cuerpo
  (en línea recta chocaría con el borde de la mesa), colocarla junto a la botella con la IK en coordenadas del
  mundo, cerrar en agarre, levantar y retroceder. Resultado: botella levantada 14 cm y llevada sin caerse (20 s).

## 7. Límites conocidos

- **Simulación, no el robot**: las ganancias PD son las de simulación de AgiBot, la fricción de articulaciones y la
  armadura son las del MJCF oficial y los dedos tienen ganancias propias de simulación. Nada de esto se ha
  identificado contra el hardware.
- La policy se entrenó en MuJoCo (dt 5 ms, manos rígidas, colisiones de malla aproximadas por cápsulas) y se ejecuta
  con dt 2 ms y dedos articulados. La evaluación (`tools/evaluate.py`) se hace en el modelo completo.
- No hay cámaras ni LiDAR simulados.
- El agarre depende del contacto blando de MuJoCo (fricción 1,2 y `condim` 4). Los objetos pequeños se agarran
  mejor con la pinza o el agarre cilíndrico que con el puño.
- La cintura la controla la policy, no el usuario.
- Coger desde arriba objetos bajos sobre la mesa no funciona: con la palma hacia abajo, el pulgar de la OmniHand
  toca el tablero antes de que la mano llegue. Funciona el agarre lateral (palma vertical), como en `demo_mesa`.
- En el sitio, la policy gira poco con órdenes pequeñas (0,44 rad/s con 0,6) y solo domina un sentido. El otro se
  resuelve con el espejo (§3). Con Q/E (1 rad/s por defecto) gira a ±0,94 rad/s.
- No arranca con órdenes lineales por debajo de ~0,25 m/s (§3, umbrales de arranque).
