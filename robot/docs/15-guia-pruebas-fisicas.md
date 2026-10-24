# 15 · Guía de pruebas físicas y despliegue a bordo

Protocolo para validar en el **robot real** cada fase del [plan de despliegue](14-plan-despliegue-a-bordo.md),
desde el preflight de solo lectura hasta la demo completa sin PC externo. Complementa (no sustituye)
el protocolo de sesiones de [docs/09](09-robot-real.md) §1: sus reglas de seguridad siguen en vigor
aquí.

> **En modo hackathon (16 h)** se aplica la versión reducida: T0, T2, T4-lite, T5, T6-lite y T7 ×1
> con los criterios relajados de [docs/14](14-plan-despliegue-a-bordo.md) §2.2. Los criterios
> completos de esta guía quedan como referencia para después. **Las reglas de seguridad de la
> sección 1 NO se relajan nunca.**

## 1. Reglas de seguridad (siempre)

1. **Dos personas mínimo**: una con el **mando** a mano (prioridad 80: siempre retoma el control) y
   otra junto al robot. Parada inalámbrica conectada.
2. Batería **≥50%** para empezar cualquier prueba que mueva el robot (los programas no arman <30% y
   se desarman <20%; las sesiones largas necesitan margen).
3. Robot de pie en `STAND_DEFAULT` antes de cada prueba. **Nunca** Zero-Torque ni Damping con el
   robot de pie.
4. **Nunca salir de URS con los brazos arriba**: `STAND_DEFAULT` los devuelve al default a ~10
   rad/s con ringing de 0,5 s. Salida ordenada = brazos a postura inicial → pausa → `STAND_DEFAULT`
   (con reintentos; `MotionBusy code=9` es conocido). Fallback: mando/app.
5. **Prohibido gestar mientras camina**: el gemelo cae 3/3 en marcha con brazos arriba; el interlock
   caminar→parar→asentar→gestar no se desactiva ni «para probar rápido».
6. Los presets de cuerpo entero **no tienen tecla de parar** (cortarlos a medias deja posturas
   inestables): ante un problema, el mando.
7. Espacio libre alrededor (≥2 m) y suelo llano y seco. Durante pruebas de marcha, nadie en la
   trayectoria.
8. Caída detectada (`body_status` 2/3/6 o `fsm` SAFE): el bridge debe desarmarse solo; la persona
   del mando confirma y se aplica el procedimiento de levantado del fabricante. Registrar el
   incidente completo antes de continuar.
9. Cada sesión escribe su log (`logs/real/*.jsonl` en el PC de apoyo o en el contenedor a bordo) y
   anota batería inicial/final. Sin log, la prueba no cuenta.

## 2. Escalera de pruebas

Orden obligatorio: cada escalón asume el anterior en verde. T0-T1 no mueven el robot; T2-T5 mueven
brazos/cuerpo; T6-T7 son la demo.

### T0 · Preflight de solo lectura (fase F1)

- **Propósito:** verificar que el contenedor `x2-onboard` ve el robot desde el PC2.
- **Procedimiento:** con el robot de pie y el mando cerca: `docker compose exec onboard python -m
  x2_ultra.real.check` en el PC2 (sin cable al PC externo).
- **Criterios:** modo `STAND_DEFAULT`, de pie (`body_status=1`), batería ≥50%, fuente de entrada
  libre, reloj del robot leído, «podría armarse» en verde para walk y arms.
- **Registrar:** salida completa de `check` + `tegrastats` (RAM libre, modo `nvpmodel`).
- **Abortar si:** el MC no responde (¿está el servicio AimDK vivo en PC2?) o la RAM libre <6 GB en
  config A.

### T1 · Benchmark en el Orin (fase F0)

- **Propósito:** sustituir la banda de estimación ×1,8-2,2 por medidas directas.
- **Procedimiento:** `python -m x2_ultra.bench --cores 8 --no-gpu --output runs/bench_orin_<fecha>`
  dentro del contenedor (o venv arm64). `MUJOCO_GL=egl` solo si se corren escenarios con render.
- **Criterios:** informe completo generado; `realtime_50hz` con periodo 20,00 ms y overruns <1%;
  factores x86→Orin recalculados a partir de `policy_inference`/`club_tours`.
- **Registrar:** `runs/bench_orin_*/report.{md,json}` actualizados en el repo.
- **Nota:** la física del gemelo puede no sostener RTF>1 en el Orin — esperable e irrelevante (el
  gemelo no va a bordo); lo que importa son las latencias de pipeline.

### T2 · Stream de brazos desde el contenedor (fase F2)

- **Propósito:** validar el puente player→`/mc/upper_body_command` en el robot.
- **Pre:** T0 verde, robot en `STAND_DEFAULT`, app del fabricante cerrada (sus gestos 6001/1011/3038
  interrumpen), sin otros publicadores en el topic.
- **Procedimiento:**
  - **T2a dry-run:** puente en `--dry-run` (log sin publicar) durante 1 turno HRI; comparar la
    trayectoria registrada con la del gemelo.
  - **T2b articulación a articulación:** como docs/09 §5 — movimiento corto (codo ~0,05 rad),
    verificar que «medido» sigue a «objetivo»; luego envolvente completa a 0,15 rad/s y por último
    0,35 rad/s (techo real).
  - **T2c salidas limpias:** 10 ciclos armar→gesto→salida ordenada (regla 4).
- **Criterios:** error de seguimiento medio ≤0,10 rad (referencia probada: 0,064) sin manos
  instaladas; 0 congelaciones no recuperables con R/reancla; 10/10 salidas limpias; robot siempre
  de pie.
- **Registrar:** `logs/real/onboard-brazos-*.jsonl`, batería, vídeo de la mejor toma.
- **Abortar si:** el MC no confirma URS, `body_status`≠1 sostenido, o el error supera 0,3 rad
  (umbral de seguimiento del puente → congelación; si congela 2 veces seguidas, parar y revisar).

### T3 · Marcha + gesto con interlock (fases F2/F3)

- **Propósito:** validar caminar→parar→asentar→gestar en el robot con locomoción del MC activa.
- **Procedimiento:** ruta corta (3-4 m, velocidad nivel 3 = 0,3 m/s adelante; lateral y giro solo
  ≥0,5 m/s y ≥0,6 rad/s — umbrales de arranque del MC). Al llegar: parada, espera de `STABLE`
  (hasta 6 s), gesto con limiter 0,35 rad/s, vuelta a postura, reanudar marcha.
- **Criterios:** 3 rutas completas sin caídas; el gesto solo arranca con el robot parado y estable;
  stroke−ancla ≤150 ms medido en el robot (techo real, no los 100 ms del sim).
- **Registrar:** log del bridge + odometría si llega (`/aima/hal/odom/state` no llegaba al PC
  externo; a bordo debería) + vídeo lateral.

### T4 · Presets y re-timing (fase F3)

- **Propósito:** calibrar lags con MC v1.1.4 y validar presets (29 confirmados, incl. bow 3001).
- **Procedimiento:** reproducir 3 presets por gesto clave; medir el preplay real de 1,0 s y el lag
  audio-gesto resultante; ajustar `TRACK_LAG_S` y los offsets del player; re-probar el gesto
  SONIC rate-limitado contra el preset equivalente y **elegir por gesto** (criterio: amplitud ≥70%
  de la diseñada y stroke dentro de presupuesto).
- **Criterios:** tabla de lags medidos cerrada; 0 caídas; `MotionBusy` solo en salidas (regla 4).
- **Registrar:** `docs/metrics/<fecha>/preset_latency_robot.json` (homólogo del
  `preset_latency_c08.json` del gemelo) + PROVENANCE de `assets/hri/` actualizado.

### T5 · Dry-run HRI con personas (fase F3)

- **Propósito:** el FSM de 6 turnos (GREETING/GUIDANCE/DIALOGUE×3/FAREWELL) con personas reales y
  paquetes precomputados (identidades de `assets/hri/identities.json`, sin Face).
- **Procedimiento:** 3 personas distintas × ciclo completo: llegada → bienvenida por rango → guía
  con marcha+interlock → 3 diálogos gestualizados → despedida (preset bow) → POINT → marcha guiada.
- **Criterios:** 3/3 ciclos completos, 0 caídas, 0 gestos caminando, respuesta del sistema ≤2 s por
  turno (latencias x86 de referencia: LLM→primer audio p50 1,05 s; con precomputados debe ser
  mejor).
- **Registrar:** log por persona + vídeo frontal y lateral + batería consumida por ciclo.

### T6 · Audio a bordo (fase F4)

- **Propósito:** voz del robot sin equipo externo.
- **Procedimiento:** reproducir los WAV de Supertonic vía `PlayTts` (o dispositivo del PC2/PC3 si
  PlayTts no acepta WAV externos); medir latencia altavoz con palmada+micrófono o con el propio log
  del servicio; sincronizar con el gesto (reloj común).
- **Criterios:** audio inteligible a 3 m; lag audio-gesto ≤150 ms compensado; sin cortes en ráfagas
  de frases seguidas.
- **Registrar:** tabla de latencias en `docs/metrics/<fecha>/audio_bordo.md`.

### T7 · Ensayo general SIN PC externo (fase F7)

- **Propósito:** la definición de hecho: demo completa a bordo.
- **Procedimiento:** desconectar el cable develop0 del PC externo (o no conectarlo). Arrancar el
  contenedor en el PC2 (arranque automático si hay systemd/launcher). Ejecutar la demo completa
  reto 2 con 1 persona; repetir 2 veces.
- **Criterios:** 2/3 demos completas sin intervención del PC externo y sin caídas; el mando solo
  como seguridad (no usado); batería consumida anotada; `tegrastats` grabado completo (RAM ≤ config
  declarada, temperatura estable).
- **Registrar:** todo lo anterior + `x2-bench --deployment` regenerado con medidas del Orin →
  el veredicto «hoy: NO» pasa a «SÍ» con evidencia.


### T8 · (Opcional) LLM en línea en el Orin (fase F6)

- **Procedimiento:** levantar `compose.jetson.yml` (4,7 GB, config B) junto a la config A; medir
  tok/s (expectativa 35-40 vs 71,3 en x86), RAM pico conjunta y temperatura bajo 10 respuestas
  seguidas.
- **Criterios:** p50 de respuesta completa ≤2,5 s (proporcional a los 1,44 s de x86 al ~55% de
  velocidad); RAM conjunta ≤10,5 GB; sin thermal throttling que degrade >20% los tok/s.
- **Abortar si:** RAM conjunta >12 GB o temperatura en escalada → quedarse con config A
  (precomputada), que es la demo por defecto.

## 3. Problemas conocidos en el robot (y respuesta)

Heredados de docs/09 §6, más los específicos del despliegue:

| Síntoma | Causa / respuesta |
|---|---|
| `check`: no llega el estado del MC | En el PC2 el descubrimiento DDS es local: verificar que el contenedor usa `network_mode: host` y `RMW_IMPLEMENTATION=rmw_fastrtps_cpp`, y que los servicios AimDK están vivos |
| No arma: «reproductor no libre (2, '6001')» | La app o el agente de voz lanzó un gesto: esperar o cerrar la app |
| Se desarma: «el MC no sigue nuestra fuente» | Otra fuente con prioridad (mando 80, app 60): nuestra prioridad es 65; cerrar la app |
| `STAND_DEFAULT` rechazado con code=9 (`MotionBusy`) | Conocido (salida de URS con app conectada): reintentos 12 s; si sigue, mando/app. Nunca forzar |
| Brazos se hunden ~0,03 rad en reposo | Normal sin manos (PD sag): el error de seguimiento esperado es ~2× el del sim; umbrales holgados (0,12→hasta 0,3 en movimiento) |
| Lateral/giro no arrancan | Umbrales del MC: ≥0,5 m/s lateral y ≥0,6 rad/s giro desde parado |
| Sellos de tiempo rechazados o futuros | Reloj del robot 1,5-6,8 s adelantado: usar `stamp=0` en velocidad y brazos |
| Contenedor reinicia solo | Healthcheck de `check` fallando por MC ocupado: el healthcheck debe distinguir «MC ocupado» de «sin MC» |
| Orin se calienta con LLM+TTS | Config B en MAXN con ventilación verificada; si degrada, config A |

## 4. Datos mínimos por sesión de prueba

1. Fecha, personas (mando + apoyo), firmware MC (`lx2501 v1.1.4` o el que haya), manos instaladas
   sí/no.
2. Batería inicial/final y `%` por prueba.
3. Logs: `logs/real/*.jsonl` del bridge y de los programas; `tegrastats` si hay compute a bordo.
4. Vídeo: frontal y lateral de la mejor toma (y de cada caída, completa).
5. Resultado contra criterios: PASS/FAIL por criterio, con el número medido.
6. Copia a `docs/metrics/<fecha>/` con README índice (convención de
   `docs/metrics/robot_real_20261020/`).

Sin estos datos la prueba se repite: el objetivo del plan es cerrar el veredicto de despliegue con
evidencia medible, no con impresiones.
