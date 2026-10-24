# Reporte de estado para el deploy en el X2 Ultra real (Club N)

Fecha: 2026-10-24 · Rama: `claude/reto2-hri`

Detalle conversacional: [README_HRI_CONVERSACIONAL.md](README_HRI_CONVERSACIONAL.md) ·
Retos abiertos: [RETOS_INTEGRACION_HRI.md](RETOS_INTEGRACION_HRI.md) ·
Puesta en marcha: [DEPLOY_X2_REAL.md](DEPLOY_X2_REAL.md)

## 1. Pipeline

cara (UniFace) → confirmación (QR QR01/QC1, o cédula simulada) → FSM
(LLEGADA → CARA → CONFIRMACION → GREETING → [STAFF/WORKER: destino de rol + POINT] → CONVERSATION → FAREWELL)
→ x2-llm (Qwen3-4B Q4, contrato B con json_schema) → Supertonic TTS → ancla CTC (QuartzNet)
→ gestos (emotes CSV cinemáticos por URS, señalamiento, SONIC co-habla en línea)
→ backend (`ShowBackend` / `TwinBackend` / `AimdkBackend`, este último en `dry_run` por defecto).

ASR (humano en el lazo): faster-whisper large-v3-turbo + Silero VAD.

## 2. Conversacional (corrida 2026-10-23 23:40, sin movimiento)

| Componente | Resultado |
|---|---|
| ASR | 6/6 WAV transcritos; p50 0,16 s, p95 0,35 s |
| LLM | tipo de gesto 0,17–0,33 s; primera oración 0,39–0,75 s |
| FSM y accesos | intención y área correctas en los 6 WAV; negación + alternativa; guía por rango (VIP camina con el X2, REGULAR/GUEST con D1, STAFF/WORKER señalamiento + indicación) |
| Suite | 11/11 (6 WAV + 5 visitas) |

Limitaciones (importantes):
- WAV1–WAV4 pasan por una **plantilla** en `fsm.py` (~279–299) que reemplaza la respuesta del LLM: la coherencia del LLM en preguntas **no está demostrada**.
- WAV5 pronuncia el id interno `recepcion_vestibulos`; WAV6 solo probado con VIP.
- Latencia al primer audio TTS **estimada** 1–3,4 s (> 2 s en negaciones y ofertas).

Identidad: `HRI_IDENTITY_REAL` (0 = simulado, defecto; 1 = UniFace + QR real; un fallo ⇒ REJECTED, sin respaldo silencioso). El modo real no se ha probado de punta a punta con foto y QR reales.

## 3. Movimiento validado en el gemelo oficial

Gemelo: MC v1.0.0, URS **emulado** (rampa 2 rad/s), modelo de **cabeza fija**. Todas las corridas con **0 caídas**.

Arreglos del backend validados:
- Rampa de salida emote → SONIC (0,8 s).
- SONIC mantiene el último cuadro 0,3 s, rampa de entrada 0,6 s y de salida a READY 0,8 s.
- Al caminar se reinicia el estado de SONIC; al terminar, rampa desde los brazos medidos.

Corrida `out_diag/twin_v4` (ver [COMPARE.md](../../out_diag/twin_v4/COMPARE.md)):

| Métrica | Antes (twin_v) | Ahora (twin_v4) |
|---|---|---|
| Recortes totales del limitador | 373 | 80 |
| Vuelta a SONIC tras reverencia | 33 % | 1,6 % |
| Vuelta a SONIC tras aplausos | 28 % | 1,6 % |
| Vuelta a SONIC tras corazón | 46 % | 16 % (peor caso restante) |
| Asentarse tras caminar | — (regresión intermedia 20,9 %) | 0 % |
| Diálogo SONIC | 5–8 % | 0–3 % |
| Inclinación máxima | ≤ 5,7° | ≤ 1,84° |

Además: ritmo de 50 Hz con ≥ 99,6 % de ciclos ≤ 5 ms; velocidad ordenada ≤ 6,3 rad/s (tope 10 fuera de emotes); gestos mientras camina rechazados (0 ejecutados); brazos SONIC al 70 %.

## 4. Cintura: bloqueada por defecto

El canal de cintura de `McBodyPose` usaba control de signo ±0,3 con realimentación, lo que producía castañeteo.
`HRI_WAIST_MODE` = **`off` (defecto: u = 0 sin realimentación)** | `slew` | `lowpass` (crítico, `HRI_WAIST_LP_HZ`, 1,5 Hz).

Gemelo, `out_diag/twin_waist2` ([COMPARE.md](../../out_diag/twin_waist2/COMPARE.md)):

| Métrica (3 ejes) | `off` | `lowpass` |
|---|---|---|
| Velocidad medida máx. de cintura (rad/s) | 0,22 / 0,15 / 0,14 | 2,68 / 1,81 / 0,67 |
| Fracción de energía > 3 Hz | 0,08 / 0,13 / 0,09 | 0,17 / 0,81 / 0,36 |
| Caídas | 0 | 0 |

La inclinación varía entre corridas y no discrimina entre modos. Las piernas no son medibles en el gemelo. Con la cintura bloqueada, la reverencia es solo brazos + cabeza.

## 5. Cabeza

- `turn_head` corregido (proporcional y simétrico; antes la derecha quedaba en 0).
- `HRI_HEAD_CHANNEL` = `body_pose` (defecto, solo giro) | `hal` (`/aima/hal/joint/head/command`, giro + cabeceo; giro ±0,4887, cabeceo −0,4014..+0,4887 rad; ≤ 10 rad/s).
- Validado solo en seco: el gemelo no tiene cabeza móvil. En el real, prueba de 2 min: ±0,1 rad de giro en URS leyendo `/aima/hal/joint/head/state`; primero `hal`, luego `body_pose`.

## 6. Pruebas en seco

66 passed, 1 skipped (backends, modos de cintura, asentarse tras caminar, rampas SONIC, rampa de emotes, cabeza, bandera de identidad, FSM).

## 7. Seguridad y puesta en marcha

- Sin modo grúa: `dry_run` → baja velocidad con parada de emergencia en mano → completo.
- Interlock de caída siempre activo: pelvis < 0,62 m; inclinación > 12° (salvo preset con pelvis ≥ 0,66 m); > 25° siempre.
- `preplaying_tick` = 500 (con 0 el robot se cae).
- En el real no se sale de URS por el SDK (MotionBusy) ⇒ emotes cinemáticos por URS.

## 8. Bloqueos para el deploy

| # | Bloqueo | Impacto |
|---|---|---|
| a | Código del motion server ya vendorizado en `third_party/hriego_opt` (sin pesos). Faltan a bordo: modelos (~4 GB), entornos y módulos externos (`paths`, `st_worker`, `x2_sonic`, GMR, SONIC), todos hoy x86 | Para correr todo local en el Orin hay que construir las dependencias aarch64 |
| b | Nunca corrió todo junto con movimiento (la corrida del 23:41 congeló el PC por memoria) | Latencia y memoria reales desconocidas |
| c | El contenedor `deploy/` (aarch64) nunca se ensayó con el pipeline HRI | Riesgo de integración a bordo |
| d | Coherencia del LLM en preguntas depende de la plantilla | Calidad de la demo |

Riesgos sim2real: URS real ≠ emulado; retardo de actuadores; masa 45 kg (sim) vs 39 kg (real); MC v1.0.0 vs v1.1.4; semántica de `McBodyPose` no verificada en el real.

## 9. Preparación

| Momento | Nota |
|---|---|
| Hoy | ~6/10 |
| Tras commitear `opt/` y una corrida en seco dentro del contenedor a bordo | 7–8/10 |
| Tras la primera sesión real a baja velocidad | 8–9/10 |

## 10. Commits clave (`claude/reto2-hri`)

| Commit | Contenido |
|---|---|
| 344e3f5 | App HRI en línea, sin modo grúa |
| d1317a3 / 3ceef74 | Runtime T1–T6; README conversacional y retos |
| e758540 | Bandera de identidad |
| 2da75ad | Gemelo twin_v |
| 78aa645 | Cabeza + rampa de salida de emotes |
| 4918f13 | Rampas SONIC |
| 9f4659a | Asentarse tras caminar |
| 0376af2 | Gemelo twin_v4 |
| cc788d5 / 693b040 | Modos de cintura; bloqueo real |
| 17ec906 | Gemelo twin_waist2 |
| 0ab5519 | Cintura bloqueada por defecto |
