# Reto 2 · Recepción HRI del N Club con la tubería HRIEGO (rama `claude/reto2-hri`)

Recepción completa de un huésped en el simulador MuJoCo propio (show engine): identificación →
caminata al punto de recepción → parada y asentamiento → 6 turnos HRIEGO precomputados (habla
Supertonic + gesto SONIC/preset/pointing alineado al ancla CTC) → ruta guiada según la categoría.
Todo es **reproducible desde un clon limpio**: los datos de visita están versionados en
`assets/hri/` (ver [PROVENANCE](../../assets/hri/PROVENANCE.md)); solo los CSV de animación del MC
de AgiBot (licencia del fabricante) y el módulo Face en vivo (datos personales) son externos.

Informe de validación: [`out_reto2/REPORT.md`](../../out_reto2/REPORT.md) · enunciado del reto:
[11-reto-2.md](../11-reto-2.md).

## (a) Qué hace y arquitectura

```
 huésped entra (sim)
      │
      ▼
 [identidad]  foto → Face (UniFace: RetinaFace+EdgeFace, opcional, en vivo)
      │        sin Face/foto → assets/hri/identities.json (persona→rango precomputado)
      ▼        o --rank VIP|REGULAR|GUEST|STAFF|EXTERNAL (sintético)
 persona → categoría del club → ruta y piso
      │
      ▼
 [locomoción] caminata al punto de recepción (5,2; 0,8) → PARAR → Settle (|cmd|<0,05 durante 0,8 s)
      │        interlock no negociable: NUNCA gestos mientras camina (doble compuerta motor+player)
      ▼
 [turnos HRIEGO ×6: GREETING · GUIDANCE · DIALOGUE×3 · FAREWELL]  (paquetes precomputados)
      │   FSM por turno (act): habla WAV Supertonic 44,1 kHz (reloj de audio = reloj de sim)
      │   ├ gesto base: SONIC co-speech (brazos 14 + waist/head 50 Hz, cuadro 100 = t0 de audio)
      │   ├ preset de catálogo: contrato Qwen3-4B Q4_K_M json_schema (type→speech→trigger),
      │   │   emote → (motion_id, area) → CSV AgiBot, reproducción CINEMÁTICA (sin preplay MC)
      │   ├ scheduler stroke-aligned: dispatch = ancla CTC (QuartzNet) − stroke_s (cronología
      │   │   cinemática c08) − TRACK_LAG_S (100 ms de retardo PD, feedforward); audio ≤ +1,5 s
      │   ├ overlay de señalamiento (POINT_LEFT/RIGHT/FRONT, point_targets + cabeza ±0,30 rad)
      │   ├ transiciones min-jerk (fade 0,6 s SONIC→preset, 0,7 s vuelta; 0,3 s entre turnos)
      │   ┗ waist como delta acotado ±[0,35 0,15 0,15] rad, solo parado; limiter 2,5 rad/s brazos
      ▼
 [despedida] brazos a reposo (gate _arms_at_rest) → POINT a la ruta → caminata (huésped sigue)
      │
      ▼
 registro 50 Hz (CSV) + eventos JSONL + resumen (caídas, gestos caminando, error stroke−ancla
 con el detector oficial c08/c12: primer cruce del 90 % del desplazamiento máx.) + MP4 (EGL)
```

Módulos: `src/x2_ultra/show/hri/` — `data.py` (tienda de solo lectura), `motion.py` (primitivas),
`player.py` (reproductor 50 Hz + interlock), `pointing.py`, `tour.py` (HriReceptionTour),
`face_id.py`/`face_probe.py` (identidad en vivo opcional + precomputada), `logger.py`,
`render.py` (headless EGL → MP4+audio), `cli.py` (punto de entrada). Edición mínima al motor:
`RobotRig.waist_delta` acotado y solo parado (`engine.py`).

## (b) Cómo ejecutar (clon limpio)

```bash
git clone <repo> && cd x2_ultra-reto2 && git checkout claude/reto2-hri
scripts/setup.sh                       # .venv + paquete x2_ultra + URDF/MJCF (python3 ≥3.10 del sistema)
scripts/fetch_hri_assets.sh            # CSV de animación del MC desde $X2_SIM_ROOT (default ~/robots/x2_simulation)
                                       #   → assets/hri/mc_classic/, verificado con sha256
.venv/bin/python -m pytest tests/test_hri_reception.py -q      # 17 tests ligeros

scripts/run_reto2_hri.sh --person Laura --video    # una persona → out_reto2/laura.mp4 (~30 s)
scripts/run_reto2_hri.sh --all --video             # las 6 personas + validation.json (~4 min)
scripts/run_reto2_hri.sh --person Laura            # sin vídeo: solo métricas (~10 s)
scripts/run_reto2_hri.sh --rank VIP --video        # rango sintético, sin identidad
# equivalencia directa: python -m x2_ultra.show.hri.cli --person Laura|--all [--video]
```

- Intérprete: `.venv` del repo; si ya tiene un venv con el paquete instalado,
  `X2_PYTHON=/ruta/venv/bin/python scripts/run_reto2_hri.sh …`. Sin rutas absolutas hardcodeadas.
- Headless: `MUJOCO_GL=egl` (el script lo fija). Un solo render EGL a la vez.
- Opcionales del sistema: `ffmpeg` (mezcla de audio del MP4; si falta, MP4 mudo), `espeak-ng`
  (frases del motor; si falta, solo subtítulos).
- **Salidas** en `out_reto2/`: `<persona>.mp4` (960×540 @25 fps h264+aac, HUD+subtítulos),
  `<Persona>_log.csv` (50 Hz), `<Persona>_events.jsonl`, `<Persona>_summary.json`,
  `validation.json` (con `--all`).
- **Datos**: `assets/hri/HRIEGO_gesture/` (versionado, espejo slim de la raíz HRIEGO). Overrides
  por entorno: `X2_HRIEGO_ROOT`, `X2_MC_CLASSIC`, `HRI_VISITS_ROOT` (raíz de visita por persona),
  `HRI_IDENTITIES`.

### Cara en vivo (opcional; la demo funciona sin ella)

Por privacidad/licencia no se versionan fotos ni galería. Sin `--photo` se usan las identidades
precomputadas (`assets/hri/identities.json`, persona→rango). Para reconocimiento en vivo:

1. checkout del módulo `Face` de la rama `Facial,-LLM-and-Gesture-integration` del repo `x2_ultra`;
2. su venv: `scripts/setup_hriego_v2.sh` (crea `Face/.venv-face`) o un venv propio con UniFace;
3. `X2_FACE_ROOT=/ruta/Face X2_FACE_PYTHON=/ruta/Face/.venv-face/bin/python` + galería institucional
   (SQLite) poblada; 4. `scripts/run_reto2_hri.sh --person Laura --photo ~/fotos/Laura --video`.

Umbrales del reconocedor: cos ≥ 0,45 y margen ≥ 0,08; cara desconocida no bloquea (categoría
`invitado`, identidad UNCONFIRMED).

### Regenerar los paquetes de visita

Con la tubería HRIEGO_gesture (rama `Facial,-LLM-and-Gesture-integration`): contrato LLM
(Qwen3-4B Q4_K_M, json_schema `rcp01c.intervention.v2`) → TTS Supertonic → StreamTalk+GMR+SONIC
(brazos/waist/head 50 Hz) → `align/anchor_plan.py` (anclas CTC QuartzNet). Después recopie el
subconjunto slim a `assets/hri/HRIEGO_gesture/` (visit_log, tts_manifest, wav sin `*_fsprobe`,
anchor_plan/, `sonic/<turn>/<turn>_arms.npz`) y regenere `assets/hri/HRIEGO_gesture.sha256`.

## (c) Resultados de validación (6/6)

De `out_reto2/REPORT.md` (2026-10-23, rama `claude/reto2-hri`, paquetes realnames; identidades
precomputadas = mismo resultado persona→rango que la sonda Face en vivo de la validación original):

Corrida con vídeo del 2026-10-23 (paquetes realnames regenerados por rango, §(i); filtro de waist
activo, §(h)):

| Persona | Estado | Categoría/Rango | Ruta/POINT | Caídas | Gestos caminando | stroke máx (ms) | tilt máx (°) | Sim (s) |
|---|---|---|---|---|---|---|---|---|
| Luis | KNOWN P001 | personal/STAFF | RIGHT (prestado) | 0 | 0 | 40 | 5,5 | 83,1 |
| Josaed | KNOWN P002 | externo/EXTERNAL | FRONT | 0 | 0 | 100 | 4,9 | 94,4 |
| Sebastian | KNOWN P003 | socio/REGULAR | LEFT | 0 | 0 | 20 | 5,0 | 114,8 |
| Mario | KNOWN P004 | invitado/GUEST | LEFT (prestado) | 0 | 0 | 40 | 4,9 | 112,4 |
| Laura | KNOWN P005 | vip/VIP | LEFT (prestado) | 0 | 0 | 20 | 4,9 | 127,8 |
| Desconocido | UNKNOWN | invitado (defecto) | LEFT (prestado) | 0 | 0 | 40 | 4,9 | 114,0 |

Criterios cumplidos: **stroke−ancla ≤ 100 ms** (máx. 100, mediana 20; detector oficial c08/c12),
**0 caídas** (pelvis > 0,5 m, tilt < 20°), **0 gestos caminando** (`gesture_while_walking = 0`,
`safety_gesture_suppressed = 0`), POINT = ruta real del club por categoría, 21/21 tests ligeros.
Tratamiento por rango verificado en el habla: 0 % de mezcla usted/tú (§(i)).
Paquetes de visita: realnames (`opt/realnames/visits/<persona>`, habla con nombres reales); los
tests de plan usan `visits_v2/<rank>`.

## (d) Estrategia de despliegue ONLINE (Jetson Orin NX 16 GB)

**Layout de procesos** (3 procesos + 1 servidor):

| Proceso | Contenido | Mem. medida |
|---|---|---|
| `brain` (`.venv-hri`) | UniFace (identidad) + StreamTalk ONNX (torch-free) + SONIC ONNX en CPU | 3,67 GB |
| `motor` 50 Hz | player/reproductor HRI + interlocks + limiter; política de locomoción ONNX | incluido abajo |
| motion server | servidor de movimiento online (primer comando ~0,44–0,55 s) | ~4,5 GB |
| `llama-server` | Qwen3-4B Q4_K_M **solo servidor**, ctx 16k, KV q8_0 | 4,7 GB (unificado) |
| TTS | Supertonic CPU + CTC QuartzNet (anclas) | 0,65 GB |

**Presupuesto de memoria**: ~10 GB de 16 GB (margen para SO/cachés). **Latencias medidas (x86)**:
LLM type 0,20 s · primera frase 0,34 s · TTS primera frase 0,62 s · LLM→primer audio **1,05 s p50 /
1,28 s p95** · audio+gesto ~1,8–1,9 s con motion server online (SONIC offline actual: 4,3 s/turno,
por eso la demo del reto 2 usa paquetes precomputados). El stroke debe caer sobre el ancla CTC con
≤100 ms: en online, el scheduler despacha el preset con `stroke_s + TRACK_LAG` de adelanto.

## (e) Estrategia de reducción de latencia y decisiones vitales

1. **Contrato B**: orden type→speech→trigger y constantes fuera del prompt → **−65 % tokens** de
   salida (el LLM emite primero lo que se habla; el gesto viaja en el mismo JSON validado por
   json_schema).
2. **Streaming por frase**: el TTS arranca con la primera frase (0,62 s), no con la respuesta
   completa; ventanas de StreamTalk + GMR a 15 Hz + SONIC online (primer comando ~0,44–0,55 s).
3. **Sim rápida**: `.mjb` precompilado + geometrías de colisión en casco convexo.
4. **StreamTalk ONNX torch-free** en el proceso brain (sin runtime PyTorch en el Jetson).
5. **`preplaying_tick 500` se MANTIENE** (1,0 s de preplay del MC): ponerlo a 0 hizo **caer el bow**
   (3001) en pruebas — el preplay es parte de la dinámica del preset vendor.
6. **Nunca gestos mientras camina** (interlock doble motor+player): en gemelo, caminar con brazos
   arriba cae 3/3; la secuencia caminar→parar→asentar→gestar es el único patrón validado.
7. **Waist acotado** (delta ±[0,35 0,15 0,15] rad, solo parado): el bow vendor pide +0,60 rad de
   waist_pitch (1,9× el límite URDF); la política nunca pierde el torso.
8. **Política de stroke `auto`**: si el preset no cabe antes del ancla, se retrasa el audio ≤1,5 s
   (política del anchor_plan) en vez de recortar el gesto.
9. **Interlock de caída**: pelvis/tilt → amortiguado; en real debe leer HAL a 500 Hz (el estado MC
   va a solo 10 Hz).

## (f) Gaps sim2real y plan para el robot real

Resumen de `HRIEGO_gesture/opt/sim2real/REPORT.md` (análisis 2026-10-23):

- **Velocidad de brazos URS 0,35 rad/s** (techo real) vs 2,5 rad/s del sim: los comandos de la
  validación lo exceden en 54–64 % de los ticks (~75 % del tiempo en movimiento). Rate-limitado a
  0,35: amplitud −0…58 %, stroke **+1,8…+2,9 s** tras el ancla. Plan: re-timar los presets con el
  MC (que sí los ejecuta a su velocidad) en vez de replay cinemático, o re-generar SONIC con
  limiter 0,35 y anclas recalculadas.
- **Cabeza sin canal en URS** (se ignora; HEAD_ONLY es exclusivo): el head-pitch del bow y el giro
  de cabeza del POINT no tienen canal real simultáneo con brazos. Plan: omitir cabeza o usar
  McBodyPose (sin probar en robot).
- **Salida de URS por SDK no conseguida** (MotionBusy code=9 pidiendo STAND_DEFAULT desde URS);
  además SD snapping devuelve brazos al default a ~10 rad/s con ringing 0,5 s. Plan: transiciones
  lentas explícitas y nunca salir de URS con brazos en alto.
- **Presets vendor SÍ existen en el robot** (29 confirmados, incl. bow 3001): en real se usaría
  `SetMcPresetMotion` con su preplay de 1,0 s (el sim usa replay cinemático sin preplay) → el
  offset de stroke cambia: **recalibrar el lag audio–gesto en el robot** (TRACK_LAG_S=0,10 s es de
  este rig/gains a 50 Hz; reloj del robot 1,5–6,8 s adelantado → stamp=0; latencia del altavoz sin
  medir).
- **Marcha con brazos arriba: no validada en real** (gemelo MC v1.0.0 cae 3/3). Mantener la
  secuencia caminar→parar→gestar; umbrales reales de arranque lateral ≥0,5 m/s y giro ≥0,6 rad/s.
- Sin manos en el robot (hand_sub_mode=1): masa de punta distinta (−0,45 kg/lado); PD real con sag
  ~0,03 rad → error de seguimiento ~2× el del sim.

## (g) Limitaciones conocidas

1. Habla precomputada con temática de laboratorio: en `visits_v2` los nombres saludados son los de
   los perfiles de la galería y el GUIDANCE prestado habla de «sala de juntas/laboratorio»; los
   paquetes realnames sí usan nombres reales. Nada se genera en línea (requisito del reto).
2. `TRACK_LAG_S` (100 ms) calibrado para este rig/gains a 50 Hz; si cambian, re-medir con
   `--no-video` (error de stroke en el resumen).
3. Sin Face en vivo, la categoría viene de `identities.json`/`--rank`: no hay demostración del
   reconocimiento en sí (la sonda en vivo tardó 2,6–2,7 s/foto en la validación original).
4. GUIDANCE prestado cuando la dirección hablada del rango no coincide con la ruta del club
   (marcado `borrowed`); la coherencia habla-gesto manda sobre la procedencia del turno.
5. `espeak-ng`/`ffmpeg` opcionales: sin ellos, MP4 mudo o sin frases del motor (solo subtítulos).
6. Fallos preexistentes en `tests/test_engine.py` (builder `mimic` vs mujoco 3.17), ajenos a esta
   rama; los 21 tests de `tests/test_hri_reception.py` pasan.
7. Un solo render EGL simultáneo; `--all` tarda ~3–4 min con vídeo en este PC (22–35 s por persona).
8. **Reacciones de piernas**: resueltas en §(h2) (bounds de waist ±0,10/±0,05/±0,03 + brazos SONIC
   al 70 % de amplitud): legs qvel RMS parado 0,030–0,052 (−82…−90 % vs pre-W1). Residuo: momentum
   de brazos no filtrable sin k menor; a largo plazo, reentrenar la política con perturbaciones de
   torso.

## (h) Canal de waist filtrado + venia estable (W1, 2026-10-23)

El delta de waist crudo de SONIC/presets movía las piernas por acople de política (reacciones de
balance) y la venia VIP (preset 3001) pedía +0,60 rad de pitch, 1,9× el límite URDF: el robot se
desplazaba medio metro hacia atrás. `engine.py` añade `WaistFilter` entre `set_waist_delta` y
`control_tick`; `player.py` gestiona la compuerta por turno:

1. **Bounds por eje** (yaw ±0,10 / pitch ±0,05 / roll ±0,03 rad, ver §(h2)) → **low-pass** 2º orden
   críticamente amortiguado (fc = 1,5 Hz) → **slew** (0,4 / 0,2 / 0,2 rad/s) → ganancia `g`.
2. **Ganancia g(t)** = gate por turno (rampa min-jerk 0,4 s: abre al entrar el turno, cierra al fin
   de secuencia, en supresión por caminata y en `abort_to_rest`) × **factor de equilibrio** (1→0 con
   tilt 3→6° o margen COM→polígono de soporte <30 mm, solo parado y con delta activo).
3. **Exceso de yaw repartido** a `shoulder_yaw` (±0,10 rad) y `head_yaw` (±0,15, solo Ultra) con
   slew propio, para no perder intención comunicativa al recortar el torso.
4. **Venia suave**: `set_waist_gentle(True)` en la ventana CSV del 3001 → cap pitch 0,10 rad y
   slew 0,06 rad/s; cabeza y brazos del CSV intactos. Marcha intacta (mismo interlock |cmd| < 0,05).

Antes → después (atribución D1, replay `rn_luis_t02_dialogue`; venia D2, `diag_bow.py`):

| Métrica | Antes | Después |
|---|---|---|
| drift x de la venia (m) | −0,570 | **+0,002** |
| margen COM→talón mín (m) | 0,008 | **0,053** (≥0,040) |
| tilt máx venia (°) | 5,2 | 3,0 |
| legs qvel RMS parado | 0,297 | 0,259 (−13 %) |
| tilt máx diálogo (°) | 4,23 | 3,29 |
| drift xy fin de turno (m) | 0,199 | 0,109 (−45 %) |

Detalle y metodología en `out_diag/W1_REPORT.md`; 4 tests nuevos en `tests/test_hri_reception.py`
(`WaistFilter` puro: bounds+slew+exceso, gentle, ganancia+reset, gate del player).

## (h2) Fix de reacciones de piernas (legs2, c_b10k07, 2026-10-23)

Experimento determinista de 7 condiciones × 3 turnos SONIC parados (`out_diag/legs2/REPORT.md`)
sobre el código W1: bounds de waist más bajos y escalado de brazos atacan causas **distintas** y
componen bien. Recomendación aplicada **c_b10k07**, dos constantes configurables:

1. `WAIST_BOUNDS = [0.10, 0.05, 0.03]` rad (yaw/pitch/roll) en `engine.py` — antes [0,18/0,08/0,05].
   El exceso de yaw sigue repartido a hombros/cabeza (§(h).3), no se pierde intención.
2. `SONIC_ARM_AMP_K = 0.7` en `motion.py`, aplicado en `player._base`: brazos SONIC
   `cmd = REST + 0,7·(cmd − REST)` alrededor del reposo. **No** aplica a presets CSV (venia/emote)
   ni a pointing (amplitud plena).

Antes → después (replay legs2, 3 turnos dialogue parados; 0,297 = baseline pre-W1):

| Métrica | W1 (antes) | c_b10k07 (después) |
|---|---|---|
| legs qvel RMS parado | 0,165–0,270 | **0,030–0,052** (−82…−90 % vs 0,297) |
| tilt máx (°) | 3,16–3,75 | **≤ 2,54** |
| drift xy máx (m) | 0,075–0,158 | **≤ 0,037** |
| arm RMS displacement | 100 % | **70 %** (gestos visibles) |
| corr waist vs SONIC LP1,5 | ≥0,81 | **≥ 0,89** |

1 test nuevo (`test_sonic_arm_amplitude_scaling`); validación completa de las 6 personas con vídeo
en `out_reto2/REPORT.md`.

## (i) Personas por rango + TTS por rango (W3, 2026-10-23)

Las 6 visitas de validación se regeneraron con una **persona de estilo por rango** (tarjeta +
few-shots inyectados al LLM) y **prosodia Supertonic por rango×estado**; los paquetes reemplazaron
in-place `assets/hri/HRIEGO_gesture/opt/realnames/visits/<persona>/` (sha256 y PROVENANCE
regenerados; fuente: `opt/personas/` del repo HRIEGO_gesture, `REPORT.md` allí):

| Persona | Rango | Persona de estilo | Tratamiento | Voz/velocidad |
|---|---|---|---|---|
| Laura | VIP | mayordomo inglés | usted + título, 0 exclamaciones | M5 (grave), 0,92–0,97 |
| Sebastian | REGULAR | anfitrión de club | usted cálido | M1, ~1,05 |
| Mario / Desconocido | GUEST | concierge paso a paso | usted | M1, ~1,02 |
| Luis | STAFF | colega de confianza | **tú exclusivo** + humor | M1, ~1,10 |
| Josaed | EXTERNAL | mostrador operativo | usted breve | M1, ~1,05 |

Resultado: **0 % de turnos y 0/6 visitas con mezcla usted+tú** (antes 84 % de visitas), VIP con
título en saludo/despedida y 0 exclamaciones, STAFF solo tú; contrato HRIEGO ALL_OK en las 6
(saludo de rango, «¡dame esos cinco!», POINT=ruta, heart VIP). e2e petición→primer audio p50 1,23 s
/ p95 1,57 s. Pendiente: la voz M5 del VIP es masculina grave y Laura es mujer (coherencia de
género sin evaluar por tiempo).
