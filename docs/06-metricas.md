# 06 · Métricas de rendimiento

Copia de `robot/docs/reto-2/METRICAS_RENDIMIENTO.md` (rama `sabanahack4.0-deploy`). Cada cifra indica su
fuente: **[medido X2]** en el robot real, **[medido PC]** en el PC de desarrollo, **[usuario]** medido por
el equipo en el robot, **[estimado]** sin medir. Datos crudos: `robot/docs/reto-2/pruebas/`.

Sistema que se usará finalmente: conversación HRI del Club N a bordo del X2 Ultra con actuación
(`live_robot_actuado`), identidad real cara → QR, gesto co-verbal, emotes propios, expresiones de
la cara y asistente de fábrica en pausa. Todas las cifras son del 2026-10-24. **[medido X2]** =
en el robot real (Jetson Orin NX, firmware v1.1.4); **[medido PC]** = RTX 3070 Laptop;
**[usuario]** = medido por el equipo en el robot; **[estimado]** = sin medir.

## 1. Latencia de la conversación (fin del habla → primer audio del robot)

| Momento | Fin de habla → 1er audio | Qué cambió | Fuente |
|---|---|---|---|
| Primer arranque con actuación | **26,8 s** | Supertonic 8 pasos, reintentos del LLM, carga dentro de la sesión armada | [medido X2] |
| Tras optimizaciones 1 (prueba identidad+QR) | **11,8 – 16,3 s** | TTS 4 pasos, 1ª cláusula aparte, LLM sin reintentos triviales, calentamiento antes de armar | [medido X2] (`pruebas/2026-10-24_identidad_qr_actuado.md`) |
| Versión final a bordo | **≈ 5 – 6 s** de inferencia | + pre-síntesis TTS (`HRI_TTS_PREFETCH=1`), despliegue `19bcd55`+ | [usuario] |
| Con inferencia en la nube (no adoptada) | ≈ 3 – 5 s | LLM/ASR/TTS en GPU L4 | [estimado] (`INFERENCIA_NUBE.md`) |

Decisión: la nube no compensa (≈ 1-2 s más rápida a cambio de ~110-130 USD/mes, conectividad del
robot y dependencia de red); queda implementada y apagada (`HRI_NUBE_URL` vacío).

## 2. Por etapa

| Etapa | Antes | Ahora | Fuente |
|---|---|---|---|
| Endpoint VAD | 0,7 s | 0,7 s (fijo) | config |
| ASR (faster-whisper large-v3-turbo int8, GPU Orin) | 1,5 – 3,1 s | 1,5 – 1,8 s | [medido X2] |
| LLM 1er token, prompt en frío (~2,4 k tokens) | 2,5 – 12,6 s | 0,13 – 0,29 s (caché caliente: calentamiento antes de armar) | [medido X2] |
| LLM respuesta completa (`respond`) | 12 – 26 s (reintento por `trigger_text`) | **4,8 – 6,9 s**, 0 reintentos | [medido X2] |
| TTS Supertonic (7,9 s de audio) | 10,1 – 17,7 s (8 pasos, RTF 1,3–2,2) | **5,8 s** (4 pasos, RTF 0,74) + 1ª cláusula ≈ 1 s | [medido X2] |
| TTS 1ª cláusula con pre-síntesis | — | texto → 1er audio 0,89 – 1,49 s (vs 1,27 – 1,77 s sin ella) | [medido PC] |
| Cámara (foto, `rgb_head_front_center`) | — | 1,3 s | [medido X2] |
| Cara (UniFace, subproceso) | — | 3,6 s por foto | [medido X2] |
| QR (OpenCV clásico → zxing-cpp + recortes) | 1 de 6 leídos | sintético 1280×720: QR de 60–100 px leídos (antes ≥ 140 px) | [medido X2] / [medido PC] |

## 3. Calidad del LLM (banco `scripts/bench_llm_hri.py`, 14 escenarios × 3)

| Modelo | Validez del contrato tras reparar | Intención correcta | Tiempo total p50 | Fuente |
|---|---|---|---|---|
| Qwen3-4B Q4_K_M (a bordo hoy) | 85,7 % | 100 % | 1,22 s | [medido PC] |
| **Qwen3-4B-Instruct-2507** (recomendado) | **100 %** | 100 % | 1,23 s | [medido PC] |
| Qwen3-1.7B | 83 % | 66,7 % | 0,77 s (rechazado: «querés», direcciones mal) | [medido PC] |
| + borrador Qwen3-0.6B | igual | igual | +5 % tok/s | [medido PC] |

Pendiente: repetir en el Orin con `deploy/bench_llm_candidatos.sh base instruct2507`.

## 4. Seguridad y movimiento (sesiones armadas, etapa lenta 5/10 rad/s)

| Métrica | Resultado | Fuente |
|---|---|---|
| Caídas / interlock de caída | 0 / 0 en todas las sesiones | [medido X2] |
| Error de seguimiento en READY | medio 0,013 – 0,014 rad, máx 0,059 – 0,097 rad | [medido X2] |
| Watchdog | 1 e-stop correcto (pose congelada al cargar el TTS armado) → corregido cargando todo antes de armar | [medido X2] |
| Limitador | 12 recortes en la transición a READY; 1 por señalamiento; ~1150 en una sesión con co-verbal (suavizado de aceleración, no falla) | [medido X2] |
| Pose del interlock | IMU del torso (~390 Hz) + `leg_odometry` (~157 Hz): v1.1.4 no publica `/aima/hal/odom/state` | [medido X2] |
| Emotes propios (12) | 0 autocolisiones en el MJCF oficial (detector validado con control positivo); 1,5 – 4 rad/s | [medido PC] (`scripts/check_emotes_mujoco.py`) |
| Gesto co-verbal (103 clips SONIC al 70 %) | < 5 rad/s; visible en el robot | [medido PC] / [usuario] |

## 5. Identidad y servicios a bordo

| Métrica | Resultado | Fuente |
|---|---|---|
| Reconocimiento facial (galería: Luis, Josaed, Sebastian, Mario, Laura, Jorge Castellanos) | cos 0,47 – 0,60 para miembros; desconocidas < 0,40 | [medido X2] |
| Flujo cara → QR | cara conocida pide QR; QR fija el rango; sin QR / QR ajeno / no miembro / worker ⇒ personal (+ Draco) | [medido X2] + tests |
| Expresiones de la cara (`PlayEmoji`) | OK con prioridad 60 (con 11 el firmware las rechaza) | [medido X2] |
| Asistente de fábrica | `RUN_MODE=only_voice` durante la sesión; micrófono sigue a ~34 msg/s | [medido X2] |
| Recursos del Orin con la pila arriba | carga 8 – 23 en 8 núcleos (percepción/SLAM de AgiBot), 2 – 3 GB libres | [medido X2] |

## 6. Pruebas automáticas

`PYTHONPATH="src;." python -m pytest tests/test_hri_confirm.py tests/test_hri_asistente.py tests/test_hri_face.py tests/test_hri_live_robot_actuado.py tests/test_hri_live_robot.py tests/test_hri_nube.py -q --noconftest`
→ **158 passed, 1 skipped**. `tests/test_backends.py` → 24 passed. (El único fallo conocido,
`test_live_smoke_x2_llm`, depende del LLM local del PC con contexto de 1024 tokens.)

## 7. Optimizaciones posteriores (en el robot; mejora notada por el equipo, sin cifras aún)

Commit `e787a94`: una sola llamada al LLM en despedidas inequívocas (≈ 5 s menos en ese turno), pre-síntesis de la primera cláusula mientras el LLM todavía escribe (1–3 s), foco de audio retenido por turno (sin pedir/soltar el foco en cada frase) y acuse inmediato por categoría (primera respuesta audible ≈ 2,5 s tras el fin del habla). Desplegadas en el X2: el equipo notó una respuesta más rápida **[usuario]**; las cifras de la tabla son **[estimado]** hasta medirlas en los logs (`fin_habla_a_primer_audio_s`).
