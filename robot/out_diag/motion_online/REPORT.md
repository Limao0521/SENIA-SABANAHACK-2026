# T4 — Movimiento co-speech ONLINE (motion_online) — 2026-10-23

## Qué se construyó
`MotionServer.start_turn(pcm_stream, sr, t_audio0)` → iterador 50 Hz de `(t, arms14, waist3, head2)`
mientras suena el audio, **sin precomputación**. Arquitectura:

- **Servidor** (`src/x2_ultra/hri_runtime/motion_online/server_proc.py`, proceso propio en el entorno
  `physicalai`): envuelve SIN MODIFICAR la cadena probada de HRIEGO_gesture/opt — `o3_brain_motor.Brain`
  (= `m2_motion_server`: StreamTalk fp16 streaming + GMR online 15 Hz + SONIC online, serial pull) con
  modelos **V2** de `o1a_meshes`, **first-block split=14**, prefix `fast` (27 ticks), `armed`. Un
  `sink` envía cada lote de ticks por **UNIX socket, JSON-lines + float32 (base64)** en cuanto existe.
- **Cliente** (`client.py`, solo numpy+stdlib → funciona en el venv del show): materializa el stream de
  PCM (ndarray/chunks, int16/float, cualquier sr → 16 kHz), lanza el servidor si no está vivo, y aplica
  por frame el post-proceso del Reto 2: recortes de posición `prep_arms` (URDF v1.4.0, margen 0.05),
  **amplitud SONIC k=0.7** alrededor del reposo, **waist ±0.10/0.05/0.03** (commit 65de416), head
  ±2.0/0.7, y el **JointLimiter** de la spec: 10 rad/s + 40 rad/s² por articulación (perfil de frenado
  discreto exacto: sin overshoot, |a| ≤ amax siempre). Línea de tiempo: `t = t_audio0 + (k−27)/50`.
- **Hook en `show/engine.py`** (solo constantes + limiter): `SPEC_VEL_JOINT=10`, `SPEC_VEL_{ARMS,WAIST,HEAD}`,
  `spec_vel_clip()` aplicado a `d.ctrl` de brazos/waist/cabeza en `control_tick` — backstop de la spec
  (los rates existentes ARM_RATE 2.5 / HEAD_RATE 2.0 / slew waist ya están muy por debajo: no cambia el
  comportamiento; `tests/test_hri_reception.py` 22/22 siguen pasando).

## Medidas (3 WAV de turnos Reto 2, `assets/hri/.../visits`; bench.json, *_online.npz, *_online_raw.npz)
Modo A: audio ya disponible (`audio_rtf=0`, como TTS pre-bufferizado). Modo B: stream paceado a tiempo
real (`audio_rtf=1.0`). Cómputo = servidor (carga 11.7 s con modelos en caché de página; fría: minutos).

| turno | first_cmd (req→1er tick) | 1er frame de movimiento | audio_start (conv. o3) | mov−audio_start | RTF | lag_last | PSS | VRAM | arm RMSE vs npz | gate <0.20 |
|---|---|---|---|---|---|---|---|---|---|---|
| laura_t02_dialogue (6.6 s) | 0.414 s | 0.654 s | 1.035 s | **−0.381 s** | 0.52 | −3.68 s (va ahead) | 2578 MB | 1348 MB | 0.239 (shift −36) | ✗ (+19 %) |
| josaed_t03_dialogue (5.6 s) | 0.404 s | 0.655 s | 1.029 s | −0.374 s | 0.62 | −2.63 s | 2568 MB | 1348 MB | **0.110** (shift −75) | ✓ |
| sebastian_t02_dialogue (5.1 s) | 0.397 s | 0.679 s | 1.021 s | −0.342 s | 0.55 | −2.78 s | 2515 MB | 1348 MB | **0.061** (shift −74) | ✓ |
| laura_t02 (paceado rtf=1.0) | 2.41 s* | 2.65 s | 3.38 s | **−0.764 s** | 1.13 (paceado) | +0.29 s | idem | idem | (misma gen.) | — |

\* el paceo de la ventana 0 exige 2.0 s de audio real antes del primer embed (WavLM por ventanas de
1.73 s de audio); aun así el movimiento queda listo 0.76 s ANTES de que arranque el audio.

- **Cómputo por ventana vs tiempo real**: GMR+SONIC 0.56–0.79 s por ventana de 1.733 s de audio
  (2.2–3.1× más rápido que tiempo real); StreamTalk domina el resto (RTF total 0.52–0.62).
- **Lag estacionario**: negativo en modo A (el pipeline termina 2.6–3.7 s antes que el reloj de audio);
  en modo B los frames llegan por delante de su vencimiento (lead_D 2.84 s incluye la espera de audio).
- **Seguridad del stream final**: arms vmax 1.79 rad/s ≤ 10, amax 37.5 ≤ 40; waist+head vmax 0.9;
  |waist| exactamente en bounds [0.10, 0.05, 0.03]; 0 caídas, tilt ≤ 9.9° (sim brain, no twin).
- **Calidad (RMSE de brazos, parte de movimiento, alineación óptima, raw k=1 sin limiter vs
  `sonic_actual` del npz offline)**: 2/3 bajo el gate de semilla 0.20 rad. laura 0.239 se pasa 19 %:
  divergencia semilla/caos (su shift óptimo −36 ≠ el estructural −73/−75 de los otros dos = la
  trayectoria online se separó de la offline por ruido de semilla de StreamTalk amplificado por SONIC,
  fenómeno ya documentado en opt/o3_brain_motor/REPORT.md Part A). No es inestabilidad: sin caída,
  tilt normal, waist/head RMSE 0.038.
- **Memoria**: PSS ~2.5 GB + VRAM 1.35 GB (holgado vs los ~4 GB presupuestados en la spec; cabe junto a
  x2-llm/ASR/show en los 22 GB con ~9 GB disponibles).

## Tests
`tests/test_motion_online.py` **10/10** (limiter v/a/convergencia/reset, post-proceso k=0.7+bounds,
protocolo float32 exacto, materialize/resample, `start_turn` contra servidor falso en UNIX socket —
timeline 50 Hz y bounds —, hook de engine). `tests/test_hri_reception.py` **22/22** (sin regresión).

## Reproducir
```bash
# servidor (arranca solo con autostart; log en out_diag/motion_online/server.log)
PYTHONPATH=src ~/anaconda3/envs/physicalai/bin/python -m x2_ultra.hri_runtime.motion_online.bench \
    --paced laura_t02_dialogue --out out_diag/motion_online        # 3 turnos Reto 2 + paceado
PYTHONPATH=src .venv/bin/python -m pytest tests/test_motion_online.py -q
```

## Riesgos / pendientes
1. RMSE de laura 0.239 > 0.20 (semilla/caos online-vs-offline; mitigable fijando semilla común o
   comparando contra referencia regenerada con la misma cadena online).
2. El paceo por ventanas de StreamTalk (1.73 s de audio por ventana) impone ~2.4 s al primer tick con
   audio estrictamente a tiempo real; con TTS más rápido que tiempo real (caso real) se obtienen los
   0.41/0.65 s del modo A.
3. Un solo cliente a la vez por diseño (simple/robusto); el show es el único consumidor.
4. El servidor vive en el entorno `physicalai` (ruta absoluta por env `MOTION_PHYS_PYTHON`); el venv del
   show solo necesita numpy (cliente).
