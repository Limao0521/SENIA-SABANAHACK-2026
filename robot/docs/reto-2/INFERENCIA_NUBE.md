# Inferencia en la nube y LLM local más rápido — latencia HRI del X2 Ultra

Fecha: 2026-10-24 · Rama de trabajo derivada de `sabanahack4.0-deploy`.

**Objetivo:** bajar el tiempo entre el fin de habla del visitante y el primer audio del
robot (hoy **11,8–16,3 s** en el X2 real) sin perder calidad. Hay dos palancas, y ambas
se activan por entorno con el comportamiento local actual como valor por defecto:

* **(A) Nube:** una VPS con GPU sirve LLM, ASR y TTS; el robot la usa si la alcanza y, si
  no, cae en el mismo turno a la ruta a bordo (timeouts + cortocircuito).
* **(B) Local:** cambios en el Orin que no dependen de red (prefijo de prompt cacheable,
  pre-síntesis del TTS, modelo/decodificación especulativa) con un banco para medirlos.

> Convención: **[medido]** = medido en esta rama (indica dónde); **[log]** = sale del log del
> robot del 2026-10-24; **[estimado]** = cálculo o dato de terceros, a confirmar.

## Resumen y plan recomendado

| Paso | Qué | Coste | Efecto esperado |
|---|---|---|---|
| 1 | A bordo: cambiar a **Qwen3-4B-Instruct-2507 Q4_K_M** (`MODEL_PATH`) tras correr `deploy/bench_llm_candidatos.sh base instruct2507` en el Orin | 0 | elimina los reintentos (~14 % de llamadas → 0 % en el banco); −5–8 s en esos turnos |
| 2 | A bordo: `HRI_TTS_PREFETCH=1` (probado primero en `--etapa lenta`) | 0 | −1–2 s por turno en el Orin **[estimado]**; −0,3–0,4 s medido en PC |
| 3 | Nube: VPS **L4 en la costa este de EE. UU.** (GCP g2-standard-4 o AWS g6.xlarge, ~0,7–0,8 USD/h), **LLM + ASR + TTS**, modo A (relé por la PC + WireGuard) | ~110–130 USD/mes a 160 h | fin de habla → 1er audio **≈ 3–5 s** (hoy 11,8–16,3 s) **[estimado con mediciones de PC]** |
| 4 | Después: TTS en la GPU del Orin; pulir el prompt de `HRI_PROMPT_PREFIJO_ESTATICO` | 0 | respaldo local más rápido |

Todo es conmutable por entorno; sin variables nuevas el robot hace exactamente lo de antes.

---

## 1. Presupuesto de latencia actual (dónde se va cada segundo)

Fuente: `docs/reto-2/pruebas/2026-10-24_identidad_qr_actuado.md` (3 turnos de Mario, GUEST).
`app_*` se mide desde que el texto del ASR llega a la app; `fin_habla_a_primer_audio_s`
desde que el VAD cierra la frase.

| Turno | ASR | LLM `type` conocido | LLM 1ª frase | 1er audio (app) | Fin de habla → 1er audio | Llamadas LLM |
|---|---|---|---|---|---|---|
| 1 «¿A dónde puedo ir?» | 1,65 | 4,12 | 5,18 | 12,81 | **14,70** | 1 (prefill frío) |
| 2 «¿…dónde está la galería?» | 1,75 | 0,80 | 3,81 | 9,78 | **11,75** | 1 |
| 3 «Muchas gracias.» | 1,49 | 0,73 | 1,75 | 13,92 | **16,31** | **2** (CONVERSATION → FAREWELL) |

A esto se suma el **endpoint del VAD: 0,7 s** de silencio antes de cerrar la frase
(`HRI_VAD_SILENCIO_MS=700`).

Descomposición del turno típico (turno 2, 11,75 s) **[log + estimado]**:

| Etapa | Segundos | Comentario |
|---|---|---|
| Endpoint VAD | 0,7 | fijo, configurable |
| ASR (whisper turbo int8, GPU Orin) | 1,5–1,8 | 1,5–3,1 s en otros turnos |
| LLM prefill | 0,1–0,3 caliente · 2,5–12,6 frío | ~2,3–2,7 k tokens de prompt |
| LLM generación hasta cerrar el JSON | 5–8 | 50–77 tokens a ~9–13 tok/s con el Orin saturado |
| TTS 1ª cláusula (Supertonic CPU, 4 pasos) | 1–2,5 | RTF 0,74 medido; la CPU está a carga 8–23 |
| Cola de audio / ROS | ~0,2 | |

Tres hallazgos que no estaban a la vista:

1. **La app espera el JSON completo** antes de sintetizar: `speech` se cierra ~1–2 s antes
   que el JSON (faltan `trigger_text`, `intent`, `area`) y ese tiempo hoy se pierde.
2. **Turnos con dos llamadas LLM seguidas:** en CONVERSATION, si la intención es `DESPEDIDA`
   o `PIDE_DESTINO`, la FSM hace una segunda llamada (FAREWELL / GUIDANCE_OFFER /
   ACCESS_DENIAL / POINT_GUIDE). El turno 3 (16,3 s) es eso.
3. **La caché de prompt casi no sirve entre estados:** el system prompt empieza por la
   tarjeta del rango **con el nombre del visitante** y sigue con los ejemplos *del estado*,
   y solo después la KB. Al cambiar de estado se reprocesan ~1,3–1,5 k tokens (prefijo común
   CONVERSATION↔FAREWELL: 2 753 de 7 649 caracteres) **[medido en PC]**; con 1 000 tok/s de
   prefill en el mejor caso del Orin eso es 1,5 s, y con la carga real hasta 4–8 s.

---

## 2. Qué conviene mandar a la nube (y qué no)

RTT desde Bogotá **[estimado, fuentes al final]**: Miami 40–60 ms · Virginia (us-east-1)
70–90 ms · São Paulo 110–150 ms (suele pasar por Miami). Enlace del robot: WiFi del recinto
o router 4G (subida 3–10 Mbps, bajada 10–30 Mbps) **[estimado]**.

| Etapa | Datos por turno | Coste de red | En el Orin | En GPU de nube | ¿Mandar? |
|---|---|---|---|---|---|
| **LLM** | ~10 KB de prompt + ~1 KB SSE | 1 RTT + 0 | 5–12 s | 1,5–3 s (8B Q4 en L4/4090) | **Sí: la mayor ganancia** |
| **TTS** | texto → PCM16 16 kHz (32 KB/s de audio) | 1 RTT + ~0,05 s | 1–2,5 s / cláusula, y roba CPU | 0,5–0,8 s en CPU x86 **[medido en PC]**; <0,2 s con onnxruntime-gpu **[estimado]** | **Sí: libera la CPU del Orin** |
| **ASR** | PCM16 16 kHz: 4 s = 128 KB de subida | 1 RTT + 0,1–0,35 s | 1,5–3,1 s | 0,15–0,3 s | **Sí, si la subida ≥ 3 Mbps** |
| Cara (UniFace) / QR | fotos de personas | — | 3,6 s | — | **No: datos personales, se quedan a bordo** |
| VAD | — | — | ~0 | — | No |

Decisiones:

* **El audio viaja como PCM16 a 16 kHz** (no float32 ni 44,1 kHz): es el formato con el que
  el robot reproduce (`HRI_PLAYBACK_SR=16000`) y con el que transcribe whisper, así que no
  se pierde calidad y el volumen es 4–6 veces menor.
* **TTS en la nube = el mismo Supertonic con los mismos presets** (M1–M5): si la nube cae a
  mitad de visita, el respaldo local suena con el mismo timbre. En la nube puede volver a 8
  pasos de difusión (`HRI_NUBE_TTS_STEPS=8`, calidad del reto 2) porque la GPU/CPU sobra;
  a bordo sigue en 4. Un TTS «mejor» (XTTS, F5) cambiaría la voz entre nube y respaldo; se
  deja como opción futura.
* **LLM en la nube = llama.cpp (misma gramática `json_schema` estricta)**, no vLLM: el
  contrato se comporta igual (orden de campos, enums), y el respaldo local usa el mismo
  servidor. Modelo por defecto **Qwen3-8B Q4_K_M** (mejor español y obediencia que el 4B,
  cabe de sobra en 24 GB); Qwen3-14B Q4_K_M si la calidad lo pide.
* Las caras y el QR **no salen del robot**.

---

## 3. Opciones de VPS con GPU (sin contratar nada)

Precios por hora bajo demanda, **[estimado: agregadores, julio–agosto 2026]**; confirmar en
la consola del proveedor. Uso típico de demo: 8 h/día × 20 días = 160 h/mes, apagando la
instancia fuera de horario (el disco se paga aparte, ~10–20 USD/mes).

| Opción | GPU | Región | RTT desde Bogotá | USD/h | 160 h/mes | Notas |
|---|---|---|---|---|---|---|
| **AWS g6.xlarge** | L4 24 GB | us-east-1 (Virginia) | 70–90 ms | 0,805 | ~129 | Factura en COP vía tarjeta; IP elástica; arranque/parada por API |
| AWS g6.xlarge | L4 24 GB | sa-east-1 (São Paulo) | 110–150 ms | 1,368 | ~219 | Más cara y **más lejos** en red desde Colombia: no recomendada |
| **GCP g2-standard-4** | L4 24 GB | us-east1 (Carolina del Sur) | 70–90 ms | 0,707 | ~113 | Spot ~0,28 USD/h si se tolera interrupción |
| **RunPod Secure Cloud** | RTX 4090 24 GB | EE. UU. (varias) | 60–100 ms | 0,69 | ~110 | Más rápida que la L4 en decodificación; pods, sin IP fija (usar WireGuard saliente o su proxy HTTPS) |
| Vultr Cloud GPU | L40S 48 GB / A16 | EE. UU. (Miami no confirmado) | 40–60 ms si Miami | 1,67 (L40S) | ~267 | Sobredimensionada; útil si Miami tiene stock |

**Recomendación:** GCP g2-standard-4 o AWS g6.xlarge en la costa este de EE. UU. (precio
y RTT casi iguales; elegir donde ya haya cuenta/créditos). RunPod 4090 si se prioriza
velocidad del LLM y se acepta un servicio menos «empresarial». São Paulo no compensa.

Velocidad esperada en una L4 **[estimado]**: whisper large-v3-turbo fp16 0,15–0,3 s por
frase de 3–5 s; Qwen3-8B Q4_K_M ~35–50 tok/s de generación y >3 000 tok/s de prefill;
Supertonic en CPU x86 RTF 0,13–0,19 (4 pasos) y 0,25–0,32 (8 pasos) **[medido en PC, §7.3]**.

---

## 4. Conectividad del robot (requisito de primera clase)

Hoy el robot **no tiene internet**: solo el cable a la PC (10.0.1.50 ↔ 10.0.1.41). Tres
modos, de menos a más cambios en el robot:

| Modo | Cómo | Cambios en el robot | Cifrado / autenticación |
|---|---|---|---|
| **A. Relé por la PC (recomendado para la demo)** | La PC tiene internet (WiFi/4G/tethering) y un túnel **WireGuard** a la VPS (10.66.0.1). `deploy/nube/pc_relay.ps1` publica `10.0.1.50:8443 → 10.66.0.1:8443` solo para 10.0.1.41 | **Ninguno**: solo `HRI_NUBE_URL=http://10.0.1.50:8443` | WireGuard (PC↔nube) + token Bearer; el tramo robot↔PC es el cable dedicado |
| B. Robot con internet propio | WiFi del recinto o router 4G en el robot; gateway con dominio y HTTPS automático (Caddy + Let's Encrypt) | Ruta por defecto vía el router (lo configura quien administre la red del robot) | TLS + token Bearer |
| C. WireGuard en el robot | Igual que B pero con túnel en el propio robot | `wireguard-tools` + root en el Orin (L4T r36: verificar módulo `wireguard` del kernel 5.15) | WireGuard + token |

Siempre, en los tres modos: **respaldo local** (abajo) y la PC/router fuera de línea no
para la demo, solo la vuelve más lenta.

---

## 5. Implementación

### 5.1 Lado robot (`src/x2_ultra/hri_runtime/nube.py`)

* `LlmConRespaldo`: misma interfaz que `LlamaStreamClient`. Intenta la nube con timeout de
  primer byte (`HRI_NUBE_LLM_TTFT_S`, 3 s) y de silencio entre trozos
  (`HRI_NUBE_LLM_LECTURA_S`, 3 s); ante error/401/5xx/timeout repite **en el mismo turno**
  contra el llama-server local. Las métricas del turno llevan `backend: nube|local`.
* `AsrConRespaldo` / `TtsConRespaldo`: igual para el socket de x2-asr y para Supertonic.
* `Cortocircuito`: tras un fallo, esa etapa se queda en local `HRI_NUBE_ENFRIAMIENTO_S`
  (30 s) para no pagar el timeout en cada turno; luego reintenta sola. Una **sonda de salud**
  en segundo plano (cada `HRI_NUBE_SONDA_S`, 5 s) lo abre si la nube cae entre turnos y lo
  cierra en cuanto vuelve.
* `LlamaStreamClient` acepta ahora `https://…/prefijo` y token Bearer (`token=`); con la
  URL local de siempre el comportamiento es idéntico.
* Cableado: `live_robot.make_llm`, `live_robot.connect_asr` y los dos `make_tts` de los
  runners envuelven el cliente local **solo si** `HRI_NUBE_URL` está definido. El
  calentamiento del runner actuado calienta nube **y** respaldo local antes de armar.
* Diagnóstico: `python3 -m x2_ultra.hri_runtime.nube` imprime salud y RTT de `/llm` y `/voz`.

Variables (todas opcionales; sin `HRI_NUBE_URL` todo queda local):

| Variable | Defecto | Uso |
|---|---|---|
| `HRI_NUBE_URL` | vacío | base del gateway (`http://10.0.1.50:8443` en modo A) |
| `HRI_NUBE_TOKEN` | vacío | token Bearer (solo por entorno, nunca en el repo) |
| `HRI_NUBE_ETAPAS` | `llm,asr,tts` | qué etapas usar en la nube |
| `HRI_NUBE_LLM_URL` | `$HRI_NUBE_URL/llm` | override del endpoint LLM |
| `HRI_NUBE_LLM_TTFT_S` / `HRI_NUBE_LLM_LECTURA_S` | 3 / 3 | timeouts LLM remoto |
| `HRI_NUBE_ASR_TIMEOUT_S` | 4 | timeout ASR remoto |
| `HRI_NUBE_TTS_TIMEOUT_S` | 3 | timeout por frase TTS remota |
| `HRI_NUBE_TTS_SR` / `HRI_NUBE_TTS_STEPS` | `HRI_PLAYBACK_SR` o 16000 / 8 | formato y calidad del TTS remoto |
| `HRI_NUBE_ENFRIAMIENTO_S` | 30 | segundos en local tras un fallo |
| `HRI_NUBE_SONDA_S` | 5 | sonda de salud en segundo plano (`/llm/health`, `/voz/health`); 0 = apagada |

### 5.2 Lado nube (`deploy/nube/`)

`compose.nube.yml` con tres servicios en una red interna; solo el gateway publica puerto:

* `llm`: `ghcr.io/ggml-org/llama.cpp:server-cuda`, Qwen3-8B Q4_K_M, ctx 16 k, flash-attn,
  KV q8_0, `--cache-reuse`, `--parallel 2`.
* `voz`: `src/x2_ultra/hri_runtime/nube_voz.py` (stdlib HTTP) con faster-whisper fp16 en GPU
  y Supertonic; vuelve a validar el token.
* `gateway`: Caddy; exige `Authorization: Bearer $HRI_NUBE_TOKEN`, enruta `/llm/*` (SSE sin
  búfer) y `/voz/*`. HTTP plano dentro de WireGuard (modo A/C) o HTTPS con dominio (modo B).
  El puerto se publica por defecto solo en `127.0.0.1` (nunca expuesto sin querer). En los
  modos A/C: `NUBE_BIND=10.66.0.1` (IP del túnel) en el `.env` del VPS; en el modo B (dominio
  con TLS de Caddy): `NUBE_BIND=0.0.0.0`. Nunca `0.0.0.0` con HTTP plano: el token viajaría
  sin cifrar.

Scripts: `desplegar_nube.sh <ip>` (genera el token en `deploy/nube/.secretos/`, sube el
stack, descarga modelos, levanta y verifica), `descargar_modelos.sh`,
`wireguard/generar_wg.sh <ip>`, `pc_relay.ps1` (modo A). Guía corta: `deploy/nube/README.md`.

### 5.3 Cómo desplegar y conmutar

```bash
# 1) En la PC: VPS ya creada a mano (Ubuntu + Docker + NVIDIA Container Toolkit)
deploy/nube/desplegar_nube.sh 203.0.113.10 ubuntu
deploy/nube/wireguard/generar_wg.sh 203.0.113.10      # túnel PC↔VPS (modo A)
# PowerShell como administrador, con el túnel WireGuard activo:
#   deploy\nube\pc_relay.ps1
# 2) En el robot (o en el .env de la sesión): activar
export HRI_NUBE_URL=http://10.0.1.50:8443
export HRI_NUBE_TOKEN=$(cat ~/.x2_nube_token)          # copiado a mano, fuera del repo
export HRI_NUBE_ETAPAS=llm,asr,tts
python3 -m x2_ultra.hri_runtime.nube                  # salud + RTT
# 3) Volver a 100 % local: unset HRI_NUBE_URL (o HRI_NUBE_ETAPAS=)
```

En la sesión con actuación, `deploy/compose.hri_club_actuado.yml` ya pasa `HRI_NUBE_URL`,
`HRI_NUBE_TOKEN`, `HRI_NUBE_ETAPAS`, `HRI_TTS_PREFETCH` y `HRI_PROMPT_PREFIJO_ESTATICO`
desde el entorno de quien lanza `deploy/hri_club_con_actuacion.sh` (vacíos/0 por defecto).

---

## 6. (B) Mejoras locales

### 6.1 Implementado (conmutadores, por defecto apagados)

| Conmutador | Qué hace | Ganancia esperada | Riesgo |
|---|---|---|---|
| `HRI_TTS_PREFETCH=1` | En cuanto `speech` se cierra en el stream, un hilo sintetiza el primer trozo **exactamente** como lo cortará `_say`; si la frase final coincide (no hubo reintento ni reescritura por KB) se usa ese audio | 1–2 s por turno (se solapa TTS con `trigger_text`/`intent`/`area`) **[estimado]** | Un hilo más de Supertonic compitiendo por CPU durante la generación; ORT libera el GIL, pero probar primero con `--etapa lenta` |
| `HRI_PROMPT_PREFIJO_ESTATICO=1` | Reordena el system prompt de lo estable a lo volátil: KB completa → contrato general → tarjeta del rango → nombre → ejemplos/espacios/instrucción del estado | Prefijo común CONVERSATION↔FAREWELL pasa de 2 753 a 6 449 caracteres **[medido]** ⇒ en turnos de 2 llamadas se reprocesan ~400 tokens en vez de ~1 400 (1–6 s menos con el Orin cargado **[estimado]**) | Cambia el orden del prompt: validar con el banco (§7) antes de activarlo |

### 6.2 Candidatos de modelo (banco `scripts/bench_llm_hri.py`)

| Candidato | Peso | Idea | Qué vigilar |
|---|---|---|---|
| Qwen3-4B Q4_K_M (actual) | 2,5 GB | base | — |
| Qwen3-4B + borrador Qwen3-0.6B Q8_0 (`DRAFT_MODEL`) | +0,64 GB | el JSON es muy predecible (claves, enums): aceptación alta | en el Orin saturado el borrador también cuesta; medir |
| Qwen3-4B-Instruct-2507 Q4_K_M | 2,5 GB | misma talla, afinado sin «thinking», mejor seguimiento de instrucciones | validez del contrato |
| Qwen3-1.7B Q8_0 | 1,8 GB | ~2× más rápido en generación | español/usted, validez, intención |

Ver resultados medidos en §7.

### 6.3 Otras palancas (no implementadas, por orden de impacto)

1. **TTS en la GPU del Orin** (onnxruntime-gpu de Jetson Zoo para Supertonic): hoy compite
   por una CPU a carga 8–23; en GPU el RTF debería bajar de 0,74 a <0,2 **[estimado]**.
2. **Decir la frase de transición** de CONVERSATION mientras corre la 2.ª llamada LLM en
   turnos DESPEDIDA/PIDE_DESTINO (cambia el diálogo: decisión de producto).
3. Bajar `HRI_VAD_SILENCIO_MS` de 700 a 500 ms (−0,2 s; más cortes en pausas largas).
4. `max_tokens` 320 → 160: no acelera el caso normal (el JSON termina antes) pero acota los
   desbocados.

---

## 7. Mediciones

### 7.1 Banco de calidad/latencia de LLM **[medido en PC]**

El robot estaba fuera de línea (`ssh 10.0.1.41`: timeout), así que el banco se corrió en la
PC de desarrollo con **RTX 3070 Laptop 8 GB** y `ghcr.io/ggml-org/llama.cpp:server-cuda`
(ctx 8192, flash-attn, KV q8_0, `--cache-reuse 512`), 14 escenarios × 3 repeticiones = 42
llamadas por candidato, una sola tentativa sin reintento, semilla 1234. **La calidad
(validez, intención) no depende del hardware**; la latencia es un buen *proxy* de una GPU
de nube tipo L4/4090, **no** del Orin.

| Candidato | validez % | tras reparar % | intención % | 1er token p50/p90 (s) | speech listo p50/p90 | total p50/p90 | tok/s | Veredicto |
|---|---|---|---|---|---|---|---|---|
| Qwen3-4B Q4_K_M (actual) | 47,6 | 85,7 | 100 | 0,41/0,75 | 0,99/1,25 | 1,22/1,53 | 83,8 | referencia |
| + borrador Qwen3-0.6B Q8_0 | 47,6 | 85,7 | 100 | 0,47/0,71 | 0,97/1,22 | 1,16/1,65 | 87,8 | sin pérdida; ganancia marginal en GPU → medir en el Orin |
| + prefijo estático v1 (solo system) | 28,6 | 100 | **66,7** | 0,30/0,35 | 0,81/0,93 | 1,13/1,40 | 69,3 | descartado (repite al visitante) |
| + prefijo estático v2 (implementado) | 35,7 | 100 | 100 | 0,32/0,38 | 0,84/1,00 | 1,17/1,48 | 67,6 | prometedor; apagado hasta pulir el prompt |
| **Qwen3-4B-Instruct-2507 Q4_K_M** | 69,0 | **100** | 100 | 0,46/0,73 | 1,00/1,27 | 1,23/1,50 | 85,4 | **recomendado a bordo**: misma talla y velocidad, 0 reintentos, «usted» y direcciones correctas |
| Qwen3-1.7B Q8_0 | 76,2 | 83,3 | **66,7** | 0,29/0,43 | 0,63/0,85 | 0,77/1,13 | 118,3 | **rechazado**: «querés/te» con REGULAR/GUEST, gimnasio «al frente» (está a la izquierda), DESPEDIDA→OTRO |

Datos crudos (una línea por llamada, con la frase generada) en
`docs/reto-2/pruebas/bench_llm_pc_2026-10-24/`. Qwen3-8B (el modelo propuesto para la
nube) no se pudo medir: el disco de la PC se llenó durante la descarga; su calidad queda
**por confirmar** con el mismo banco en la VPS (`scripts/bench_llm_hri.py --url
http://127.0.0.1:8080` dentro del contenedor `llm`) antes de fijarlo.

Por qué Instruct-2507 recorta latencia sin cambiar de talla: con el modelo actual ~14 % de
las llamadas no pasan el validador ni tras `repair_trigger` y la FSM repite la llamada
entera (en el Orin, +5–8 s ese turno); con Instruct-2507 fue 0 de 42.

* «Validez» = el JSON pasa `contract.validate` tal cual; «tras reparar» = pasa después de
  `contract.repair_trigger` (lo que la FSM ya hace antes de reintentar). Lo que no se repara
  dispara un **reintento completo** (otra llamada LLM entera): con el prompt actual eso
  ocurre en ~14 % de las llamadas (errores: `trigger_text` no vacío con `type=NONE`,
  exclamaciones con VIP, speech > 30 palabras en PIDE_DESTINO). Es una fuente de latencia
  más en el robot.
* **Prefijo estático, versión 1** (todo en un único system reordenado): primer token p90
  0,75 → 0,35 s, pero la intención cae a 66,7 % y el modelo repite la frase del visitante
  como speech ⇒ descartada.
* **Prefijo estático, versión 2** (la implementada: system reordenado + instrucción del
  estado como mensaje `user`): primer token p90 0,75 → 0,38 s, tras reparar 100 %
  (0 reintentos), intención 100 %. Pero en 2 de 14 muestras repite la frase del visitante y
  en ACCESS_DENIAL propone otra alternativa que la pedida ⇒ **queda apagada por defecto**;
  necesita una iteración de prompt y volver a medir antes de activarla en el robot.
* **Borrador Qwen3-0.6B**: misma calidad (la decodificación especulativa no cambia la
  salida) y solo +5 % de tok/s en GPU; en el Orin (limitado por ancho de banda de memoria)
  la ganancia puede ser mayor o negativa por la contención: **medir allí**.
* Nota de compatibilidad: los llama.cpp recientes eliminaron `--draft-max/--draft-min`
  (ahora `--spec-draft-n-max`); `llm/server/entrypoint.sh` solo los pasa si el binario los
  anuncia, así que no rompe, pero en imágenes nuevas el borrador queda sin límite explícito.

### 7.2 Cómo correr el banco en el robot

```bash
# en el robot, con la sesión HRI PARADA (memoria), desde la raíz del repo:
#   modelos extra en llm/models/ (descarga en la PC y scp; el robot no tiene internet):
#   Qwen3-1.7B-Q8_0.gguf            https://huggingface.co/Qwen/Qwen3-1.7B-GGUF
#   Qwen3-4B-Instruct-2507-Q4_K_M.gguf  https://huggingface.co/unsloth/Qwen3-4B-Instruct-2507-GGUF
deploy/bench_llm_candidatos.sh                     # todos; o: base draft06 q17b
REPS=1 LIMITE=6 deploy/bench_llm_candidatos.sh base   # humo
```

Cada candidato deja `out_diag/bench_llm/<etiqueta>_<fecha>.jsonl` (una línea por llamada)
y `_resumen.json` (validez del contrato, validez tras reparar, acierto de intención,
p50/p90 de primer token / speech listo / total, tok/s y muestras de frases); al final
imprime la tabla comparativa (`--comparar`).

### 7.3 Otras mediciones en PC

| Medición | Resultado |
|---|---|
| Supertonic CPU (portátil x86, 4 hilos), 4 pasos | RTF 0,13–0,19; 1ª cláusula de 2,6 s de audio en 0,49 s |
| Supertonic CPU, 8 pasos | RTF 0,25–0,32; 1ª cláusula en 0,81 s |
| `nube_voz` real + `TtsRemoto` por HTTP (localhost, 8 pasos, salida 16 kHz) | 0,69 / 1,17 / 1,36 s para 2,6 / 5,1 / 6,7 s de audio; sobrecoste HTTP ≈ 1 ms; 82 KB por 2,6 s |
| `AsrRemoto` ida y vuelta con 4 s de audio (motor falso, localhost) | 1,5 ms de protocolo |
| `LlmConRespaldo`, nube sana (llama-server GPU) | 0,7–1,4 s por GREETING, `backend=nube` |
| nube rechaza la conexión | 1.ª llamada 2,8 s (Windows reintenta el SYN ~2 s; en Linux es inmediato) y luego 0,68 s en local por el cortocircuito |
| nube «agujero negro» (IP sin ruta) | 1.ª llamada 3,9 s (= TTFT 3 s + local), luego 0,68 s; con la sonda de salud (`HRI_NUBE_SONDA_S`) una caída entre turnos ni siquiera cuesta esos 3 s |
| App completa en tiempo real (`ClubHriApp` realtime, LLM GPU + Supertonic CPU 4 pasos, 1ª cláusula) | texto → 1er audio 1,27–1,77 s; con `HRI_TTS_PREFETCH=1` 0,89–1,49 s (aciertos de caché en 3 de 4 frases) |

Lectura: en una GPU de nube el TTS en CPU pasa a ser la etapa más larga (0,5–0,8 s la 1ª
cláusula). Si se quiere bajar más, `onnxruntime-gpu` en la imagen `voz` (no probado aquí).

### 7.4 Fin de habla → primer audio: hoy vs. objetivo

| Escenario | VAD | ASR | LLM hasta JSON | TTS 1ª cláusula | Red | **Total** |
|---|---|---|---|---|---|---|
| Hoy, Orin (turno de 1 llamada) **[log]** | 0,7 | 1,5–1,8 | 6–10 | 1–2,5 | — | **11,8–14,7** (+0,7 VAD) |
| Hoy, Orin (turno de 2 llamadas) **[log]** | 0,7 | 1,5 | 11–12 | 1–2,5 | — | **16,3** (+0,7) |
| Local + prefetch TTS **[estimado]** | 0,7 | 1,5–1,8 | 6–10 | 0–1 (solapado) | — | **10–13** |
| Local + Qwen3-1.7B + prefetch **[estimado, si pasa calidad]** | 0,7 | 1,5–1,8 | 3–5 | 0–1 | — | **6–8** |
| **Nube LLM + ASR + TTS (L4, us-east)** **[estimado con mediciones de PC]** | 0,7 | 0,3–0,6 | 1,2–2,5 | 0,5–0,8 | 0,1–0,3 | **2,8–4,9** |
| Nube, turno de 2 llamadas | 0,7 | 0,3–0,6 | 2,5–4,5 | 0,5–0,8 | 0,2–0,4 | **4,2–7** |
| Nube caída a mitad de turno | — | — | +3 s una vez (TTFT) y luego local | — | — | como hoy + 3 s, una sola vez |

---

## 8. Riesgos

| Riesgo | Mitigación |
|---|---|
| Sin red o red lenta en el recinto | Respaldo local automático por etapa + cortocircuito; modo A no requiere red en el robot |
| Jitter 4G (picos de 300–800 ms) | Timeouts cortos (3–4 s) y cortocircuito; el peor caso es «como hoy + 3 s» una vez cada 30 s |
| Token filtrado | Token solo en entorno/`.secretos/` (git-ignorados); rotar con `desplegar_nube.sh`; gateway y servidor de voz lo validan; en modo A/C además WireGuard |
| Datos personales | Caras y QR nunca salen; a la nube va el audio de la frase y el texto del diálogo (nombre de pila incluido en el prompt) — avisar en el aviso de privacidad del evento |
| Diferencia de calidad nube/local (8B vs 4B) | Mismo contrato y validador en ambos lados; si la nube cae a mitad de visita el estilo puede cambiar levemente |
| Coste olvidado (VPS encendida) | Apagar fuera de horario; `desplegar_nube.sh parar` |
| Prefetch TTS con el robot armado | Opt-in; probar en `--etapa lenta`; el hilo solo sintetiza, nunca reproduce |

## 9. Pendientes

* Probar en el X2 real (el robot estaba fuera de línea en esta sesión: `ssh` a 10.0.1.41
  sin respuesta) y correr `deploy/bench_llm_candidatos.sh` en el Orin.
* Las variables `HRI_NUBE_TIMEOUT/TTFT/SONDA…` finas no están en el compose (usan su
  defecto); añadirlas si hace falta afinarlas en el robot.
* Iterar el prompt del prefijo estático (repite la frase del visitante en 2/14) y medirlo
  en el Orin, donde la ganancia de prefill es mucho mayor que en la GPU de la PC.
* Medir RTT real desde la red del recinto/4G al proveedor elegido antes de contratar.

## Fuentes de precios y RTT ([estimado])

* AWS g6.xlarge: [Holori us-east-1](https://calculator.holori.com/aws/ec2/g6.xlarge/us-east-1), [DoiT sa-east-1](https://www.doit.com/compute/spot/sa-east-1/g6.xlarge)
* GCP g2-standard-4: [Google Cloud, precios de aceleradores](https://cloud.google.com/products/compute/pricing/accelerator-optimized?hl=es), [Holori us-east1](https://calculator.holori.com/gcp/vm/g2-standard-4/us-east1)
* RunPod RTX 4090: [runpod.io/gpu/4090](https://www.runpod.io/gpu/4090), [DIY AI 2026](https://diyai.io/ai-tools/hosting/runpod-pricing/)
* Vultr L40S: [computeprices.com](https://computeprices.com/providers/vultr/gpus/l40s)
* RTT: [bestusavps Miami](https://bestusavps.com/best-for/miami/), [fly.io Colombia](https://community.fly.io/t/high-latency-in-the-colombian-region/10695), [hatsnet GRU–MIA](https://hatsnet.io/docs/network/latency/pairs/gru-mia-rtt)
