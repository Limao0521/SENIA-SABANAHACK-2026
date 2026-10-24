# HRI runtime — especificación compartida (Club N)

Contrato común para las tareas paralelas T1–T6. Todo el código nuevo vive en `src/x2_ultra/hri_runtime/` (rama `claude/reto2-hri`). Cada tarea escribe SOLO en sus carpetas; nadie hace `git add/commit` salvo T6.

## Reglas globales
- Club: **"Club N"** (nunca nombres reales de clubes). Sin "don/doña". Bogotá, registro cortés.
- Datos del club: **base sintética fija** `src/x2_ultra/hri_runtime/club_kb.json` (horarios, ubicaciones, servicios inventados pero siempre iguales). El LLM responde solo con la KB.
- Clases: VIP, REGULAR (socio), GUEST (invitado), STAFF, WORKER (subtipo `logistica` | `mantenimiento`). EXTERNAL ⇒ WORKER.
- Personajes: VIP = mayordomo inglés (usted, título + apellido, sin exclamaciones, voz Supertonic M5 vel. 0.92); REGULAR = anfitrión que lo conoce (usted, nombre); GUEST = concierge que orienta (usted); STAFF = colega de confianza (tú, "¡dame esos cinco!"); WORKER = recepción operativa breve (usted).
- Seguridad de movimiento: límite por articulación **10 rad/s** fuera de emotes; emotes limitados por especificación (cadera roll/pitch 40, cadera yaw 30, cintura 20, brazos 20, cabeza 30 rad/s); torque máx 120 N·m; bucle bajo nivel 500 Hz. Nunca gestos mientras camina. Cintura acotada (±0.10/0.05/0.03) con filtro existente; reverencia con tope 0.10 rad/0.06 rad/s.

## Tabla de accesos (sí/no) — `access.py` (T1)
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
Cada espacio tiene en `club_kb.json` una dirección relativa a la recepción: `right` | `left` | `front` (para POINT).

## FSM (T1) — `fsm.py`
LLEGADA → CARA → CONFIRMACION (T2: QR válido de esa persona ⇒ rango; sin QR ⇒ cédula simulada ⇒ WORKER + orden de trabajo simulada) → GREETING → [STAFF/WORKER: DESTINO_DE_ROL proactivo desde credencial/orden de trabajo, con POINT] → CONVERSATION (bucle ASR):
- intención (LLM, json_schema enum): `PREGUNTA` | `PIDE_DESTINO`(área enum de la tabla) | `SI` | `NO` | `DESPEDIDA` | `OTRO`.
- PREGUNTA ⇒ responde con KB, **sin guiar**.
- PIDE_DESTINO ⇒ ACCESO: no ⇒ negación cortés (NEGATION_NORMAL) + alternativa; sí ⇒ GUIDANCE_OFFER:
  - VIP: "¿Desea que le acompañe?" sí ⇒ el X2 camina con él (backend.walk_to); no ⇒ POINT.
  - REGULAR/GUEST: "¿Desea que D1 le guíe?" sí ⇒ backend.dispatch_d1(área) (stub); no ⇒ POINT.
  - STAFF y WORKER: POINT + indicación verbal.
- DESPEDIDA o 30 s sin interacción ⇒ FAREWELL.
La FSM decide el acto; el LLM elige texto + gesto dentro del enum por estado (contrato B: type → speech → trigger_text, constantes inyectadas por código).

## Interfaces Python (fijas)
```python
# hri_runtime/backends/base.py  (T5 lo crea; T4/T6 lo usan)
class Backend(Protocol):
    def state(self) -> dict: ...            # {"t","pelvis_z","tilt_deg","mode","stable","walking":bool,"fall":bool}
    def stream_upper_body(self, t: float, arms14, waist3, head2) -> None: ...   # 50 Hz, aplica límites
    def play_emote(self, motion_id: int, area: int, t_dispatch: float) -> None: ...  # cinemático por CSV (sim y real vía URS)
    def point(self, direction: str, t_stroke: float, hold_s: float = 1.5) -> None: ...
    def walk_to(self, area: str) -> None: ...   # bloquea gestos mientras camina
    def set_velocity(self, vx: float, wz: float) -> None: ...
    def dispatch_d1(self, area: str) -> None: ...  # stub (API D1 desconocida)
    def stop(self) -> None: ...; def estop(self) -> None: ...
```
Implementaciones: `ShowBackend` (motor show/ del reto 2), `TwinBackend` (gemelo oficial), `AimdkBackend` (robot real; `dry_run=True` por defecto).
```python
# hri_runtime/asr/__init__.py (T3)
class Asr: def transcribe(self, wav_path_or_pcm, sr) -> dict  # {"text","t_start","t_end","latency_s"}
# hri_runtime/confirm/__init__.py (T2)
def confirm_identity(face_result: dict, qr_image=None, cedula=None) -> dict  # {"person_id","name","rank","worker_subtype","work_order":{"area","task"},"status"}
# hri_runtime/motion_online/__init__.py (T4)
class MotionServer: def start_turn(self, wav_pcm_stream, sr, t_audio0) -> Iterator[(t, arms14, waist3, head2)]
```

## WAV de validación (`~/Downloads`, orden por hora)
WAV1 `WhatsApp Ptt 2026-10-23 at 9.24.00 PM.ogg` = pregunta ⇒ responde sin guiar · WAV2 9.24.35 = coherencia de restricciones · WAV3 9.24.50 = responder preguntas · WAV4 9.26.07 = misma intención que WAV3 · WAV5 9.26.26 = restricciones de WORKER · WAV6 9.26.52 = restricciones VIP/REGULAR/GUEST.

## Recursos
PC: 22 GB RAM, 12 GB VRAM, 2 GB swap. Corren juntos: x2-llm (~5.5 GB), ASR (≤1.5 GB), motion server/brain (~4 GB), show (~2.5 GB). Revisar `free -m` antes de cargar modelos; no usar bucles `while pgrep -f`.
