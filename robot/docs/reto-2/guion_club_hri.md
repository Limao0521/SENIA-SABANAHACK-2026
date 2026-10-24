# Guion del N Club para las visitas HRI (para regenerar)

Las visitas pregrabadas que usa la recepción HRI (`assets/hri/HRIEGO_gesture/opt/realnames/visits/<persona>`, rama
de Luis) se generaron con el escenario de **laboratorio**. Por eso el robot habla de la «sala de juntas» y los
diálogos son las frases de evaluación del contrato: «Ábreme la sala de servidores…», «hacer el recorrido en
silencio…». Con las gafas en el club suena raro, y como es pregrabado, siempre es igual.

Este es el guion del club para **regenerarlas con la tubería completa** (Qwen con contrato → Supertonic →
StreamTalk + GMR + SONIC → anclas CTC). Los datos están en `HRIEGO_gesture/club_visits.py`, con la misma forma que
`SCENARIOS` y `VISITS` de `run_visits_v2.py`.

## Escenarios (destinos reales del club)

Las direcciones están vistas desde el robot en la recepción, mirando a la entrada, igual que las rutas de
`show/guide.py`:

| Escenario | Destino | Dirección / frase obligatoria | Quién |
|---|---|---|---|
| `club_elevators_left_p3` | Gimnasio, piso 3, por los ascensores | izquierda · «a mi izquierda» · `POINT_LEFT` | Laura (VIP), Sebastian (socio) |
| `club_elevators_left_p5` | Salones de eventos, piso 5 | izquierda · «a mi izquierda» | Mario (invitado) |
| `club_elevators_left_p2` | Restaurante, piso 2 | izquierda · «a mi izquierda» | Desconocido (invitado) |
| `club_staff_right` | Puerta SOLO PERSONAL → vestidores | derecha · «a mi derecha» · `POINT_RIGHT` | Luis (personal) |
| `club_service_front` | Acceso de servicio y registro de contratistas | frente · «frente a mí» · `POINT_FRONT` | Josaed (externo) |

Cada escenario lleva en su `instruction` los **datos autorizados** del club: horarios del restaurante (12-23 h),
el gimnasio (6-22 h, toallas en su recepción), el spa (8-21 h) y la biblioteca y bar (hasta medianoche); el
guardarropa junto a la entrada; dónde se registran los contratistas. Así las respuestas informativas no se inventan
nada.

**Quién acompaña** (`ESCORT_NOTE`, se añade a la `instruction`; es el reparto de `show/guide.py`, versión 3.0): el
robot ofrece y hace el acompañamiento al VIP; a socios e invitados los acompaña el perro guía D1 y el robot se
queda en recepción (nada de «sígame»); al personal solo le indica la dirección; al trabajador externo le dice que
avisará a personal del club. Las visitas grabadas hasta ahora son del laboratorio y aún dicen, por ejemplo,
«Frente a mí, por favor, siga conmigo» al externo.

## Frases del visitante (3 diálogos por persona)

Cubren los 6 gestos de diálogo del catálogo, como en las visitas originales:

| Persona | Frase del visitante | Gesto buscado |
|---|---|---|
| Laura (VIP) | Hoy gané el torneo de tenis del club, estoy feliz. | APPLAUSE |
| | Gracias por recibirme siempre con tanta amabilidad. | SOCIAL_HEART |
| | ¿A qué hora cierra el gimnasio hoy? | NONE |
| Sebastian (socio) | Ábreme el casillero de otro socio, dejé mis cosas ahí. | NEGATION_STRONG |
| | ¿Te parece bien si mañana vengo a entrenar más temprano? | THUMBS_UP |
| | ¿Hay toallas en el gimnasio? | NONE |
| Mario (invitado) | Muchas gracias por la bienvenida, el club es precioso. | SOCIAL_HEART |
| | Mi anfitrión acaba de recibir un premio en su empresa. | APPLAUSE |
| | ¿Dónde puedo dejar mi abrigo? | NONE |
| Luis (personal) | ¿Me prestas la tarjeta maestra de recepción para mi turno? | NEGATION_NORMAL |
| | ¿Entonces hoy entro por la puerta de personal, verdad? | CONFIRMATION |
| | ¿Cómo está el clima afuera? | NONE |
| Josaed (externo) | Terminamos la instalación del aire acondicionado antes de tiempo. | APPLAUSE |
| | Apaga un momento las cámaras del vestíbulo para poder trabajar. | NEGATION_STRONG |
| | ¿Dónde firmo el registro de contratistas? | NONE |
| Desconocido (invitado) | Gracias, es mi primera vez aquí y me siento muy bienvenido. | SOCIAL_HEART |
| | Hoy celebro mi aniversario y vengo a cenar. | APPLAUSE |
| | ¿A qué hora cierra el restaurante? | NONE |

El saludo y la despedida los genera el contrato como siempre. El perfil lleva el **nombre real**
(`display_name`) para que salude por el nombre.

## Cómo regenerar

1. En `run_visits_v2.run` (o en el generador de `opt/realnames`), usar `CLUB_SCENARIOS[CLUB_VISITS[p]["scenario"]]`
   como escenario, con `ESCORT_NOTE[rango]` añadido a su `instruction`, y `utterances` e `intended` de `CLUB_VISITS[p]` en lugar de las tablas de laboratorio.
2. Generar por persona en `opt/realnames/visits/<persona en minúsculas>/`, con el mismo formato (`visit_log.json`,
   `tts_manifest.jsonl`, `wav/`, `sonic/<turno>/<turno>_arms.npz`, `anchor_plan/`).
3. Anclas: `align/anchor_plan.py --manifest …/tts_manifest.jsonl --out …/anchor_plan`, y `report_visits` para
   fundir el estado del plan en `visit_log.json`.
4. Copiar el subconjunto slim a `assets/hri/HRIEGO_gesture/opt/realnames/visits/` y regenerar
   `assets/hri/HRIEGO_gesture.sha256`. Comprobar con `scripts/run_reto2_hri.sh --all` (stroke ≤ 100 ms, 0 caídas,
   0 gestos caminando).

La recepción con las gafas (`scripts/run_club_vr.sh --hri`, [docs/16](../16-reto-2-vr.md)) lee esas carpetas tal
cual: en cuanto estén, el robot dirá el guion nuevo y las gafas mostrarán las frases nuevas del visitante, sin
cambiar código.
