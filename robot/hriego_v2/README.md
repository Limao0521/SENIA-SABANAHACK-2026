# HRIEGO v2 · recepción con Qwen GPU y voz M1 en CPU

HRIEGO v2 une el reconocimiento facial existente de `Face/`, la SQLite
institucional, el Context Manager y la FSM de HRIEGO v1 con el contenedor
`x2-llm` y Supertonic 3. La cámara ocupa la mitad izquierda de la ventana;
perfil, contexto, estados y texto generado aparecen a la derecha. El audio se
reproduce por PipeWire (`pw-play`) o ALSA (`aplay`). Las intenciones motoras se
validan y registran, **sin ejecutar movimientos físicos**.

## Plataforma y dependencias

- Linux x86_64 con sesión gráfica y webcam V4L2 accesible.
- GPU NVIDIA con controlador, `nvidia-smi`, Docker Engine, Docker Compose v2 y
  NVIDIA Container Toolkit. El usuario necesita acceso al daemon Docker y al
  dispositivo de cámara.
- Python 3.12 con `venv` y `pip`; `curl`; `pw-play` o `aplay`.
- Al menos 5 GiB de RAM disponible antes de cargar el TTS y 1 GiB de VRAM
  libre. El guardián interrumpe el worker si la RAM baja de 2 GiB o la VRAM
  libre baja de 1 GiB. El equipo probado tenía 24 GiB de RAM y 12 GiB de VRAM.
- Red para la primera instalación: Qwen3-4B Q4_K_M (~2,5–3 GB), modelos ONNX
  de UniFace (~17 MB), Supertonic 3 (~386 MB) y las imágenes/paquetes Docker y
  Python. Los pesos se descargan y no se guardan en Git.

Las dependencias Python de cámara están fijadas en
`Face/requirements/live-gpu-cu130.txt`. El worker de voz usa
`hriego_v2/requirements-supertonic.txt` (`supertonic==1.3.1`, ONNX Runtime CPU).
El contenedor usa `llm/docker-compose.yml` y `llm/compose.x86-cuda.yml`.

## Instalación desde un clon de esta rama

```bash
git clone --branch 'Facial,-LLM-and-Gesture-integration' \
  https://github.com/juanulloa2050/x2_ultra.git
cd x2_ultra
./scripts/setup_hriego_v2.sh
```

El instalador crea dos entornos virtuales, instala los paquetes, descarga y
verifica los modelos de UniFace, descarga Supertonic, verifica el SHA-256 del
GGUF, construye/arranca `x2-llm` con GPU y ejecuta el preflight. Es
idempotente. Si el contenedor ya está listo, puede ejecutarse con
`--skip-docker`. No se necesita el repositorio `Laplace`: el contrato RCP-01c
auditado está versionado en `hriego_v2/contract_assets/` y se comprueba por
SHA-256 al iniciar.

La base `Face/data/institutional_identity.sqlite3` está incluida en la rama
por solicitud expresa del proyecto. Contiene los cinco perfiles y sus
plantillas biométricas reales. El instalador ajusta sus permisos a `0600`;
controle el acceso al repositorio y a cualquier copia de la base. Una persona
que no esté registrada puede abrir la ventana, pero no iniciará un ciclo
autónomo hasta que se añada su identidad a la galería.

## Ejecutar

```bash
./scripts/run_hriego_v2.sh --check-only
./scripts/run_hriego_v2.sh
```

El segundo comando abre la ventana dividida, precarga Supertonic M1 en un
worker persistente y usa el `LocalLLM` del contenedor `x2-llm`, cuyo
`llama-server` mantiene Qwen3-4B Q4_K_M residente en GPU. Ante una única cara
registrada, la FSM recorre `IDLE → GREETING → GUIDANCE → FAREWELL → IDLE`.
Cerrar con `q`, `Esc` o la ventana; `c` cancela la visita. Para otra webcam:

```bash
HRIEGO_CAMERA_INDEX=1 ./scripts/run_hriego_v2.sh
```

Supertonic usa voz M1 en español con velocidades 1.05, 1.0 y 0.98 para
bienvenida, orientación y despedida. Estas velocidades no equivalen a las
instrucciones prosódicas libres de Qwen3-TTS. La prueba local del 21 de
octubre de 2026 completó los tres estados en 28,8 s; Qwen tardó
1,86/2,77/1,85 s y Supertonic 0,98/2,26/0,72 s en las frases de ese ciclo.
La calidad de la voz M1 fue aceptada por el usuario durante la prueba.

## Componentes y datos

| Componente | Ubicación | Función |
| --- | --- | --- |
| UniFace + BYTETracker | `Face/` | Detección, identidad y seguimiento |
| Registro institucional | `Face/data/institutional_identity.sqlite3` | Perfiles y cinco rangos |
| Context Manager/FSM | `hriego/`, `hriego_v2/` | Política, contexto y estados |
| Qwen cuantizado | `llm/` | `LocalLLM → llama-server` en GPU |
| Supertonic M1 | `hriego_v2/supertonic_worker.py` | Síntesis ONNX en CPU |
| Contrato RCP-01c | `hriego_v2/contract_assets/` | Validación de discurso e intención |

La memoria de conversación de HRIEGO v2 se guarda en
`llm/data/hriego_v2_conversations.sqlite3`. El diario de eventos y los WAV
quedan en `hriego_v2/data/`; esas salidas no se versionan. La SQLite de memoria
del contenedor (`llm/data/agent.db`) es independiente. El RAG documental de
HRIEGO conserva la configuración de v1: deshabilitado por defecto, aunque el
servicio `x2-llm` mantiene su propia API de RAG.

## Backend de voz anterior

Qwen3-TTS CustomVoice con Aiden y mezcla Talker INT8/INT4 + Code Predictor
INT4 sigue seleccionable:

```bash
HRIEGO_V2_TTS_BACKEND=qwen3 ./scripts/run_hriego_v2.sh
```

Este backend opcional necesita además el binario del experimento
`experiments/hriego_v2_sprint_20261021/backend-source/` y el modelo en
`Laplace/voice/models/Qwen3-TTS-12Hz-1.7B-CustomVoice`. No forman parte del
instalador reproducible de Supertonic. La opción predeterminada y probada
desde este clon es M1.
