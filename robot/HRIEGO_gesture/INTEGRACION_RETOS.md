# Integración de HRIEGO_gesture en los retos 1 y 2 (Autoriente)

Estado: documento de diseño. No se ha cambiado ningún código. Las referencias `sabanahack:ruta:línea` apuntan a `git show origin/sabanahack:<ruta>` en el repositorio x2_ultra. Las cifras de HRIEGO vienen de `HRIEGO_gesture/README.md`, `twin/runs/p4_commercial/REPORT.md` y `twin/runs/c12_transitions/REPORT.md`.

## 1. Contexto: dos simuladores distintos

**Nuestros resultados** se obtienen en el gemelo digital oficial del X2 (AgiBot MC + AimDK, MC v1.0.0). Ahí se usan presets de emoción por el dominio 77 del SDK (`SetMcPresetMotion`), la fuente URS (`UPPERBODY_REMOTE_SPLIT`) con brazos y cintura de SONIC, McBodyPose para cintura y cabeza, y `preplaying_tick` 500 sin tocar. Los brazos de SONIC llegan por URS y el MC congela la cabeza durante los presets (c12, probe 3).

**Los retos** se simulan en el motor propio `show/` de la rama `origin/sabanahack`, que es MuJoCo sin MC:
- La policy de "brazos libres" controla piernas y cintura (`src/x2_ultra/show/engine.py:160`). Los brazos y la cabeza son actuadores de posición que se escriben desde `RobotRig` (`engine.py:161-166`, con `controls_arms` falso).
- No hay presets del MC ni reloj de audio. La voz sale de `espeak-ng` (`show/app.py:624-632`) y el vídeo de `ffplay` aparte, con `-ss` y reloj `time.perf_counter` (`show/screen.py:79`, `:197-200`).
- No hay grabador MP4. El único `--video` es de entrada (`show/app.py:4`, `:640`).
- Reto 1 tiene dos robots: `ultra` (X2 Ultra-N, cuello de 2 ejes, y = -0,75) y `edu` (X2 EDU, sin cuello, y = +0,75) (`show/stage.py:85-88`). Reto 2 tiene un robot, el Ultra-N, en el vestíbulo del club (`show/stage.py:96-109`, `show/club.py:59`, `ROBOT_START = (4.4, 0.8, 0.0)`).

Consecuencia: lo que demostramos en el gemelo no se reproduce en el motor de los retos sin adaptar la capa de órdenes. Las dos capas tienen semánticas distintas (presets frente a objetivos de posición con límite de velocidad).

## 2. Formato propuesto `performance.v1` (solo especificación)

Un formato independiente del simulador, con un fichero por espectáculo y un bloque por robot:

- **Timeline de conversación.** Lista de líneas, cada una con `wav` y `t_start` en un reloj de conversación (segundos desde el inicio). En el gemelo ya existe: `twin/runs/p4_commercial/timeline_{ultra,original}.json` y `conversation_clock.json`. En p4 se usa `t_k = end(k-1) + 0,30 s`.
- **Trayectorias articulares a 50 Hz.** Brazos (14, orden AimDK), cintura (3) y cabeza (2). Proceden de los `*_arms.npz` de SONIC. Convención a confirmar leyendo el npz: se toma el fotograma 100 como t0 del audio (p4 REPORT, "SONIC clip from frame 100").
- **Eventos de emoción.** `motion_id`, `area`, ancla CTC (`anchor_s`), despacho alineado al trazo (`dispatch = anchor - lead_stroke`, c08), duración y parámetros de transición V2 (fade de salida 0,6 s, ventana de mínimo de velocidad ±0,3 s, fade de entrada 0,7 s; c12 REPORT). Catálogo en `contract/catalog.json`: 12 emociones con `motion_id` y 3 POINT_* con `target_vector`.
- **Eventos de apuntado.** Dirección (`POINT_RIGHT/LEFT/FRONT`), fases preparación/mantenimiento/recuperación y ancla. En HRIEGO `motion_id` es `null` para POINT (`anchor_only_no_preset`).
- **Intervalos de mirada.** Para la cabeza, con la ventana de tiempo y el objetivo (en el gemelo, `Ultra head +0,35 rad` durante las líneas del otro robot, p4 REPORT).

**Replay pack.** `twin/replay/` existe en este checkout: timelines comerciales, 19 clips del show comercial (`commercial/audio`, `commercial/motion`), 31 arms SONIC de visitas con su WAV de 16 kHz (`visits/<rank>/<turn>/`) y las metricas c08 (`presets_c08/`). Ver `twin/replay/README.md` para el formato `*_arms.npz`. Queda por confirmar con el agente que produce `performance.v1` que este formato es el definitivo.

## 3. Receta de integración del reto 1 (`ultra` + `edu`)

Mapeo de robots: X2 Ultra → `ultra` (Ultra-N, con cabeza); X2 Original → `edu` (sin cabeza, `show/stage.py:33` `has_head`).

1. **Cola de órdenes a 50 Hz en `RobotRig`.** Hoy `set_arm_targets` y `set_head_targets` (`engine.py:101-111`) fijan un objetivo y el control avanza con `ARM_RATE` y `HEAD_RATE` (`sim/engine.py:36-37`: 2,5 y 2,0 rad/s). Para reproducir SONIC hay que pasar el valor exacto de cada tick. Opciones: (a) saltarse el límite de velocidad para el flujo de SONIC, o (b) subir `ARM_RATE` solo en ese modo. Recomendado (a) con un límite de salvaguarda por tick, para no romper la regla de `clamp_arms`.
2. **Cintura como delta sobre la salida de la policy.** Los pies y la cintura salen de `policy.act` (`engine.py:160`). Sumar el delta de cintura de SONIC a `d.ctrl[self.act_policy]` cambia el equilibrio; el riesgo está señalado. Medir antes de usarlo en el show.
3. **Reloj de audio común.** Reemplazar `ffplay` y `perf_counter` (`screen.py:79-87`, `:197-200`) por un reloj único del espectáculo que avance con la simulación (`ShowEngine.sim_time`, `engine.py:226`). Cada WAV arranca en `t_start` del timeline, no en el instante de lanzar el proceso.
4. **Emociones como reproducción cinemática.** Como en `show/dance.py` (clips cinemáticos que imponen postura en cada paso, `engine.py:301-310`). Las emociones de AgiBot son CSV con `timeMS` y `command_pos::<joint>` (según el brief; no verificado en el repo). Problema de licencia: los assets son externos y su licencia no está clara. Requiere blends tipo V2 (fade de salida 0,6 s, fade de entrada 0,7 s).
5. **EDU ignora la cabeza.** Ya lo hace la capa (`set_head_targets` retorna si `not has_head`, `engine.py:107-108`). En el timeline no deben existir eventos de cabeza para `edu`.
6. **Grabación MP4.** `mujoco.Renderer` por cámara (ya existe `publico`, `stage.py` / `venue.AUDIENCE_CAM`) y mux con `ffmpeg` del timeline de audio. No está implementado. Verificar que el host tiene contexto GL offscreen.

Límite del gemelo que no hay que copiar al motor: en el MC la cabeza queda congelada durante un preset (c12 probe 3) y la ventana V2 se ajusta a esa limitación. En el motor de los retos no hay presets, así que el congelado no aplica, pero hay que decidir si la cabeza mantiene la mirada durante la emoción.

## 4. Receta de integración del reto 2 (recepción, un robot)

1. **Sustituir los `Say` fijos por turnos de HRIEGO.** `guide.py` genera pasos `Say(text, seconds, pose)` (`guide.py:41-45`) con frases escritas a mano en `_greeting` (`:218-237`) y `_route` (`:240-266`). El flujo nuevo: `gesture_context` → `x2-llm` (Qwen3-4B, json_schema, validador) → `Supertonic` → `anchor_plan` → planificador. Los `Say` se convierten en turnos con `text`, `motor_intents` y `anchor_s`.
2. **FSM de categorías ↔ rangos.** `guide.CATEGORIES` (`guide.py:201-207`): `vip`→VIP, `socio`→REGULAR, `invitado`→GUEST, `personal`→STAFF, `externo`→EXTERNAL. Los saludos de HRIEGO usan `GREETING_<rango>` (catálogo: VIP 3001/11, REGULAR 1003/2, GUEST 1011/2, STAFF 1008/2, EXTERNAL 1002/2).
3. **POINT_* para rutas.** `guide.py` ya tiene las rutas (`_route`) con `Face(yaw)` al llegar. Los gestos `POINT_RIGHT/LEFT/FRONT` se usan para indicar el ascensor o la puerta, con su `target_vector`. Los apuntados de HRIEGO aún no están bien calibrados (ver §5).
4. **Hook de reconocimiento facial.** `guide.py:275-276` deja el punto: "aquí irá el reconocimiento facial con la cámara del PC, ver docs/reto-2/demo.md". `guide.py:284` ya tiene el paso `Pause(1.5, "reconocimiento facial (simulado)...")`. Con `FaceRecognizer.identify` (según `guide.py`; el archivo `docs/reto-2/demo.md` no se ha leído aquí) se sustituye la simulación. HRIEGO deshabilita la cara (`README.md`, sección 1), así que el perfil entra por `run_visit.PROFILES`.
5. **Latencia y memoria.** Objetivo de 2 s (brief). Medido: LLM p50 1,32 s / p95 1,55 s en primer intento, con reintento p95 2,93 s; TTS Supertonic p50 0,67 s; anchor CTC 0,086 s por clip. En serie, p50 ≈ 2,1 s antes de sonar: al límite. Memoria: `x2-llm` pico 5,7 GB de RAM; 3 workers (StreamTalk, GMR, SONIC) estables 7,9 GB (pico ~10 GB con pipeline); Supertonic RSS pico 505 MB. Plataforma objetivo Orin NX 16 GB (memoria unificada). Sumando, son ~14 GB antes del SO y de MuJoCo: no cabe con holgura. Medir en el Orin.
6. **Qué corre en vivo y qué offline.** En vivo: LLM, validador, TTS y anchor (sin cuello de botella). Offline: SONIC (StreamTalk → GMR → SONIC) para NONE y POINT; 6,3 s en caliente y 31 s en frío (`workers/REPORT.md`). Por tanto, SONIC no puede ir en el turno: hay que precomputarlo para las frases de guía o aceptar un hueco. Los presets de emoción sí son en vivo (lanzamiento por dispatch).
7. **Sin reloj de audio.** Como en el reto 1, sustituir `say()` y `espeak-ng` (`engine.py:243-245`, `app.py:624-632`) por la salida de los WAV de Supertonic con su `t_start`.

## 5. Checklist de requisitos externos y riesgos abiertos

Requisitos externos:
- [ ] Acceso al MC y AimDK en el robot real para presets y URS (los datos de `c12` y `p4` son del gemelo).
- [ ] Licencia de los CSV de animación de AgiBot (sin confirmar) y de los checkpoints de SONIC y StreamTalk.
- [ ] Contenedor `x2-llm` con Qwen3-4B Q4_K_M y llama-server; el puente es `llm_bridge.py` (`docker exec`).
- [ ] Supertonic M1 (`tts.py`, sobre `hriego_v2`) y QuartzNet CTC (`align/`).
- [ ] `espeak-ng` en la máquina del show (solo para la voz de la simulación).
- [ ] `imageio_ffmpeg` y `ffmpeg` para vídeo y audio; `ffplay` para el audio actual del motor de los retos.
- [ ] Orin NX 16 GB con presupuesto de memoria medido (no hay cifras de Orin en el repo).
- [ ] Reto 1: la presentación por voz (parte 1 del enunciado, `docs/10-reto-1.md` §5) no está en la simulación.
- [ ] Reto 2: integrar el sistema de reconocimiento facial existente (socio / no socio), que el código deja fuera a propósito.

Riesgos abiertos:
- **Replay pack**: `twin/replay/` ya existe (ver seccion de replay arriba). El formato `performance.v1` aun debe confirmarse con su productor.
- **Cintura como delta** sobre la policy (§3.2): puede desestabilizar. Sin medir.
- **Cabeza congelada** en el MC durante presets (c12 probe 3); en p4, Ultra quedó a -0,34 rad al empezar la despedida (p4 REPORT, desviación 2). Solución propuesta en el propio informe, no aplicada.
- **Picos de velocidad** en el handoff: hasta 4,6 rad/s en Ultra farewell y 3,6 rad/s en Original applause (p4 REPORT, desviación 1). Superan los 1,3 rad/s de c12.
- **Anclas CTC no validadas por humanos** (README, §3.2). El error de disparo de emociones en el gemelo es +1 a +19 ms, pero el error del ancla no está medido frente a audio real.
- **Apuntado**: POINT supera 15° en 6 de 10 ejecuciones del gemelo; la media en el hold es 4-8° (README, §3.5).
- **LLM**: 86,5 % de contrato válido final; NONE devuelve INVALID en 3 de 16; SOCIAL_HEART con recall 0,50 (README, §3.1). Precisión 1,0 en todas las emociones: el sistema prefiere no disparar.
- **Latencia**: el objetivo de 2 s está al límite (§4.5).
- **Memoria** en Orin NX 16 GB (§4.5).
- **Sincronía de audio**: `ffplay` no se resincroniza con la imagen (`docs/10-reto-1.md` §5). Se resuelve con el reloj común (§3.3).
- **Trazabilidad**: `validation/trace_check.py` y `latency_budget.py` leen trazas de `twin/runs/` que no están en este commit (README, §3.5).
- **Cifras de planes**: los planes almacenados son `--align onset`; el defecto actual es `auto` (stroke), con retardos de inicio de audio de hasta 1,24 s (README, §3.2).
