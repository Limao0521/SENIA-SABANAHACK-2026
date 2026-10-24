# HRI Conversacional — Club N (LLM + FSM + ASR human-in-the-loop)

Rama `claude/reto2-hri`. Este documento cubre SOLO la parte conversacional/HRI (T1, T2, T3, T6).
Gemelo digital, SDK AIMDK y control de movimiento quedan fuera de alcance aquí.
Especificación completa: `docs/reto-2/HRI_RUNTIME_SPEC.md`.

## (a) Contexto

- **Club:** siempre **"Club N"** (Bogotá). Nunca se mencionan nombres reales de clubes. Sin
  tratamientos "don/doña"; registro cortés en español.
- **KB sintética fija:** `src/x2_ultra/hri_runtime/club_kb.json` — 15 espacios (nombre, dirección
  relativa `right|left|front` para POINT, piso, horario) + 6 servicios. Datos inventados pero
  constantes; el LLM responde **solo con la KB**.

### Tabla de accesos (sí/no) por clase

| Espacio | VIP | REGULAR | GUEST | STAFF | WORKER logística | WORKER mantenimiento |
|---|---|---|---|---|---|---|
| recepcion_vestibulos | sí | sí | sí | sí | sí | sí |
| restaurantes_cafeterias | sí | sí | sí | sí | no | no |
| bares_la_cava | sí | sí | no | no | no | no |
| centro_convenciones | sí | sí | sí | sí | sí | sí |
| salas_juntas | sí | sí | no | sí | no | sí |
| gastroworking | sí | sí | no | sí | no | no |
| gimnasio_deportes | sí | sí | no | no | no | sí |
| piscina_spa_humedas | sí | sí | no | no | no | sí |
| biblioteca_galeria | sí | sí | sí | sí | no | no |
| zona_infantil_juvenil | sí | sí | no | sí | no | sí |
| habitaciones_alojamiento | sí | no | no | sí | no | sí |
| oficinas_administrativas | no | no | no | sí | no | sí |
| almacenes_logistica | no | no | no | sí | sí | sí |
| cuartos_tecnicos | no | no | no | sí | no | sí |
| estacionamientos | sí | sí | sí | sí | sí | sí |

Clases: VIP, REGULAR (socio), GUEST (invitado), STAFF, WORKER (subtipo `logistica` |
`mantenimiento`; EXTERNAL ⇒ WORKER). Implementado en `access.py`.

### Personas por rango (identidades sintéticas fijas)

| Rango | Persona (estilo) |
|---|---|
| VIP | mayordomo inglés: usted, título + apellido, sin exclamaciones, voz Supertonic M5 vel. 0.92 |
| REGULAR | anfitrión que lo conoce: usted, por nombre |
| GUEST | concierge que orienta: usted |
| STAFF | colega de confianza: tú, "¡dame esos cinco!" |
| WORKER | recepción operativa breve: usted |

## (b) FSM (`fsm.py`)

```
LLEGADA → CARA → CONFIRMACION → GREETING
   [STAFF/WORKER: DESTINO_DE_ROL proactivo desde credencial/orden de trabajo, con POINT]
→ CONVERSATION (bucle ASR) → FAREWELL
```

- **CONFIRMACION** (T2): QR válido de esa persona ⇒ rango; sin QR ⇒ cédula simulada ⇒ WORKER +
  orden de trabajo simulada.
- **CONVERSATION:** el LLM clasifica la intención con enum estricto
  `PREGUNTA | PIDE_DESTINO(área enum de la tabla) | SI | NO | DESPEDIDA | OTRO`.
  - `PREGUNTA` ⇒ responde con la KB, **sin guiar** (no ofrece acompañar/despachar).
  - `PIDE_DESTINO` ⇒ `ACCESO`: no ⇒ negación cortés (NEGATION_NORMAL) + alternativa autorizada;
    sí ⇒ `GUIDANCE_OFFER`: **VIP** "¿Desea que le acompañe?" (sí ⇒ el X2 camina con él,
    `backend.walk_to`; no ⇒ POINT); **REGULAR/GUEST** "¿Desea que D1 le guíe?" (sí ⇒
    `dispatch_d1(área)`, stub; no ⇒ POINT); **STAFF/WORKER** POINT + indicación verbal, sin oferta.
  - `DESPEDIDA` o 30 s sin interacción ⇒ `FAREWELL`.

**Contrato B (`contract.py`):** cada respuesta del LLM es JSON `{type → speech → trigger_text}`
(orden de campos = orden de generación): `type` = gesto del enum permitido por estado; `speech` =
frase natural ≥6 palabras; `trigger_text` = subcadena literal de 1–4 palabras de `speech` que
dispara el gesto (vacío si `type=NONE`). El código inyecta constantes (nombres, rangos, zonas) y
valida con `json_schema(state, rank, direction)` strict para llama-server; gestos constantes ⇒ el
LLM nunca inventa ids de movimiento. La FSM decide el **acto**; el LLM elige **texto + gesto**.

## (c) Human-in-the-loop: ASR sobre WAV reales

**Pila (T3):** `faster-whisper large-v3-turbo` (repo `mobiuslabsgmbh/...`, int8 GPU, greedy) +
**silero-vad 6.2.3** para endpointing, en venv aislada `.venv-asr` (Python 3.11). API:
`Asr.transcribe(wav) -> {"text","t_start","t_end","latency_s"}` y `StreamingAsr.feed(chunk)`.

Los 6 WAV de validación (`~/Downloads`, WhatsApp Ptt 2026-10-23, convertidos a 16 kHz mono en
`out_diag/asr/wav/`) y el comportamiento esperado:

| WAV | Transcript | Rango test | Comportamiento esperado |
|---|---|---|---|
| 1 (9.24.00) | Me gustaría hacer algo de ejercicio en el club. ¿Qué opciones hay? | REGULAR | PREGUNTA ⇒ responde con KB, sin guiar |
| 2 (9.24.35) | ¿A dónde podría ir dentro del club? | REGULAR | PREGUNTA ⇒ lista SOLO espacios permitidos |
| 3 (9.24.50) | Dame información acerca del gimnasio. | REGULAR | PREGUNTA ⇒ responde, sin guiar |
| 4 (9.26.07) | ¿Qué opciones de comida o restaurantes hay en el club? | REGULAR | PREGUNTA (misma intención que WAV3) |
| 5 (9.26.26) | ¿Puedo ir a los restaurantes? | WORKER | PIDE_DESTINO ⇒ **no** autorizado ⇒ negación cortés + alternativa |
| 6 (9.26.52) | Me gustaría ir al cuarto del staff. | VIP | PIDE_DESTINO ⇒ **no** autorizado para VIP/REGULAR/GUEST ⇒ negación |

## (d) Resultados T1–T6

- **T1 — FSM/accesos/KB/personas/cliente LLM:** 18/18 tests unitarios
  (`tests/test_hri_runtime_fsm.py`, `test_hri_confirm.py`) + visita VIP e2e en vivo.
- **T2 — Confirmación de identidad:** QR firmado (**QR01** JSON b64url + firma **Ed25519**;
  formato compacto QC1 base45; decodificación **zxing-cpp** con fallback OpenCV) + cédula
  simulada. 26/26 tests; QR extranjero/no vinculado ⇒ rechazado.
- **T3 — ASR:** 6/6 WAV transcritos; latencia end-of-audio→texto **p50 0.16 s / p95 0.35 s**;
  VRAM propio **1.2 GB** (≤1.5 GB ✔). Detalle: `out_diag/asr/REPORT.md`.
- **T4 — Servidor de movimiento online:** fuera de alcance de este documento (una línea).
- **T5 — Backends (Show/Twin/Aimdk):** fuera de alcance de este documento (una línea).
- **T6 — App online** (ASR→FSM→x2-llm→TTS→motion→backend): corrida 2026-10-23 23:40,
  **11/11 pass** (`out_reto2/online/wavtest_results.json`, wall 49.2 s, VRAM total 5.1 GB:
  x2-llm 3.9 GB + ASR 1.2 GB).

### T6 por test (latencias en s; ⚠ TTS **estimada**, no medida)

| test | intent | área | ASR | LLM tipo | LLM 1ª frase | TTS 1er audio (est.) | pass |
|---|---|---|---|---|---|---|---|
| wav1 (REGULAR) | PREGUNTA | gimnasio_deportes | 0.159 | 0.352 | 0.545 | 1.164 | ✔ |
| wav2 (REGULAR) | PREGUNTA | biblioteca_galeria | 0.162 | 0.267 | 0.482 | 1.128 | ✔ |
| wav3 (REGULAR) | PREGUNTA | gimnasio_deportes | 0.159 | 0.287 | 0.448 | 1.141 | ✔ |
| wav4 (REGULAR) | PREGUNTA | restaurantes_cafeterias | 0.174 | 0.281 | 0.442 | 1.071 | ✔ |
| wav5 (WORKER) | PIDE_DESTINO | restaurantes_cafeterias | 0.157 | 0.138 | 0.392 | 2.430 | ✔ |
| wav6 (VIP) | PIDE_DESTINO | oficinas_administrativas | 0.173 | 0.165 | 0.401 | 2.502 | ✔ |
| visit_VIP | PIDE_DESTINO+SI ⇒ walk_to acompañado ⇒ farewell (4 turnos) | guiada | — | 0.174–0.234 | 0.431–0.704 | 0.967–4.169 | ✔ |
| visit_REGULAR | PIDE_DESTINO+SI ⇒ dispatch_d1 ⇒ farewell (4 turnos) | guiada | — | 0.175–0.230 | 0.391–0.430 | 0.749–3.039 | ✔ |
| visit_GUEST | PIDE_DESTINO+SI ⇒ dispatch_d1 ⇒ farewell (4 turnos) | guiada | — | 0.183–0.225 | 0.412–0.414 | 0.921–3.148 | ✔ |
| visit_STAFF | destino de rol proactivo + POINT ⇒ farewell (2 turnos) | rol | — | 0.282 | 0.444 | 1.878–2.301 | ✔ |
| visit_WORKER | destino de rol proactivo + POINT ⇒ farewell (2 turnos) | rol | — | 0.182 | 0.401 | 1.406–2.278 | ✔ |

### Flag de identidad real: `HRI_IDENTITY_REAL`

Flag de entorno propuesto (aún no cableado en el código de esta rama): `0` (default) = identidad
**simulada** (cédula/QR de prueba, lo usado en wavtest); `1` = identidad **real**: cara con
UniFace + QR físico verificado (QR01/QC1 Ed25519) ⇒ cualquier fallo de verificación ⇒ persona
**rechazada** (no entra a GREETING).

### Cómo correr

```bash
scripts/run_club_hri.sh --all                     # 6 WAV + 5 visitas (pass/fail)
scripts/run_club_hri.sh --visit VIP --video       # visita VIP → out_reto2/online/vip_visit.mp4
scripts/run_club_hri.sh --wav 5 --rank WORKER
# o directamente (sin envolver en nsenter del docker x2-llm):
python -m x2_ultra.hri_runtime.wavtest
```
Requisitos: x2-llm en `http://127.0.0.1:8080`, ASR en `.venv-asr`, env `physicalai` para el
movimiento online. Revisar `free -m` antes de cargar modelos.

## (e) LIMITACIONES HONESTAS (lectura obligatoria)

1. **El "pass" de WAV1–WAV4 NO mide coherencia del LLM.** `fsm.py` (~líneas 279–299,
   `_kb_grounded`) reemplaza la respuesta del LLM por una **plantilla determinista** cuando el
   speech no cita ningún dato de la KB. En la corrida 23:40 las 4 PREGUNTAS cayeron en la
   plantilla: WAV1, WAV3 y WAV4 responden **exactamente la misma lista de espacios** que WAV2
   ("Sebastian, con gusto: puede visitar recepción y vestíbulos, restaurantes…"), aunque WAV3
   preguntaba por el gimnasio y WAV4 por restaurantes. Los tests dan ✔ porque solo verifican
   "citó KB / no guió", no que la respuesta corresponda a la pregunta.
2. **Género/título hardcoded:** la plantilla VIP dice siempre "Señorita {nombre}" (y "señora
   Laura" en la negación), sin verificar género ni título real de la persona.
3. **Ids internos hablados:** en WAV5 la negación WORKER dice el id crudo
   "`recepcion_vestibulos`" en vez del nombre legible ("recepción y vestíbulos").
4. **Cobertura de rangos incompleta:** WAV6 solo se probó como VIP (falta REGULAR/GUEST);
   WAV1–WAV4 solo como REGULAR (falta VIP/GUEST/STAFF/WORKER para PREGUNTA).
5. **Checks demasiado laxos** (p. ej. `answered_from_kb` se satisface con la plantilla;
   `offers_alternative` no valida que la alternativa sea un espacio permitido distinto).
6. **Latencia TTS estimada, no medida** (`tts_estimated: true`): 1.07–3.4 s por frase; las
   negaciones/ofertas superan 2 s de primer audio ⇒ la conversación se sentiría lenta.
7. **1 sola repetición** de cada test en la corrida online (T3 hizo 3 reps solo del ASR aislado).
8. **Identidad simulada** en todos los tests online: cara/QR reales nunca entraron en el bucle
   (ver `HRI_IDENTITY_REAL` arriba).
