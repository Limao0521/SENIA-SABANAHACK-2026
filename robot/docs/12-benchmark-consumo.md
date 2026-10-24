# 12 · Benchmark de consumo: el X2 Ultra emulado en el PC

`x2-bench` (paquete `src/x2_ultra/bench/`) emula en un PC x86-64 el procesamiento que el robot
reparte entre sus ordenadores y mide **latencia, rendimiento y consumo** de cada pieza de los dos
retos ([10](10-reto-1.md), [11](11-reto-2.md)). Nació en la rama `feat/consumption-benchmark`
(salida de `sabanahack-1.0`).

## 1. Qué se emula y contra qué se compara

| Pieza del benchmark | En el robot real | Dónde corre allí |
|---|---|---|
| Lazo de control 50 Hz + policy ONNX | MC / locomoción RL | **PC1** RK3588s (prohibido código propio) |
| Física MuJoCo 500 Hz | no existe (el robot no se simula a sí mismo) | — (carga añadida del gemelo digital) |
| Recepción guiada, puertas, huésped | aplicación propia del reto 2 | **PC2** Jetson Orin NX 16 GB |
| Render de la vista / vídeo de pantalla | pantalla e interacción | **PC3** RK3588 |
| Face + LLM + TTS/gestos (HRI, `--hri`) | HRIEGO v2 | **PC2** (no emulable en Windows: V4L2, `select` sobre pipes, docker x86-cuda) |

El PC de desarrollo mide **todo en un solo proceso x86**: es una cota superior conservadora por
núcleo frente al reparto real. Las latencias se extrapolan al Orin NX con factores documentados
(sección 4).

## 2. Uso

```bash
# Windows (venv propio, rasterizador nativo wgl)
uv venv .venv-bench --python 3.12
uv pip install --python .venv-bench\Scripts\python.exe -e ".[dev,bench]"
$env:MUJOCO_GL='wgl'; .venv-bench\Scripts\python.exe -m x2_ultra.bench --list
.venv-bench\Scripts\python.exe -m x2_ultra.bench --quick            # vuelta de reconocimiento
.venv-bench\Scripts\python.exe -m x2_ultra.bench                    # completa (~5-10 min)

# Linux / el Orin del robot
.venv/bin/python -m x2_ultra.bench --cores 8                        # afinidad a 8 núcleos como el Orin
```

Opciones: `--only k1,k2` (escenarios), `--scale N` (duraciones ×N), `--realtime-s S`,
`--cores N` (afinidad de CPU), `--p-idle/--p-tdp W` (constantes del modelo de consumo),
`--no-gpu`, `--output DIR`, `--deployment [REPORT_JSON]` (análisis de viabilidad de despliegue
a bordo del robot sin correr escenarios: elemento→ordenador, presupuesto de RAM de PC2, latencias,
gaps sim2real y checklist «sin PC externo»; cita el report.json indicado o el más reciente).
Requisitos: `third_party/agibot_x2_urdf` (scripts/setup.sh),
`assets/motions/` (`scripts/fetch_linkcraft_dance.py --all-requested`) para el baile, y
`docs/media/demo.mp4` para el vídeo.

## 3. Escenarios y métricas

| Escenario | Reto | Qué mide |
|---|---|---|
| `policy_inference` | componente | µs por inferencia ONNX de las dos policies (CPUExecutionProvider) |
| `physics_single` | componente | µs por `mj_step` (2 ms simulados) de un robot aislado |
| `stage_walk` | 1 | ciclo de control completo a 50 Hz con los dos robots andando (círculos paralelos en la tarima) |
| `stage_dance` | 1 | el ciclo con los dos robots bailando Tai Chi Chen (reproducción cinemática) |
| `stage_show` | 1 | show comercial completo: 19 parlamentos, brazos a 50 Hz desde el pack HRIEGO |
| `realtime_50hz` | 1 | **jitter real** del hilo en tiempo real: periodo entre ciclos contra los 20 ms |
| `render_offscreen` | 1 | render 720p de la cámara del público (lo que cuesta la vista de la demo) |
| `video_decode` | 1 | decodificación ffmpeg 720p→rgb24 del vídeo de la pantalla LED |
| `club_tours` | 2 | recepción guiada de las cinco categorías: ruta, puertas, frases y vuelta |

Por escenario se guarda: latencia del ciclo con **p50/p95/p99/max y overruns** contra el
presupuesto de 20 ms, **RTF** (tiempo simulado / tiempo de pared), caídas, distancia, CPU y RAM
del proceso y del sistema (muestreador a 10 Hz en `samples_*.csv`), potencia de GPU **medida**
(`nvidia-smi power.draw`, integrado trapezoidal), energía total y la estimación Orin NX.
Salida: `runs/bench_<fecha>/report.{md,json}`.

En Windows el escenario `realtime_50hz` activa `timeBeginPeriod(1)`: sin ello el reloj del sistema
cuantiza `time.sleep` a ~15,6 ms y el jitter medido sería un artefacto de la plataforma, no del
pipeline. La tolerancia de overrun es 25 ms (un sleep aterriza 0-2 ms tarde por diseño); el objetivo
sigue siendo 20,00 ms de periodo medio.

## 4. Consumo: qué es medido y qué estimado

- **GPU: medido.** `nvidia-smi power.draw` muestreado a ~1 Hz e integrado. En portátil incluye el
  consumo base de la GPU dedicada (~10-20 W en reposo), que domina en escenarios cortos.
- **CPU: estimado.** Windows no expone RAPL sin driver. Modelo lineal documentado:
  `P = P_idle + (P_tdp − P_idle) · núcleos_ocupados / n_lógicos`, con `núcleos_ocupados` del tiempo
  de CPU del proceso (user+system, psutil). Por defecto `P_idle=12 W`, `P_tdp=45 W` (Ryzen 7 5800H);
  ajustables con `--p-idle/--p-tdp`. Es un modelo: acota bien la tendencia, no el vatio exacto.
- **Factores x86 → Orin NX (estimaciones, no medidas).** Geekbench 5 single-core públicos:
  5800H ≈ 1450-1500, Orin NX (Cortex-A78AE @ ~2 GHz) ≈ 700-780 → **×2,0** para cargas de CPU
  (×2,2 para overhead de intérprete). Cargas limitadas por ancho de banda: el LPDDR5-6400 del Orin
  (102,4 GB/s) supera al DDR4-3200 del 5800H (~51,2 GB/s) → **×0,9**. La validación definitiva
  exige correr `x2-bench` en el Orin real (allí con `MUJOCO_GL=egl` y `--cores 8`).

## 5. Referencia del robot real

- Ficha ([01](01-robot-x2-ultra-n.md)): batería **421 Wh**, **~2 h** andando a 0,5 m/s →
  **~210 W medios totales**. Ojo: casi todo es tracción de actuadores; el compute (PC1+PC2+PC3)
  es una fracción menor — el Orin admite 10-25 W por modo de potencia.
- Los logs de batería del robot real ([docs/metrics/robot_real_20261020](metrics/robot_real_20261020/README.md))
  dan el porcentaje al 1 % en sesiones de 1-5 min: cuantización demasiado gruesa para estimar W
  por sesión; la referencia válida es la ficha.
- HRI medido en el stack de software sobre el **PC de desarrollo x86 + gemelo** (el Orin NX NO está
  medido — `HRIEGO_gesture/README.md` §9; no emulable aquí): `HRIEGO_gesture/validation/latency_budget.json`
  — LLM en contenedor p50 **1,44 s** / p95 1,59 s; TTS Supertonic RTF p50 **0,162**; alineación CTC
  cold p50 86 ms. `HRIEGO_gesture/visits_v2/resource_profile.json` — generación **71 tok/s**;
  contenedor `x2-llm`: 5,7 GB RAM pico, GPU 99 % de utilización, VRAM 3,9 GB.

## 6. Límites conocidos

- La física MuJoCo es carga del **gemelo digital**, no del robot: allí la locomoción la ejecuta el
  MC en hardware dedicado. Comparar `cycle_50hz` con el presupuesto del Orin es orientativo.
- RSS del proceso es acumulativo entre escenarios (Python/OpenGL no devuelven memoria al SO).
- Sin audio (espeak-ng/ffplay/pw-play no existen en Windows): los escenarios no lo incluyen; en el
  robot real el TTS va en PC3/PC2 y sus números están en la sección 5.
- `--cores 8` limita la afinidad, no la microarquitectura: no reproduce cachés/anchos de banda del
  SoC embebido.
