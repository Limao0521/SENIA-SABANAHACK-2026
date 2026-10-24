# 13 · Registro del trabajo de benchmark y análisis de despliegue

Documentación completa de lo realizado entre el 22 y el 23/10/2026: el harness `x2-bench`
([docs/12](12-benchmark-consumo.md)), sus dos corridas válidas, el análisis de consumo de CPU, la
corrida sobre la rama `reto2-hri` y el análisis de viabilidad de despliegue a bordo. Los informes
crudos (JSON/MD/CSV) están en `runs/` y son la fuente de verdad; este documento los resume y los
pone en contexto.

## 1. Objetivo (y su reformulación)

1. **Original:** emular en el PC de desarrollo (x86-64, Windows) el procesamiento que el X2 Ultra-N
   reparte entre sus ordenadores y medir consumo y rendimiento de las piezas de los dos retos,
   teniendo en cuenta la diferencia de arquitectura (PC x86-64 vs Jetson Orin NX ARM).
2. **Reformulado (el que manda):** el benchmark debe responder **«si todos los elementos del sistema
   pueden ejecutarse a nivel local dentro de un robot real X2 Ultra, sin depender de un PC externo
   para el procesamiento»**. Eso se materializó en el comando `x2-bench --deployment`
   (`src/x2_ultra/bench/deployment.py`, sección 7).

## 2. Ramas, commits y entorno

| Rama | Commits | Contenido | Estado remoto |
|---|---|---|---|
| `feat/consumption-benchmark` | `9a2c1d1` harness + `88f8470` fix de trayectoria | `src/x2_ultra/bench/` (8 módulos), tests, docs/12, `runs/bench_full_v2/` | pusheada (HEAD `88f8470`) |
| `reto2-hri` (local; upstream `origin/claude/reto2-hri`) | `e58de03` (HRI reto 2) + merge `7cf9a0f` + `5615ce7` | rama del reto 2 con el harness fusionado + módulo de despliegue | pusheada (`e58de03..5615ce7`) |

Entorno de las corridas: PC de desarrollo Windows 11, Ryzen 7 5800H (16 hilos), GPU NVIDIA dedicada
(medida con `nvidia-smi power.draw`), venv `.venv-bench` (Python 3.12, mujoco 3.17.0, onnxruntime
1.31.0), `MUJOCO_GL=wgl`, `PYTHONIOENCODING=utf-8`.

## 3. Metrología: qué es medido y qué estimado

- **Latencias: medidas** con `time.perf_counter` alrededor de cada ciclo; percentiles exactos
  (p50/p95/p99/max) contra el presupuesto de 20 ms (50 Hz); overruns con tolerancia de 25 ms.
- **RTF: medido** (tiempo simulado / tiempo de pared).
- **Jitter de tiempo real: medido** con `timeBeginPeriod(1)` en Windows (sin ello el sleep se
  cuantiza a ~15,6 ms y falsearía la medida).
- **GPU: medida** (`nvidia-smi power.draw` ~1 Hz, integrado trapezoidal; incluye el consumo base de
  la GPU dedicada en portátil, ~10-20 W, que domina en escenarios cortos).
- **CPU: estimada** (Windows no expone RAPL): modelo lineal `P = 12 W + (45−12) · núcleos_ocupados/16`,
  núcleos ocupados del tiempo de CPU del proceso (psutil). Acota la tendencia, no el vatio exacto.
- **Extrapolación al Orin NX: estimada** con factores de Geekbench 5 single-core públicos
  (×2,0 cpu_bound, ×2,2 overhead de intérprete, ×0,9 memory_bound) y **banda de incertidumbre
  ×1,8-2,2**. La validación definitiva exige correr `x2-bench` en el Orin (fase F0 del
  [plan de despliegue](14-plan-despliegue-a-bordo.md)).
- **Cifras HRI (LLM/TTS/CTC/Face): medidas en el PC de desarrollo x86 + gemelo**, NO en el Orin ni
  en el robot (`HRIEGO_gesture/README.md` §9: «El Orin NX 16 GB no está medido»). Se citan como
  referencia con esa advertencia explícita en todos los informes.

## 4. Incidente metodológico: corrida v1 invalidada

La primera corrida completa (`runs/bench_full_v1`) registró **4 caídas** en `stage_walk` y
`realtime_50hz`. Causa: la trayectoria de esos escenarios hacía que los robots **andarieran hacia
atrás a 0,5 m/s cerca del canto de la tarima** (inversión de sentido en el borde), y la policy no
lo sostiene. No es un defecto del repo ni del robot simulado: es un artefacto del guion del
benchmark. Corrección (commit `88f8470`): trayectoria **circular** `vx=0.4, wz=0.5` (radio ≈0,8 m),
los dos robots describen círculos paralelos en fase con separación constante de 1,5 m — verificado
con trazas de 60 s (ultra y∈[−0,8,0,8], edu y∈[0,7,2,3], siempre dentro de la tarima
x∈(−1,9,1,4), y∈(±3,0)). Un intento intermedio con `wz=0.35` (radio 1,14 m) también se invalidó:
el edu se salía por y=3,03 > 3,0.

**v1 quedó marcada como inválida** (nota metodológica en `runs/bench_full_v2/report.md`: qué se
detectó, por qué no es defecto del repo, commit de la corrección). Las latencias de v1 y v2
coinciden dentro de ±0,3 ms — el incidente no contaminaba las cifras de latencia, solo las de
caídas. **v2 es la corrida válida.**

## 5. Corrida válida v2 (`runs/bench_full_v2/`, rama feat/consumption-benchmark)

9 escenarios, **0 caídas**, show completo de 19 parlamentos terminado, 5/5 rutas del club
completadas. Cifras clave (detalle por escenario en `runs/bench_full_v2/report.md`):

| Escenario | Resultado clave |
|---|---|
| `stage_show` (show comercial, 2 robots + física) | ciclo 50 Hz mean 5,02 / p99 6,61 ms · **RTF 4,19×** |
| `club_tours` (5 categorías de recepción) | 5/5 done, 0 caídas · RTF ~10× · peor ciclo (externo) mean ~2,0 / p99 ~3,3 ms |
| `realtime_50hz` (jitter real, 30 s) | periodo **20,00 ± 0,25 ms**, p99 20,36 ms · **0 overruns** |
| `policy_inference` / `physics_single` | µs por inferencia ONNX / por `mj_step` (ver report) |

**Estimación Orin NX (banda ×1,8-2,2):** `stage_show` p99 → **11,4-13,9 ms = 57-70%** del
presupuesto de 20 ms (cabe, con la física incluida, que a bordo no corre); `club_tours` mean →
**17-21%** del presupuesto. Ojo: la fracción de presupuesto se calcula sobre la **media**, no sobre
el p99 (aclaración de la nota metodológica).

### 5.1 Consumo de CPU (lo que pidió el usuario explícitamente)

- **La física MuJoCo domina**: las escenas de 2 robots consumen **6-6,7 núcleos de media con picos
  de ~10** (de 16 hilos); `club_tours` ~3,4 núcleos; `stage_dance` ~1,2; `video_decode` ~0,5.
- **Potencia CPU estimada: 13-26 W** según escenario (modelo lineal de la sección 3).
- **Aviso del `realtime_50hz`**: el threadpool de MuJoCo hace *spin-wait* — el escenario registra
  ~125 core-ms por ciclo cuando el cómputo real son ~26-34 core-ms; su cifra de CPU está inflada
  por la espera activa, no por trabajo.
- **RSS acumulativo hasta 4,2 GB**: Python/OpenGL no devuelven memoria al SO entre escenarios; es
  artefacto de medición en un solo proceso, no del sistema real (en el robot cada pieza va en su
  proceso).
- Referencia del robot real: batería 421 Wh, ~210 W medios andando — **casi todo actuadores**; el
  compute (PC1+PC2+PC3) es una fracción menor (el Orin admite 10-25 W por modo de potencia).

## 6. Corrida sobre `reto2-hri` (`runs/bench_reto2hri/`)

La rama del reto 2 (recepción HRI con FSM de 6 turnos, gestos precomputados HRIEGO, `waist_delta`)
se fusionó con el harness sin conflictos. Verificación previa: el único cambio de motor que trae
(`RobotRig.waist_delta`) solo actúa **con el robot parado y mando <0,05**, así que no toca los
escenarios de marcha. Corrida completa: exit 0, **0 caídas en los 9 escenarios**, 5/5 rutas
completadas.

| Escenario | reto2-hri | v2 | Δ |
|---|---|---|---|
| `stage_show` | mean 5,03 / p99 6,67 ms · RTF 3,94× | mean 5,02 / p99 6,61 · RTF 4,19× | ruido |
| `realtime_50hz` | 20,00 / p99 20,36 / max 20,73 ms · 0 overruns | 20,00 / p99 20,36 · 0 overruns | idéntico |
| `club_tours` (peor categoría) | mean 2,01 / p99 3,31 ms → Orin est. p99 6,62 ms = **20%** del ciclo | ≈igual | ruido |

Un max puntual de 252 ms en `stage_show` es un outlier del scheduler de Windows (el p99 sigue en
6,67 ms y los overruns en 0%). **Conclusión: la rama reto2-hri no introduce regresiones de
rendimiento.**

## 7. Análisis de viabilidad de despliegue a bordo (`--deployment`)

Commit `5615ce7`. Comando: `x2-bench --deployment [report.json]` (por omisión cita el
`runs/bench_*` más reciente; no corre escenarios). Salida: `runs/deployment_reto2hri/deployment.{md,json}`.
Inventario: 11 elementos (MC/locomoción, stream URS 50 Hz, player HRI, LLM Qwen3-4B, TTS+CTC, Face,
gestos SONIC, presets vendor, pack comercial, gemelo, venue) × ordenador destino × veredicto ×
evidencia con rutas fuente.

**Veredicto:**

- **Por presupuesto: SÍ.** Config A (demo precomputada): 4,82 GB = 30% de la RAM de PC2. Config B
  (+LLM en línea): 9,52 GB = 59%. Config C (todo en línea + motion server): 14,02 GB = 88%, JUSTO.
  Ciclo de control estimado en Orin ≤20% del presupuesto (club). TTS RTF 0,16. La locomoción ya la
  lleva el MC vendor en PC1, probado en robot real.
- **Hoy: NO.** El sistema integrado **nunca ha corrido a bordo**: todo lo validado se opera desde
  un PC externo x86 con docker y cable a 10.0.1.41 (`scripts/robot_real.sh`); el puente
  player→robot no existe (`show/` no importa `x2_ultra.real`, aunque el handoff de `real/arms.py`
  sí está probado en el robot con error medio 0,064 rad); y el Orin NX no está medido.
- **El gemelo MuJoCo se queda fuera a bordo por diseño** (6-6,7 núcleos x86 la escena de 2 robots;
  saturaría los 8 núcleos del Orin y no hace falta: la locomoción real la ejecuta el MC).

El análisis lista además **8 gaps sim2real** (techo URS 0,35 rad/s vs 2,5 del sim; cabeza sin canal
URS; salida de URS por SDK no conseguida —`MotionBusy code=9`—; preplay MC de 1,0 s en presets;
marcha con brazos arriba no validada en real; sin manos —sag ~0,03 rad, error ~2×—; interlock de
caída debe leer HAL a 500 Hz; MC v1.1.4 vs v1.0.0) y un **checklist de 8 pasos** cuyo camino
mínimo son los 4 primeros: contenedor PC2 con SDK aarch64, puente player→real, audio a bordo y
re-timing al techo real.

## 8. Correcciones de exactitud aplicadas

- `report.py` y `docs/12` §5 decían «HRI medido en el stack real»: **falso** — las medidas son del
  PC de desarrollo x86 + gemelo. Corregido en `5615ce7` con la cita explícita de
  `HRIEGO_gesture/README.md` §9.
- `show/` nunca envía órdenes al robot físico (`docs/integracion-hri-sabana.md`): confirmado por
  inventario de importaciones, y citado como evidencia en el análisis de despliegue.

## 9. Tests y calidad

`tests/test_bench.py`: **9 tests pasando** (percentiles y overruns, modelo de potencia lineal,
factores Orin, muestreador de sistema, registro de escenarios, roundtrip de informe y roundtrip
del análisis de despliegue). Pyright da imports sin resolver (mujoco, deployment) por usar el
Python del sistema en vez de `.venv-bench`: artefacto de entorno, ignorable.

## 10. Índice de artefactos

| Artefacto | Ruta |
|---|---|
| Harness del benchmark | `src/x2_ultra/bench/` (scenarios, runner, stats, power, system, report, deployment) |
| Uso y metodología del bench | [docs/12](12-benchmark-consumo.md) |
| Corrida válida v2 (+ nota metodológica) | `runs/bench_full_v2/report.{md,json}`, `samples_*.csv` |
| Corrida reto2-hri | `runs/bench_reto2hri/report.{md,json}`, `samples_*.csv` |
| Análisis de despliegue | `runs/deployment_reto2hri/deployment.{md,json}` |
| Plan de desarrollo a bordo | [docs/14](14-plan-despliegue-a-bordo.md) |
| Guía de pruebas físicas | [docs/15](15-guia-pruebas-fisicas.md) |
| Referencias del robot real | [docs/09](09-robot-real.md), `docs/metrics/robot_real_20261020/` |
| Validación HRI reto 2 | `docs/reto-2/README_HRI.md`, `out_reto2/REPORT.md` |
