# Reto 2 · La demo que queremos

Documento de objetivo: qué tiene que verse en la demo del reto 2 (N Club, [enunciado](reto2.jpeg)) y qué falta
para llegar. El estado de la simulación está en [docs/11](../11-reto-2.md).

## La demo en una frase

Una persona se sienta delante del PC: **la cámara del PC la reconoce** y, en la simulación, **ella es el huésped que
entra al club**. Lo maneja con el teclado; el X2 Ultra-N la ve llegar al mostrador, la saluda según quién es, la
acompaña por el pasillo hasta el ascensor y le dice a qué piso subir, mientras ella camina detrás del robot.

## Guion (lo que ve el público)

1. **Llegada.** En pantalla, el huésped está fuera del club, frente a las puertas de vidrio (vista detrás del
   huésped). La persona pulsa W y entra; las puertas se abren solas. Al fondo, el mostrador con el robot.
2. **El robot la ve.** Cuando la persona se acerca, el robot la sigue con la cabeza; al tenerla delante del mostrador
   (≈2,5 m) empieza la recepción.
3. **Reconocimiento facial.** La **webcam del PC** toma la cara de la persona real; el sistema de reconocimiento
   facial del club dice si es socio (y quién) o no.
4. **Confirmación de la categoría** (requisito del reto): con información adicional o interacción, el robot fija una
   de las cinco categorías. Ideas: socio VIP o socio = el reconocimiento ya basta, se saluda por el nombre; invitado =
   nombre del socio anfitrión o código de invitación; personal = carné; trabajador externo = empresa y orden de
   trabajo.
5. **Saludo y destino según la categoría**, con gesto, voz y subtítulo: «¡Bienvenido de nuevo, señor Pérez! Su mesa le
   espera en el restaurante, piso 2».
6. **Guía.** «Acompáñeme, por favor»: el robot va por el pasillo y la persona lo sigue; si se queda atrás, el robot se
   para y le dice «Por aquí, por favor».
7. **El ascensor.** El robot se gira, señala el ascensor que toca y dice el piso; las puertas se abren y la persona
   entra. El robot vuelve a la recepción a esperar al siguiente.

Un recorrido distinto para cada una de las cinco categorías (requisito del reto). Hay **tres rutas**: socios, VIP e
invitados suben a los pisos del club (ascensores), el personal va a la puerta de personal (vestidores) y los
trabajadores externos al acceso de servicio. Cómo se confirma cada categoría y qué cambia:

| Categoría | Cómo se confirma | Saludo | Destino |
|---|---|---|---|
| Socio VIP | reconocimiento facial (socio) + nivel en la base de socios | por el nombre, trato preferente | ruta 1: ascensores, al piso que prefiera |
| Socio | reconocimiento facial (socio) | por el nombre | ruta 1: ascensores, al piso que pida (restaurante, gimnasio…) |
| Invitado | no socio + nombre del anfitrión o código de invitación | bienvenida al club, menciona al anfitrión | ruta 1: ascensores, al piso donde lo espera el anfitrión |
| Personal del club | no socio en la base de socios + carné / lista de turnos | breve, de compañero | ruta 2: puerta SOLO PERSONAL (vestidores) |
| Trabajador externo | no socio + empresa y orden de trabajo del día | indicaciones de seguridad | ruta 3: ACCESO DE SERVICIO, con registro de contratistas |

## Qué hay ya en la simulación

- El club (recepción al estilo hotel, pasillo, vestíbulo de ascensores con directorio de pisos), el X2 Ultra-N y las
  puertas automáticas.
- **Modo huésped** (tecla M en `scripts/run_club.sh`): la persona se maneja con W/S andar, A/D girar, Q/E de lado,
  Mayús correr; en primera persona (o desde atrás); no atraviesa paredes, muebles, puertas cerradas ni al
  robot.
- El robot espera mirando al huésped, lo detecta delante del mostrador, saluda, dice el destino, lo guía (y lo espera
  si se queda atrás), señala el ascensor y dice el piso, y vuelve. Voz con `espeak-ng` y subtítulos.
- Las tres rutas y los cinco guiones (saludo y cierre por categoría); el tipo de visitante y el piso se eligen a
  mano en el panel («Recepción guiada»).
- **Con las Meta Quest 3** ([docs/16](../16-reto-2-vr.md)): la persona hace el check-in facial en la webcam del PC
  antes de ponerse las gafas (con el visor puesto la cámara ya no la reconoce), entra al club con los joysticks y
  el robot la saluda por su nombre; el micrófono de las gafas es el micrófono del PC durante la sesión.

## Qué falta

- **Reconocimiento facial con la cámara del PC** (no implementado a propósito en esta fase). En el código, el sitio
  donde va es el paso `Pause(1.5, "reconocimiento facial (simulado)…")` de `club_tour` en `src/x2_ultra/show/guide.py`;
  se sustituirá por un paso `Recognize` que llame a algo como:

  ```python
  class FaceRecognizer:                      # adaptador del sistema del club (o uno propio con la webcam)
      def identify(self, frame) -> Identity: ...

  @dataclass
  class Identity:
      member: bool                           # socio / no socio (lo que da el sistema del club)
      member_id: str | None
      name: str | None
      confidence: float
  ```

  y con su resultado elija el saludo y el destino en vez del panel.
- **Confirmación de la categoría** (voz, QR/carné o pantalla) que sustituya a la elección en el panel.
- Pasar el recorrido al **robot real**: voz (`PlayTts` de AimDK), gestos (`SetMcPresetMotion`) y marcha con la
  locomoción del MC (`scripts/robot_real.sh` ya maneja la marcha y los gestos, [docs/09](../09-robot-real.md)).
