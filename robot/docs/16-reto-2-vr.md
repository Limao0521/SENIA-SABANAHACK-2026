# Reto 2 con gafas: el visitante entra al Club N con las Meta Quest 3

Quien lleva las gafas **es el visitante**: aparece fuera del club, entra con los joysticks, llega al mostrador y el
X2 Ultra-N lo recibe, conversa con él y lo guía. El público lo sigue en el PC con la vista «Huésped: detrás». Antes
de ponerse las gafas, la persona hace el **check-in** en la webcam del PC (reconocimiento facial UniFace y el QR de
su credencial), así el robot la saluda por su nombre y según su rango, y sabe a qué espacios del club puede pasar.

```bash
scripts/run_club_vr.sh --hri            # check-in con cara + QR y la recepción con la máquina de estados de Luis
scripts/run_club_vr.sh --hri --sin-qr   # el rango sale solo de la cara (identidad simulada)
scripts/run_club_vr.sh                  # sin cámara: la categoría se elige en el panel
```

`run_club_vr.sh` es `run_club.sh --vr`. Admite las demás opciones de `run_club.sh` y además:

| Opción | Qué hace |
|---|---|
| `--vr-puerto 8443` | puerto HTTPS de la página de las gafas |
| `--vr-api-puerto 8766` | puerto de la API local (`ws://127.0.0.1`) para la conversación |
| `--vr-micro defecto\|virtual\|no` | micrófono de las gafas en el PC: predeterminado (por defecto), solo fuente virtual, o nada |
| `--vr-grabar carpeta/` | guarda en WAV todo lo que capta el micrófono de las gafas |
| `--vr-recepcion hri\|hri-qwen\|guion\|conversacion` | cómo atiende el robot (ver [Recepción](#recepción-máquina-de-estados-hri-de-luis)) |
| `--sin-qr` | recepción `hri` sin credencial QR: basta la cara |

## Cómo funciona

```
 Quest 3 (navegador, WebXR, three.js)                PC (ventana del club, MuJoCo)
 ┌───────────────────────────────────┐   HTTPS   ┌──────────────────────────────────────┐
 │ escena: scene.json + scene.bin    │◄──────────│ vr/export.py: la escena en mallas    │
 │ dibuja a 72-90 Hz, cabeza local   │  wss /ws  │ vr/server.py                         │
 │ joysticks → paso + colisiones     │──pose────►│   huésped (show/guest.py, follow)    │
 │ micrófono 16 kHz                  │──PCM─────►│   → micrófono virtual x2_quest_mic   │
 │ subtítulos y voz del robot        │◄──────────│   ← estado 50 Hz, subtítulos, WAV    │
 └───────────────────────────────────┘           │ vr/bridge.py: modo huésped, check-in │
                                                 └──────────────────────────────────────┘
```

- **Las gafas dibujan la escena ellas mismas.** No se manda vídeo del simulador: con vídeo, la latencia de red en el
  giro de cabeza marea. La escena se exporta una vez (unos 206 mil triángulos: las mallas del robot se simplifican
  de 1,5 millones). Después el PC solo manda a 50 Hz la pose de los cuerpos que se mueven (el robot y las puertas),
  y las gafas la interpolan con 60 ms de colchón.
- **El huésped lo mueven las gafas.** El joystick calcula el paso y las colisiones en las propias gafas, con el
  mismo mapa de ocupación, puertas y distancia al robot que `show/guest.py`. Andar no tiene latencia. La pose se
  envía al PC (30 Hz) y el muñeco del huésped la sigue (`Guest.follow`). El robot lo ve llegar, lo mira y lo espera
  igual que en el modo huésped con teclado.
- **Solo se anda con los joysticks.** Moverse por la habitación no desplaza al visitante: la cabeza puede salirse
  15 cm (para inclinarse), y si se va más lejos el mundo la acompaña. La altura de los ojos es fija (1,62 m;
  `?ojos=1.70` en la URL la cambia).
- Al conectarse unas gafas, la ventana entra sola en modo huésped (la escena vuelve al principio) y pasa a la vista
  «Huésped: detrás» para el público.

## Conectar las gafas

1. Arrancar `scripts/run_club_vr.sh`. En el panel «Gafas VR» y en la terminal aparece la dirección, p. ej.
   `https://172.21.17.56:8443/`.
2. En las Quest, abrir el **navegador** y esa dirección. El certificado es autofirmado: «Avanzado → Continuar». Se
   guarda en `~/.local/share/x2_ultra/vr_cert`, así que solo hay que aceptarlo una vez por IP.
3. Esperar «Escena lista» y «Conectado», y pulsar **Entrar en VR**. El navegador pide permiso para el micrófono:
   aceptar.

Las Quest y el PC tienen que estar en la misma red y el cortafuegos del PC tiene que dejar entrar el puerto. Este
PC tiene `ufw` activo, así que la primera vez hay que abrirlo con `sudo ufw allow 8443/tcp`. Si la red del evento
aísla a los clientes entre sí (pasa en muchas Wi-Fi de eventos), se usa **cable USB**:

```bash
adb reverse tcp:8443 tcp:8443       # con las Quest en modo desarrollador y conectadas por USB
# y en el navegador de las gafas: https://localhost:8443/
```

Por USB no hace falta abrir el cortafuegos.

**Ver en pantalla** (en la misma página) sirve para probar sin gafas desde cualquier navegador: W/A/S/D andar,
Mayús correr, arrastrar el ratón para mirar y Espacio como gatillo.

## Controles (mandos Touch)

| Mando | Acción |
|---|---|
| Joystick izquierdo | andar adelante/atrás y de lado, hacia donde se mira |
| Gatillo lateral izquierdo (o pulsar el joystick) | andar rápido |
| Joystick derecho izq./der. | girar 30° de golpe (`?giro=45` en la URL lo cambia) |
| Gatillo (cualquiera) | «estoy hablando»: marca el turno para la conversación (el micrófono va siempre abierto) |
| Mantener B o Y 1,5 s | volver a empezar la visita (escena al principio) |

El punto del mando izquierdo se pone verde con el gatillo y crece con el nivel del micrófono. Los subtítulos del
robot flotan delante, algo por debajo de la mirada, con el estado debajo (check-in y recorrido).

## Check-in con reconocimiento facial

Con las gafas puestas, la webcam no puede reconocer a nadie: el visor tapa ojos y frente, que es lo que usa el
reconocimiento. Las Quest 3 tampoco tienen cámaras hacia la cara. Por eso el reconocimiento se hace **antes**, como
en la entrada de un club:

1. La persona, sin gafas, se pone delante de la webcam del PC. Face (`--hri`, UniFace) confirma una sola cara. El
   panel «Gafas VR» muestra `Check-in: Nombre · categoría` y las gafas lo enseñan bajo los subtítulos.
2. Se pone las gafas. Face deja de verla y avisa «ausente». Con VR, la ventana **no** borra por eso la identidad
   (`ShowWindow.keep_profile`): vale hasta que otra persona haga check-in o se pulse **Nuevo check-in**.
3. Al llegar al mostrador, el robot **le pide de viva voz el QR de su credencial**: «Hola, Sebastian. Para confirmar
   su acceso, muéstreme por favor el código QR de su credencial» (y las gafas lo indican). El visitante lo enseña a
   la webcam del PC (en el celular o impreso; con las gafas puestas, levantándolas un poco). El proceso de Face lo
   lee en los mismos cuadros (`hriego_v2/show_face_stream.py`, zxing-cpp; evento `qr`, «Face: QR leído…» en la
   terminal). Si ya lo enseñó en el check-in, no se lo pide. A los 20 s sin QR se lo recuerda.
4. Lo verifica la simulación, no la cámara (`hri_runtime.confirm`, de Luis): formatos QR01/QC1 firmados con Ed25519
   o los compactos IDV2 de la demo, y la credencial tiene que estar **vinculada a esa misma cara**
   (`confirm/qr_bindings.default.json`). Si no corresponde, pide otro una vez. El trabajador externo no trae QR: se
   le lee la cédula (simulada) y queda como WORKER, sin pedírselo.
5. Si no se confirma (cara desconocida, ningún QR en 45 s, QR inválido o de otra persona), el visitante queda
   **rechazado**: el robot no lo saluda por ningún rango, dice que no pudo confirmar su credencial y avisa a personal
   humano. No hay respaldo silencioso. Para otro intento: **Reiniciar visita**.

QR de prueba (los de Luis): `tests/fixtures/qr_vip_compacto.png` (Laura), `qr_regular_compacto.png` (Sebastian),
`qr_invitado_compacto.png` (Mario) y `qr_club_staff_compacto.png` (Luis). El servidor VR los sirve en
`https://IP-del-PC:8443/qr/` para abrirlos en el celular. Los QR firmados (QR01/QC1) necesitan la llave pública y el manifiesto de la demo (`HRI_QR_PUBLIC_KEY`,
`HRI_QR_MANIFEST`), que están en el equipo de Luis y no en este repo, y `cryptography` en el entorno.

Sin `--hri` no hay check-in: el robot da el saludo genérico y la categoría sale del panel.

Una mejora posible más adelante: recortar la foto de la cara en el check-in, ponerla en la cara del muñeco del
huésped y pasar a Face la cámara de la cabeza del robot simulado. Así el robot «la reconocería con sus propios ojos»,
con el mismo flujo que tendría el robot real. Técnicamente reconocería una foto: Face no tiene detección de vida
(ver [conclusiones_face.md](conclusiones_face.md)).

## Recepción: máquina de estados HRI de Luis

Con `--vr-recepcion hri` (por defecto), la recepción la conduce la **FSM del runtime HRI de Luis**
(`src/x2_ultra/hri_runtime/`, ver [reto-2/README_HRI_CONVERSACIONAL.md](reto-2/README_HRI_CONVERSACIONAL.md) y
[reto-2/HRI_RUNTIME_SPEC.md](reto-2/HRI_RUNTIME_SPEC.md)), puesta en el club 3D por `vr/fsm_visit.py`:

```
LLEGADA → CARA → CONFIRMACION (QR / cédula) → GREETING → CONVERSATION (bucle: micro de las gafas → Whisper → FSM)
        → [GUIDANCE_OFFER → sí / no] → acompañamiento o indicación → FAREWELL
```

1. **Saludo** según el rango, en vivo: el LLM local (`x2-llm`, contrato B de Luis) elige la frase y el gesto del
   catálogo (GREETING_VIP, GREETING_REGULAR…) con la personalidad de cada rango (mayordomo para el VIP, «tú» y
   «¡dame esos cinco!» para el personal) y Supertonic lo dice. El robot atiende desde su puesto, sin dar un paso.
2. **Conversación**. El robot escucha («Te escucho…» en las gafas), Whisper transcribe y la FSM decide el acto:
   - **Pregunta** («¿a qué hora abre el gimnasio?»): responde con la biblioteca de espacios
     (`hri_runtime/club_kb.json`: 15 espacios con piso, horario y descripción, y 6 servicios), sin guiar.
   - **Pide un destino**: mira la **tabla de accesos** por rango (`hri_runtime/access.py`). Si no puede pasar, niega
     con la cabeza (NEGATION_NORMAL) y le propone una alternativa permitida; la conversación sigue.
   - Si puede, según quién acompaña (`show.guide.ESCORT`): al VIP le **ofrece** acompañarlo él («¿Desea que le
     acompañe?»); a socios e invitados, el perro guía D1. Con un sí, acompañan; con un no, el robot señala hacia la
     ruta (POINT) y se despide. Al personal solo le señala hacia dónde ir.
   - **Despedida** («gracias, adiós»): se despide (FAREWELL_WAVE; SOCIAL_HEART al VIP).
   - Sin voz durante 12 s pregunta «¿En qué más puedo ayudarle?»; a la tercera, se despide.
3. Al **trabajador externo** (y al rechazado) lo saluda y le dice que avisará a personal humano del club, sin
   conversación ni acompañamiento (regla del club; la especificación de Luis le señalaría su orden de trabajo).
4. Tras acompañar (o tras la indicación en el mostrador, con el D1), la despedida de la FSM y el robot vuelve a su
   puesto. Ver [docs/11 §3](11-reto-2.md#3-la-recepción-guiada-quién-acompaña-a-cada-visitante).

### Identidad del Club N: sentido de pertenencia

La meta de la recepción es que **cualquiera**, sea cual sea su rango, se sienta parte del club. El rango decide los
privilegios (accesos, quién acompaña); la pertenencia es igual para todos. La identidad se tomó del club social y
empresarial en el que se inspira el club 3D (sus textos institucionales), manteniendo el nombre **Club N** y sin
datos que lo identifiquen:

- **Esencia**: el club social y empresarial de Bogotá, lugar de encuentro de familias, empresas e ideas.
- **Anfitriones**: el X2 se llama **Alfred** y el perro guía AgiBot D1, **Draco**. Alfred se presenta al pedir el
  QR; si le preguntan cómo se llama él o el perro contesta con una frase fija (`fsm_visit.WHO`) sin pasar por el
  LLM, y al perro siempre lo llama «Draco» (`club_kb.json` → `identidad.anfitriones`).
- **Lema**: «Club N, nuestro lugar de encuentro.» (pantalla del mostrador, placa del vestíbulo, saludo y despedida).
- **Propósito**: experiencias únicas y memorables; lo mejor del pensamiento, la cultura, el arte y el deporte.
- **Valores**: servicio y excelencia, comunidad, resiliencia y unidad, pensamiento y cultura, bienestar (placa
  «NUESTROS VALORES» en la pared norte del vestíbulo).
- **Voz**: cortesía bogotana, cálida y serena; siempre «nuestro»; nunca palabras que excluyan. La formalidad cambia
  con cada persona (usted y título al VIP, tú al personal), la calidez no.

Dónde vive: `hri_runtime/club_kb.json` → `identidad` (con una línea de pertenencia por rango) y `personas.py`
(`identity_text`, en la tarjeta de **todos** los rangos, más dos reglas globales). El contrato B pide pertenencia en
el saludo y la despedida y que la negación de acceso empiece por lo que sí puede vivir (las alternativas de
`access.py` proponen primero una experiencia, no la recepción). Las frases fijas (pedir el QR, el rechazo, el aviso a
personal, las de respaldo de la FSM) hablan igual: «Queremos recibirle bien: un compañero de nuestro equipo le ayuda
ahora a completar su registro», «Gracias por cuidar nuestra casa». El trabajador externo ya no recibe un trato frío
de trámite. Fuera del saludo y la despedida se quita el lema si el LLM lo repite. Tests en `tests/test_vr_fsm.py`:
identidad en la tarjeta de cada rango, pertenencia en saludo y despedida de todos, y ninguna frase fija que excluya.

**Los 15 espacios en el club 3D** (`show/spaces.py`). La KB manda (nombres, pisos, horarios, accesos); el club 3D
solo pone el camino. El directorio del vestíbulo de ascensores lista los pisos de la KB:

| Ruta en el club 3D | Espacios | Señala |
|---|---|---|
| pasillo y ascensores (el ascensor llega al piso de la KB; el 1 es el propio vestíbulo, con La Cava) | restaurantes, gastroworking, biblioteca (2) · convenciones, zona infantil (3) · salas de juntas, oficinas (4) · gimnasio, piscina y spa (5) · habitaciones (6) · La Cava (1) · estacionamientos (S2) | izquierda |
| puerta SOLO PERSONAL (escalera al sótano) | cuartos técnicos (S1) | derecha |
| ACCESO DE SERVICIO | almacenes de logística (S1) | al frente |
| aquí mismo | recepción y vestíbulos | al frente |

La dirección a la que apunta el robot sale de esa ruta, no de la `direccion` de la KB, y es la misma que la FSM le
da al LLM (`spaces.kb()`); así gesto, frase y camino coinciden.

**Lo que se corrige del LLM** (sus límites están en [RETOS_INTEGRACION_HRI.md](reto-2/RETOS_INTEGRACION_HRI.md)):
la frase del «sí» es fija y nombra el destino real («Con mucho gusto. Le acompaño hasta…»), porque el LLM a veces
nombraba otro; si la oferta habla de otro espacio que el pedido, se rehace con la ficha de la KB, y siempre termina
con la pregunta. Las frases de respaldo de la FSM dicen el nombre del espacio, no su id.

Cada turno tarda unos 1-3,5 s con `x2-llm` en la GPU (la oferta hace dos llamadas) más el audio.

Los otros modos: `--vr-recepcion hri-qwen` es la recepción conversacional anterior (saludo y despedida pregrabados
de Luis y en medio Qwen con `show/conversation.py`); `guion`, la visita pregrabada completa de Luis (antes de cada
diálogo, las gafas muestran la frase que debe decir el visitante), y `conversacion`, la conversación libre de la
ventana. Si la GPU del portátil se queda atascada en P8/180 MHz (LLM a 0,5-5 tok/s y «timed out»), reiniciar el PC
la arregla; mientras tanto sirve el contenedor en CPU (`x2-llm-cpu`, puerto 8081), elegido con `HRI_LLM_URL` (y
`X2_QWEN_URL` para `hri-qwen`).

## Audio para la conversación

**Micrófono de las gafas → PC.** Las gafas mandan su micrófono (con cancelación de eco, 16 kHz, mono) por el
WebSocket. El PC crea con PipeWire una fuente virtual, **«Micrófono Quest 3 (club VR)»** (nodo `x2_quest_mic`), y
mientras dura la sesión la pone como **micrófono predeterminado**. Al cerrar la ventana vuelve el que había. Así,
cualquier programa que grabe del micrófono oye a quien lleva las gafas sin cambiar nada: `pw-record` (lo que usa la
conversación de `show/conversation.py`), Whisper, el navegador… Para grabar de ella explícitamente:
`pw-record --target x2_quest_mic --rate 16000 --channels 1 voz.wav`. Si no llega audio, la fuente da silencio; si
se acumula retraso, se tiran las tramas viejas.

**Voz del robot → gafas.** Lo que dice el robot (`engine.say`) llega a las gafas como subtítulo y, si la casilla
«Voz» está marcada, como audio: espeak-ng aparte, en WAV. Los altavoces del PC siguen con la voz de la ventana para el
público.

**API local** para quien haga la conversación en otro proceso: `ws://127.0.0.1:8766` (solo desde el PC, sin TLS).

| Dirección | Mensaje |
|---|---|
| recibe, binario | PCM s16le 16 kHz mono del micrófono de las gafas, en tramas de 20 ms (640 bytes) |
| recibe, JSON | `{"t": "hello", ...}` al conectar; `{"t": "ptt", "down": true/false}` gatillo; `{"t": "quest", "connected": n}`; `{"t": "speech", "text": ...}` lo que dice el robot (para no transcribir su propia voz) |
| envía, JSON | `{"t": "say", "text": "..."}`: el robot lo dice (subtítulos en PC y gafas, voz) |
| envía, JSON | `{"t": "subtitle", "text": "..."}`: solo subtítulo |
| envía, binario | un WAV: suena en las gafas y en el PC (para una voz propia, p. ej. Supertonic) |

```python
import asyncio, json, websockets

async def main():
    async with websockets.connect("ws://127.0.0.1:8766") as ws:
        await ws.send(json.dumps({"t": "say", "text": "Hola, ¿en qué le puedo ayudar?"}))
        async for msg in ws:
            if isinstance(msg, bytes):
                pass                      # 20 ms de audio de las gafas: a la detección de voz / ASR
            else:
                print(json.loads(msg))    # ptt, quest, speech...

asyncio.run(main())
```

## Archivos

| Archivo | Qué hace |
|---|---|
| `src/x2_ultra/vr/export.py` | escena MuJoCo → mallas, materiales, texturas y luces para three.js |
| `src/x2_ultra/vr/server.py` | HTTPS + WebSocket de las gafas, estado a 50 Hz, API local, certificado |
| `src/x2_ultra/vr/mic.py` | micrófono virtual de PipeWire con el audio de las gafas |
| `src/x2_ultra/vr/bridge.py` | une el servidor con la ventana: modo huésped, check-in, subtítulos y voz, panel |
| `src/x2_ultra/vr/fsm_visit.py` | recepción con la FSM de Luis: identidad cara + QR, accesos, quién acompaña |
| `src/x2_ultra/show/spaces.py` | los 15 espacios de la KB en el club 3D (ruta, dirección, directorio de pisos) |
| `hriego_v2/show_face_stream.py` | Face en la webcam: identidades confirmadas y QR leídos (JSON por línea) |
| `src/x2_ultra/vr/conversation_visit.py` | recepción `hri-qwen` (Whisper → Qwen → Supertonic con el reproductor de Luis) |
| `src/x2_ultra/vr/hri_visit.py` | recepción HRI con el guion pregrabado de Luis; detector de que el visitante habló |
| `src/x2_ultra/vr/web/` | página WebXR (`app.js`, `mic-worklet.js`, three.js r170 incluido) |
| `src/x2_ultra/show/guest.py` | `Guest.follow` / `release`: el huésped lo mueven las gafas |
| `tests/test_vr.py` | exportación, huésped, estado del servidor, micrófono y saludo |
| `tests/test_vr_fsm.py` | identidad cara + QR, espacios del club 3D y visitas completas con la FSM |

La escena exportada se escribe en `runs/vr/` en cada arranque (unos 6 s, en segundo plano).

## Estado

Probado en el PC: escena y movimiento en un Chromium sin gafas («Ver en pantalla»), estado y pose por WebSocket,
micrófono hasta la fuente virtual y la API local, y el saludo por nombre al llegar al mostrador. **Falta probarlo con
las Quest puestas**: sesión WebXR, mapeo de los mandos, comodidad del giro y la inclinación, rendimiento a 72 Hz
(Phong con 7 luces) y el micrófono del navegador de las Quest. Si va justo de rendimiento, lo primero es quitar los
focos (SpotLight) en `app.js`.
