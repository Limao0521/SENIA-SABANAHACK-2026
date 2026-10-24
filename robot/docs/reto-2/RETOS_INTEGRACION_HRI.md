# Retos de integración — HRI Conversacional (Club N)

Solo capa conversacional/HRI (LLM, FSM, ASR, TTS, identidad). Gemelo/SDK/motion fuera de alcance.
Estado actual: corrida online 23:40 con 11/11 pass, pero con las limitaciones de
`README_HRI_CONVERSACIONAL.md` §(e). Retos, con estado y siguiente paso cada uno.

## 1. Grounding en KB sin plantillas (crítico)

**Problema:** hoy `_kb_grounded` (fsm.py ~279–299) sustituye la frase del LLM por una plantilla
cuando no cita la KB; en la práctica WAV1/3/4 responden la misma lista genérica que WAV2. El
"pass" no mide coherencia pregunta↔respuesta.
**Estado:** plantilla activa en producción de tests; LLM rara vez cita la KB con el prompt actual.
**Siguiente paso:** en vez de post-corregir, **pre-inyectar** en el prompt la ficha del espacio
detectado (nombre, dirección, piso, horario de `club_kb.json`) + la lista de espacios permitidos
del rango; añadir un **validador de cita de KB con 1 reintento** (segunda llamada con la ficha
más explícita); si el reintento también falla, el fallback a plantilla se **cuenta como fallo del
test**, no como pass.

## 2. Tests estrictos por WAV, 3 repeticiones, todos los rangos

**Problema:** checks laxos (`answered_from_kb` se satisface con plantilla), 1 sola repetición,
WAV1–4 solo REGULAR y WAV6 solo VIP.
**Siguiente paso:** aserciones específicas por WAV (WAV3 debe mencionar gimnasio/piso 5/horario;
WAV4 restaurantes; WAV2 solo permitidos; negaciones sin ids crudos), matriz **6 WAV × rangos
aplicables × 3 reps**, y registrar tasa de éxito, no un único pass/fail.

## 3. Nombres hablados vs ids internos

**Problema:** WAV5 dice "`recepcion_vestibulos`" en voz alta.
**Siguiente paso:** capa de render id→`nombre` de la KB obligatoria en todo texto dirigido a TTS
(negaciones, alternativas, POINT verbal); test que prohíba `\w+_\w+` (snake_case) en `speech`.

## 4. Género/título de la persona

**Problema:** la plantilla dice "Señorita"/"señora" a cualquier VIP; prefijos hardcoded por rango.
**Siguiente paso:** llevar `genero`/`titulo` en la identidad confirmada (T2) e inyectarlos como
constantes del contrato B; nunca inferir tratamiento del nombre.

## 5. Latencia TTS ≤ 2 s de primer audio

**Problema:** TTS estimada en 1.07–3.4 s; negaciones y ofertas de guía superan 2 s ⇒ se siente
muerto el diálogo. Además hoy la latencia es **estimada**, no medida.
**Siguiente paso:** Supertonic **por frase** (no por utterance completa) con **streaming de la
primera frase** al altavoz mientras se sintetiza el resto; medir el primer audio real (timestamp
de primer chunk reproducido) y hacerlo métrica de pass/fail.

## 6. Turn-taking del ASR: VAD, barge-in, ruido

**Problema:** el endpointing cierra con ~500 ms de silencio VAD (~0.65 s percibidos); no hay
barge-in (el usuario no puede interrumpir al robot mientras habla); los WAV son limpios — en
recepción real habrá ruido de fondo y voces cruzadas.
**Siguiente paso:** sintonizar umbral/duración de silencio de Silero por entorno; pausar ASR o
permitir interrupción durante playback TTS (barge-in ⇒ parar TTS y procesar); probar con WAVs
ruidosos reales y con habla solapada.

## 7. Identidad real en el bucle y flujo de rechazo

**Problema:** cara/QR reales nunca entraron en la corrida online (todo simulado);
`HRI_IDENTITY_REAL` no está cableado.
**Siguiente paso:** implementar el flag (`0`=simulado default, `1`=cara UniFace + QR físico
QR01/QC1 Ed25519 vía zxing-cpp); con `1`, fallo de verificación ⇒ **rechazo cortés** (estado nuevo
o NEGATION) sin pasar a GREETING; test e2e con credencial válida, inválida y de otra persona.

## 8. Timeout de 30 s y farewell

**Problema:** el spec exige DESPEDIDA o 30 s sin interacción ⇒ FAREWELL; el timeout está en la
FSM pero no ejercitado en wavtest (visitas usan turnos guionizados).
**Siguiente paso:** test de inactividad real (acelerado con reloj inyectable) y farewell por
timeout distinto del farewell por despedida explícita.

## 9. Farewell en visitas STAFF

**Problema:** la visita STAFF termina en 2 turnos con farewell genérico; no cierra el ciclo
"destino de rol cumplido" (p. ej. confirmar que va a su puesto).
**Siguiente paso:** farewell específico de rol tras POINT/destino proactivo; verificar en
visit_STAFF/visit_WORKER que el cierre menciona el destino de la orden de trabajo.

## 10. Co-residencia de memoria (recurso compartido)

**Problema:** en 12 GB de VRAM corren x2-llm (3.9 GB) + ASR (1.2 GB) + TTS; al sumar motion
online + video en la app completa hubo un **freeze a las 23:41** (la corrida exitosa 23:40 es
inmediatamente anterior). Host con 22 GB RAM y 2 GB swap ⇒ OOM/swapping silencioso.
**Siguiente paso:** **serializar los trabajos pesados** (cargar/descargar o turnarse LLM↔motion
según fase de la FSM), chequear `free -m`/VRAM antes de cada carga, y never arrancar el gemelo
con <8 GB libres (regla del spec). Medir VRAM pico de la app completa antes de reactivar video.

## Prioridad de ejecución

1. **Grounding sin plantillas** (1) — es lo que invalida la métrica principal de calidad.
2. **Tests estrictos × rangos × 3 reps** (2) — sin esto no se puede verificar el punto 1.
3. **Nombres hablados + género/título** (3, 4) — bugs de usuario visibles, fixes baratos.
4. **TTS ≤2 s streaming + medir latencia real** (5).
5. **Identidad real + rechazo** (7) y **timeout 30 s** (8).
6. **Turn-taking/barge-in/ruido** (6) y **farewell STAFF** (9).
7. **Memoria: serialización de cargas pesadas** (10) — bloqueante solo al reactivar motion+video.
