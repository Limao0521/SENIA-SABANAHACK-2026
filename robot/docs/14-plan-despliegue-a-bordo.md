# 14 · Plan de desarrollo: todo el sistema a bordo, contenerizado

> **Meta:** un contenedor Docker (imagen aarch64 + `docker compose`) que despliegue **todo el
> sistema dentro del robot** —en el PC2 (Jetson Orin NX 16 GB)— sin depender de un PC externo para
> el procesamiento. La demo del reto 2 (recepción guiada HRI) y el show del reto 1 deben poder
> ejecutarse a bordo con el cable desconectado.
>
> **⏱️ MODO HACKATHON: el presupuesto real son 16 horas.** La sección 2 es EL plan a ejecutar
> (recortes y schedule hora a hora); las secciones 3-6 quedan como referencia del plan completo.

Punto de partida: el análisis de viabilidad (`x2-bench --deployment`,
`runs/deployment_reto2hri/deployment.md`, resumen en [docs/13](13-registro-benchmark-despliegue.md) §7).
**Por presupuesto SÍ cabe; hoy NO corre a bordo.** Este plan cierra esa distancia. La ejecución de
cada fase en el robot sigue la [guía de pruebas físicas](15-guia-pruebas-fisicas.md).

## 0. Estado y restricciones

**Lo que ya existe (y funciona):**

- Cliente AimDK (ROS 2/rclpy) probado **en el robot real** (19-20/10/2026): locomoción
  (`/aima/hc/locomotion`, 5 sesiones sin caídas) y brazos URS 50 Hz (`/mc/upper_body_command`,
  error medio 0,064 rad) — `src/x2_ultra/real/`, [docs/09](09-robot-real.md).
- Player HRI validado 6/6 personas en gemelo (stroke−ancla ≤100 ms, 0 caídas, 0 gestos caminando) —
  `src/x2_ultra/show/hri/`, `docs/reto-2/README_HRI.md`.
- Paquetes HRIEGO **precomputados y versionados** en `assets/hri/` (+ identidades en
  `identities.json`): la demo no necesita Face ni generación en línea.
- SDK AimDK descargable para **aarch64** (`official_sim/fetch_assets.sh`) y overlay compilable para
  arm64 (`scripts/build_aimdk_overlay.sh`).
- Perfil Jetson del LLM ya escrito: `HRIEGO_gesture/llm/compose.jetson.yml` (arm64
  `dustynv/llama_cpp` r36.4.0, ctx 16k, KV q8_0 → 4,7 GB unificados). **Nunca desplegado.**

**Restricciones duras:**

| Restricción | Consecuencia para el plan |
|---|---|
| PC1 (RK3588s) es vendor: prohibido código propio | La locomoción y el MC no se tocan; solo se les habla por DDS |
| El FastDDS del robot solo escucha en develop0 (10.0.1.41) | El contenedor debe correr **en el PC2** (allí el descubrimiento es local); por Wi-Fi/cable no hay discovery |
| PC2: Orin NX 16 GB unificados, 8×A78AE, modos 10-25 W | Presupuesto de RAM por configuraciones (sección 1); modo de potencia MAXN para las primeras pruebas |
| Techo URS real 0,35 rad/s (sim: 2,5) | Re-timing obligatorio del pack comercial y de los gestos SONIC (F3) |
| Salida de URS por SDK no conseguida (`MotionBusy code=9`) | Protocolo de salida explícito + fallback al mando (F5) |
| Sin manos (`hand_sub_mode=1`), sag ~0,03 rad, error ~2× | Umbrales de seguimiento holgados; validación visual |
| MC v1.1.4 (robot) vs v1.0.0 (gemelo); reloj del robot 1,5-6,8 s adelantado | Recalibrar lags en el robot (F3/F4); usar `stamp=0` |

**Incertidumbre principal:** qué se puede ejecutar en el PC2 (¿docker propio? ¿acceso al contenedor
AimDK? ¿root/ssh?). **F0 lo verifica antes de comprometer la arquitectura.** Plan B documentado en
la sección 4.

## 1. Arquitectura objetivo

```
┌─ PC1 · RK3588s (VENDOR, no se toca) ─────────────────────────────┐
│  MC lx2501 v1.1.4: locomoción 50 Hz, URS, presets, HAL 500 Hz    │
└──────────────▲───────────────────────────────────────────────────┘
               │ DDS (develop0, descubrimiento local)
┌─ PC2 · Jetson Orin NX 16 GB ─────▼───────────────────────────────┐
│  contenedor x2-onboard (docker compose, network host)            │
│  ├─ onboard      : puente player/show → real (50 Hz)   ~0,5 GB   │
│  │   (x2_ultra.real + x2_ultra.show.hri.player, interlocks,      │
│  │    limiter 0,35 rad/s, fallbacks al mando)                   │
│  ├─ tts-ctc      : Supertonic + QuartzNet (CPU)         0,65 GB  │
│  ├─ brain        : paquetes HRIEGO precomputados        3,67 GB  │
│  ├─ [opc] llama  : Qwen3-4B Q4_K_M (compose.jetson)     4,7 GB   │
│  └─ [opc] face   : UniFace arm64                        ~2,3 GB  │
│  Audio → AimDK PlayTts (servicio del robot) → altavoces/PC3      │
└──────────────────────────────────────────────────────────────────┘
```

Config A (demo hackathon, precomputada): **4,82 GB = 30%** de la RAM · Config B (+LLM): **9,52 GB
= 59%** · Config C (+motion server en vivo): 14,02 GB = 88%, JUSTO — fuera del alcance de este plan
(la demo usa gestos precomputados). El gemelo MuJoCo **no** se despliega a bordo (por diseño; su
papel es la validación previa en el PC de desarrollo).

## 2. Plan hackathon: 16 horas (la vía rápida)

**Presupuesto:** 16 h de reloj, dos pistas en paralelo. **Config A obligatoria** (todo precomputado).
Lo que no esté en esta sección, no se hace durante la hackathon.

### 2.1 Recortes de alcance (decisiones tomadas para caber en 16 h)

| Se corta | En su lugar |
|---|---|
| F6 completo (Face arm64, LLM en línea) | Identidades precomputadas (`assets/hri/identities.json`) y frases fijas — ya validado 6/6 |
| Regenerar gestos SONIC con limiter + anclas recalculadas (F3 completo) | **Presets del MC** (`SetMcPresetMotion`, 29 confirmados) para los gestos clave + rate-limiter 0,35 rad/s para lo que quede de SONIC; `TRACK_LAG_S` ajustado a ojo en el robot |
| Bench completo en el Orin (F0/T1) | Oportunista: 30 min SOLO si sobra tiempo al final (`realtime_50hz` + `policy_inference`); no bloquea nada |
| Cabeza en los gestos (gap 2: sin canal URS simultáneo) | Omitida en la v1 a bordo (decisión ya tomada en F3) |
| Calibración fina de audio (reloj común) | Lag fijo medido una vez en T6-lite y compensado con una constante |
| Show comercial del reto 1 a bordo | Fuera: la demo a bordo es la recepción del reto 2 |

### 2.2 Schedule hora a hora (H = horas desde el inicio)

| Ventana | Pista A (PC/contenedor) | Pista B (robot/físico) |
|---|---|---|
| **H0-H1** | **F0:** acceso al PC2 (ssh, ¿docker?, ¿compose?, RAM libre, `nvpmodel` MAXN). **GO/NO-GO H1:** sin docker accesible → Plan B (sección 4) o fallback F2 (sección 2.4) | Robot disponible, batería ≥80%, mando y parada asignados, espacio reservado |
| **H0-H4** | **F1:** contenedor `x2-onboard` (implementación ya lanzada en `deploy/`: Dockerfile.aarch64, compose.onboard.yml, build + onboard.sh) — copia al Orin y **build nativo allí** (~1 h) | Pruebas de mesa: repasar protocolo [guía 15](15-guia-pruebas-fisicas.md) T2, preparar logs/metrics |
| **H4-H5** | **T0:** `onboard.sh check` en verde desde el contenedor en el PC2, **sin cable al PC externo** | Testigo de T0; si falla: troubleshooting DDS (`network_mode: host`, fastrtps) |
| **H5-H9** | **F2-lite:** puente player→robot mínimo: reutilizar handoff de `real/arms.py` + `real/walk.py`; interlock caminar→parar→asentar→gestar portado; `--dry-run` primero (log sin publicar) | **F5-lite (30 min):** protocolo de salida de URS ensayado con `brazos` actual: brazos a postura → `STAND_DEFAULT` con reintentos → fallback mando (el `MotionBusy code=9` NO se resuelve en la hackathon, se gestiona) |
| **H8-H10** | Corregir lo que T2 destape | **T2:** brazos desde el contenedor: dry-run → un eje → envolvente a 0,15 y 0,35 rad/s → 5/10 salidas limpias (criterio relajado: 5/10, no 10/10) |
| **H10-H12** | **F3-lite:** rate-limiter 0,35 en el puente + presets MC para saludo/bow/POINT; medir preplay real (1,0 s) y ajustar `TRACK_LAG_S` | **T4-lite:** 2 presets + 1 gesto SONIC limitado en el robot; tabla de lags mínima |
| **H12-H13** | **F4-lite:** WAV de la demo vía `PlayTts`; medir lag una vez, compensar con constante | **T6-lite:** audio audible a 3 m, lag ≤250 ms (relajado; 150 ms era el criterio completo) |
| **H13-H15** | Soporte/fixes | **T3+T5 integrados:** 2 ciclos HRI completos con personas (identidad precomputada → bienvenida → guía con interlock → gesto preset → bow + POINT de despedida), 0 caídas |
| **H15-H16** | **Congelación:** nada de código nuevo tras H15 | **T7:** ensayo general con el cable desconectado ×1 (no ×2); grabación de evidencia (vídeo, logs, batería); cierre |

### 2.3 Suelo mínimo viable (lo que HAY que tener a H16)

Recepción del reto 2 a bordo del PC2, sin PC externo: el contenedor corre el player con paquetes
precomputados, brazos por URS con limiter 0,35 rad/s **o** presets del MC, locomoción por el MC
vendor, audio por el robot, 1 ciclo completo con persona sin caídas, evidencia grabada.

### 2.4 Fallbacks (en orden, se decide en los GO/NO-GO)

1. **H1 sin docker en PC2** → Plan B (sección 4): bundle dentro del contenedor AimDK (misma lógica,
   otro empaquetado; +1-2 h).
2. **H10 sin puente de brazos fiable** → demo a bordo **solo con presets del MC** (saludo, bow,
   POINT, gestos de los 29 confirmados): sin stream URS, el player dispara servicios en vez de
   comandos 50 Hz. Pierde fluidez SONIC, gana robustez.
3. **H13 sin contenedor estable** → demo **desde el PC externo por cable** (estado actual ya
   validado 6/6 en gemelo y probado en robot el 19-20/10): el objetivo de la hackathon se degrada a
   «demo funciona» y el despliegue a bordo queda documentado como trabajo en curso con el contenedor
   construido y T0/T2 pasados.

### 2.5 Reglas de la hackathon

- Cada GO/NO-GO se decide a la hora marcada con lo que haya, no se aplaza.
- Todo test físico sigue las reglas de seguridad de la [guía 15](15-guia-pruebas-fisicas.md) §1 —
  sin excepciones por falta de tiempo (2 personas, mando, batería ≥50%).
- Sin log no cuenta (guía 15 §4); la evidencia de T7 es el entregable de la hackathon.
- H15 = congelación: lo que no está verde a H15 no se toca; se ejecuta el fallback que corresponda.

## 3. Fases (plan completo — referencia)

Estimaciones en días-persona; cada fase termina con su criterio de aceptación y sus datos
registrados ([guía 15](15-guia-pruebas-fisicas.md)). F0-F5+F7 son el **camino mínimo**; F6 es
opcional.

### F0 · Acceso y línea base en el Orin — 0,5-1 día

| | |
|---|---|
| Tareas | 1) Acceso al PC2 (ssh/app del fabricante): confirmar usuario, docker/compose disponibles, versión L4T/JetPack, RAM libre, `nvpmodel`/`tegrastats`. 2) Copiar el repo y correr `x2-bench` **en el Orin** (`--cores 8`, `MUJOCO_GL=egl`, `--no-gpu` si no hay `nvidia-smi`; alternativa: muestrear `tegrastats`). 3) Fijar modo de potencia MAXN para pruebas. |
| Entregables | `runs/bench_orin_*/report.{md,json}` · nota de acceso PC2 en docs/09. |
| Aceptación | Informe del bench **medido en el Orin**: sustituye la banda ×1,8-2,2 por factores reales. Si el bench de física satura (esperable: el gemelo no va a bordo), valen `policy_inference`, `realtime_50hz` y `club_tours` como cotas. |
| Riesgo | Sin docker ni root en PC2 → **Plan B** (sección 4). |

### F1 · Contenedor aarch64 `x2-onboard` — 1-2 días

| | |
|---|---|
| Tareas | 1) `deploy/Dockerfile.aarch64` multi-stage: stage *build* (SDK AimDK aarch64 + overlay arm64 con `McBodyPose`), stage *runtime* (base `ros:humble` arm64 o `nvcr.io/nvidia/l4t-base`, python 3.10+, `x2_ultra` instalado, `assets/hri/`). 2) `deploy/compose.onboard.yml`: `network_mode: host`, repo/assets en volumen ro, `logs/` rw, restart policy, límites de memoria por servicio según config A. 3) `deploy/build_aarch64.sh` (build nativo en el Orin o cross con buildx). 4) Entrada `python -m x2_ultra.real.check` como healthcheck de solo lectura. |
| Entregables | `deploy/` (Dockerfile, compose, build, README de despliegue) · imagen versionada `x2-onboard:<tag>`. |
| Aceptación | **T0 de la guía 15**: `check` en verde **desde el contenedor en el PC2** (modo, fuente, batería, reloj, si se puede armar) sin PC externo. |
| Riesgo | Paquetes sin rueda arm64 (onnxruntime tiene; torch solo si F6) → el runtime mínimo no lleva torch. |

### F2 · Puente player → robot — 2-3 días

El gap central: que la salida del player HRI (y del show del reto 1) alimente los topics reales en
vez del `ctrl` de MuJoCo.

| | |
|---|---|
| Tareas | 1) Módulo `x2_ultra.real.onboard`: consume la trayectoria del player (brazos L7/R7 @50 Hz + waist_head) y la publica por `/mc/upper_body_command` reutilizando el handoff **ya probado** de `real/arms.py` (registro de fuente prioridad 65, `hand_sub_mode=1`, envolvente, congelación por seguimiento); la locomoción guiada va por el camino de `real/walk.py` (`/aima/hc/locomotion`, rampas 0,5 m/s²·1 rad/s²). 2) Interlock de caída leyendo **HAL a 500 Hz** (el estado del MC va a solo 10 Hz — gap 7). 3) Interlock doble motor+player (caminar→parar→asentar→gestar) portado del gemelo. 4) Modo `--dry-run` (log sin publicar) y `--gemelo` (regresión contra el twin antes de tocar el robot). |
| Entregables | `src/x2_ultra/real/onboard.py` + tests sin ROS (estilo `tests/test_real_control.py`) + flag `--real` en el player. |
| Aceptación | En gemelo: 1 turno HRI completo end-to-end vía puente, 0 caídas, stroke−ancla ≤100 ms. En robot: **T2 y T3 de la guía 15** (stream de brazos y marcha+gesto con interlock). |
| Riesgo | Arbitraje de fuentes: el mando (80) y la app (60) pueden tomar el control; el bridge debe desarmarse como `walk.py`/`arms.py` ya hacen. Tren superior sin arbitraje → exigir cero publicadores antes de armar. |

### F3 · Re-timing al techo real (0,35 rad/s) y MC v1.1.4 — 2-3 días

| | |
|---|---|
| Tareas | 1) Rate-limiter 0,35 rad/s activo en el puente (los comandos validados exceden el techo en 54-64% de los ticks). 2) Regenerar los gestos SONIC del pack con limiter 0,35 **y anclas CTC recalculadas** (rate-limitar sin más cuesta hasta −58% de amplitud y +1,8…+2,9 s de stroke), o sustituir por presets del MC donde el gesto lo permita (los presets sí se ejecutan a su velocidad, con preplay de 1,0 s). 3) Recalibrar `TRACK_LAG_S` (100 ms en gemelo) y el lag audio-gesto **en el robot** con MC v1.1.4. 4) Sellos de tiempo `stamp=0` (reloj del robot adelantado 1,5-6,8 s). |
| Entregables | `assets/hri/` regenerado con PROVENANCE actualizado · tabla de lags medidos en robot (`docs/metrics/<fecha>/`). |
| Aceptación | **T4 de la guía 15**: en el robot, stroke−ancla ≤150 ms medido (relajado frente a los 100 ms del sim por el techo real), amplitud ≥70% de la diseñada, sin congelaciones por seguimiento. |
| Riesgo | Cabeza sin canal URS simultáneo (gap 2): el head-pitch del bow y el giro POINT no tienen canal real → omitir cabeza o usar `McBodyPose` (documentado pero sin probar en robot). Decisión: **omitir cabeza en la v1 a bordo**. |

### F4 · Audio a bordo — 1 día

| | |
|---|---|
| Tareas | 1) Reproducir los WAV de Supertonic por el servicio AimDK `PlayTts` (docs/03 §9; documentado, sin medir con nuestros WAV) o, si no acepta WAV externos, por el dispositivo de audio del PC2/PC3. 2) Medir latencia de altavoz y establecer reloj común gesto-audio. 3) Cola de frases del FSM de 6 turnos conectada al player. |
| Entregables | Módulo de audio en `real/onboard.py` · latencia medida en `docs/metrics/`. |
| Aceptación | **T6 de la guía 15**: audio audible desde el robot, lag audio-gesto medido y compensado ≤150 ms. |

### F5 · Salida limpia de URS — 1 día (punto parcialmente abierto)

| | |
|---|---|
| Tareas | 1) Secuencia de salida: gestos terminados → brazos a postura inicial lenta (transiciones explícitas, nunca salir con brazos arriba: `STAND_DEFAULT` los devuelve a ~10 rad/s con ringing 0,5 s) → reintentos de `STAND_DEFAULT` (el code=9 `MotionBusy` aparece con la app conectada) → fallback documentado al mando/app. 2) Integrarlo como apagado ordenado del contenedor (trap SIGTERM). |
| Aceptación | **T2c de la guía 15**: 10/10 salidas limpias (robot de pie, brazos abajo, fuente borrada). |

### F6 · Opcionales: Face arm64 y LLM en línea — 2-4 días

| | |
|---|---|
| Face | Build arm64 (torch Jetson wheels), cámara del robot o USB en el Orin; medir FPS/RAM. **La demo funciona sin Face** (identidades precomputadas persona→rango, ya validadas). Solo si el jurado pide reconocimiento en vivo. |
| LLM | Desplegar `compose.jetson.yml` en el PC2 (4,7 GB, config B) y medir tok/s reales (expectativa documentada 35-40 vs 71,3 en x86). Bienvenidas generadas en línea en vez de precomputadas. **T8 de la guía 15.** |

### F7 · Ensayo general y cierre — 1 día

Demo completa reto 2 (y show reto 1 si procede) **con el cable desconectado**: persona llega →
Face/identidad → bienvenida → guía → diálogo → despedida. Registrar todo (métricas, vídeo,
batería). Actualizar `x2-bench --deployment` con las medidas del Orin y cerrar el veredicto «hoy»
de NO a SÍ en `runs/deployment_*/deployment.md`. **Definición de hecho del plan.**

## 4. Plan B: si el PC2 no admite docker propio

La documentación del robot dice que el código propio va **en el contenedor AimDK del PC2**. Si F0
descubre que no hay docker accesible ni root:

1. Empaquetar el mismo contenido como **bundle desplegable dentro del contenedor AimDK** (script de
   instalación + venv arm64 + overlay compilado in situ): la «contenerización» pasa a ser la imagen
   del fabricante y nuestro sistema un proceso supervisado (`systemd`/`tmux`/launcher) dentro.
2. El compose queda para el PC de desarrollo y para F6-LLM (que sí assume docker Jetson).
3. Todo lo demás del plan (puente, re-timing, audio, pruebas) es independiente del empaquetado.

## 5. Riesgos globales

| Riesgo | Mitigación |
|---|---|
| Caída durante pruebas con brazos en alto | Interlocks ya implementados (marcha→parar→asentar→gestar); marcha con brazos arriba **prohibida** (gemelo cae 3/3); mando prioridad 80 siempre a mano |
| Batería en sesiones largas | Sesiones con ≥50%; el bridge se desarma <20% (como `walk.py`); la demo completa dura minutos |
| Térmica del Orin (LLM+TTS sostenidos) | MAXN con ventilación verificada; `tegrastats` grabado en T8; config A (sin LLM) es el plan por defecto |
| Gestos de la app/voz del fabricante interrumpiendo (6001/1011/3038 observados) | El bridge se desarma y reancla como los programas de docs/09; app cerrada durante pruebas |
| Divergencia gemelo↔robot (MC v1.1.4) | Toda recalibración (F3/F4) se mide en el robot; el gemelo queda como regresión previa, nunca como validez final |

## 6. Resumen de esfuerzo

| Camino | Fases | Estimación |
|---|---|---|
| **Hackathon (lo que se ejecuta)** | F0+F1+F2-lite+F3-lite+F4-lite+F5-lite+F7-lite (sección 2) | **16 h de reloj, 2 pistas** |
| Mínimo completo (demo a bordo) | F0+F1+F2+F3+F4+F5+F7 | 8-12 días-persona |
| Con opcionales | + F6 (Face, LLM) | 12-17 días-persona |

El modo hackathon comprime 8-12 días a 16 h recortando alcance (sección 2.1), relajando criterios
de aceptación (T2: 5/10 salidas; T6: lag ≤250 ms; T7: un ensayo) y apoyándose en los fallbacks de
la sección 2.4 — el suelo es la demo que YA funciona desde el PC externo. Tras la hackathon, lo que
quede del plan completo (F3 fino, F5 robusto, F6, bench en Orin) se retoma con los criterios de las
secciones 3 y la guía 15 sin relajar.

Orden de ejecución = orden de las fases; F3 y F4 pueden solaparse con F2 a partir del primer
stream de brazos exitoso. Cada fase física sigue el protocolo de seguridad de la
[guía 15](15-guia-pruebas-fisicas.md) (dos personas, mando, parada inalámbrica).
