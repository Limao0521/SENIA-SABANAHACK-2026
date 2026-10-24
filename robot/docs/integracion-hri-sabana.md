# Face, LLM y gestos en los retos de Sabana Hack

La integración es opcional (`--hri`). Los escenarios y recorridos manuales siguen disponibles sin cámara ni LLM.

## Arranque

1. Instale los recursos de HRIEGO v2 con `./scripts/setup_hriego_v2.sh`. Esto prepara el entorno Face, los modelos de reconocimiento y el contenedor `x2-llm` con Qwen3-4B. La CLI de Qwen usada para programar **no** sustituye ese contenedor de ejecución.
2. Reto 1: `scripts/run_show.sh --hri`. Pulse **Show completo (19 parlamentos)** para reproducir el guion comercial de las series X, D, C y G con los WAV y los brazos sincronizados de `HRIEGO_gesture/twin/replay/commercial/`. Solo gesticula el robot que habla; el otro tiene los brazos en reposo. Al acabar el último parlamento, ambos inician automáticamente **Tai Chi estilo Chen**. Al terminar el baile, el Ultra-N da las gracias con la pista de voz comercial y ambos saludan con la mano antes de volver a reposo. El material original asigna el segundo papel al X2 Original; en este escenario lo representa el X2 EDU, que comparte los 14 nombres de articulaciones de brazo. **Bienvenida nueva con LLM y gesto** conserva la opción de generar una frase nueva con Qwen. El show completo también está disponible sin `--hri` porque usa pistas ya preparadas.
3. Reto 2: `scripts/run_club.sh --hri`. Face confirma la categoría, pero el robot espera a preguntar al visitante adónde va. Pulse **Entrar como huésped** y acérquese al mostrador, o **Iniciar conversación**; el micrófono se abre al terminar la pregunta. **F** permite terminar la respuesta antes, y también puede escribirla. Qwen responde y el recorrido comienza solo cuando identifica un destino configurado. VIP, socio e invitado van al ascensor correspondiente; personal va a vestidores; externo va al acceso de servicio.

La cámara se abre en un proceso Face independiente en el reto 2. Whisper transcribe el micrófono en un hilo y Qwen genera la respuesta de conversación en otro; el simulador no espera por ellos. El destino se valida contra el directorio antes de iniciar la guía. El botón **Recibir y guiar** conserva la demo manual. La bienvenida nueva del reto 1 conserva el contrato `rcp01c.intervention.v2`.

## Alcance de la unión

| Parte | Uso en la demo |
| --- | --- |
| `Face/` | Webcam, identidad confirmada y rango; se exige una sola cara reconocida. |
| `llm/` y `HRIEGO_gesture/` | Qwen local responde al visitante del reto 2; el contrato de gestos sigue disponible para las intervenciones del reto 1. |
| `src/x2_ultra/show/` | Mantiene la decisión de ruta, locomoción y puertas; aplica frases y poses de brazos al simulador. |

El visitante indica el piso por voz. La categoría confirmada por Face o elegida por el operador limita qué ruta se puede iniciar; la frase hablada no concede acceso a puertas. En el reto 1, el show comercial reproduce audio IndexTTS y objetivos de brazos SONIC ya calculados. La conversación del reto 2 usa `espeak-ng` y una pose de saludo; todavía no usa el audio Supertonic ni la alineación CTC de `HRIEGO_gesture`. No se envían órdenes al robot físico.

En el reto 2, antes de cada tramo de caminata el Ultra-N manda ambos brazos a reposo y espera a que las articulaciones medidas lleguen a esa posición. Si no llegan en cuatro segundos, la visita se detiene sin iniciar la marcha.
