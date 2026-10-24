# LLM local · memoria + RAG — contenedor único para el X2 Ultra

Inferencia LLM **100 % local** en un solo contenedor Docker, pensada para correr
**a bordo del robot** (Jetson Orin NX 16 GB) o en un PC/servidor. Extraída y
ampliada a partir de [`linksoul-local`](../..) (el `llm-server` probado allí),
**sin ninguna dependencia de la plataforma LinkSoul/AgiBot**: esto es solo el
cerebro de texto, con ventana de contexto amplia, memoria de conversación y
RAG sobre la documentación del repo. Los otros módulos del proyecto
(simulación, gemelo, control real) quedan fuera; otros procesos del robot
consumen este servicio por HTTP.

```
┌──────────────────────── contenedor x2-ultra/llm ────────────────────────┐
│                                                                         │
│  llama-server (llama.cpp)            API FastAPI (app/)                 │
│  Qwen3-4B Q4_K_M · GPU/CPU           memoria + RAG + orquestación       │
│  :8080  /v1/chat/completions ◄────── :8000 /chat (SSE)                  │
│  (API cruda compatible OpenAI)       /sessions /rag /facts /metrics     │
│                                                                         │
│  volúmenes:  ./models (GGUF) · ./data (SQLite + embeddings)             │
│              ./persona (editable) · ../docs → /corpus (RAG)             │
└─────────────────────────────────────────────────────────────────────────┘
```

## Qué incluye

- **Ventana de contexto amplia**: 16 384 tokens por defecto en Jetson
  (32 768 en GPU de escritorio; Qwen3-4B los soporta nativos). Flash
  Attention + KV cache `q8_0` mantienen el coste de memoria bajo
  (~1,2 GB de KV por cada 16 K). `PARALLEL=1`: toda la ventana es para la
  conversación activa (llama-server reparte el contexto entre slots).
- **Memoria de conversación** (SQLite en `./data`, sobrevive reinicios):
  historial por sesión + **resumen rodante**: al superar
  `HISTORY_MAX_MESSAGES` los turnos antiguos se comprimen en un resumen
  anclado tras el system prompt → contexto efectivo ilimitado sin reventar
  la ventana. Opcional: extracción de **hechos de largo plazo**
  (`FACT_EXTRACT=true`) recuperables por similitud.
- **RAG**: índice local (embeddings ONNX `potion-multilingual-128M` vía
  fastembed + búsqueda coseno con numpy sobre SQLite; sin servicios
  externos). Al arrancar indexa automáticamente `../docs` (montado en
  `/corpus`). El contexto recuperado se inyecta **en el último mensaje de
  usuario**, nunca tras el system prompt: el prefijo
  system+resumen+historial queda byte-idéntico entre turnos y llama-server
  reutiliza su KV cache (`--cache-reuse`) → TTFT mínimo aunque la sesión
  sea larga.
- **Latencia primero**: `enable_thinking=false` (Qwen3 responde directo,
  ahorra 1–3 s/turno), warmup del prefijo al arrancar, keep-alive HTTP,
  decodificación especulativa opcional (`DRAFT_MODEL`, +25–60 % tok/s),
  métricas por turno (TTFT, tok/s, tokens cacheados) en cada respuesta y en
  `GET /metrics`.

## Quickstart en tu PC (Windows/Linux, CPU — para validar)

```powershell
cd llm
Copy-Item .env.example .env      # Linux: cp .env.example .env
.\scripts\download-model.ps1     # Qwen3-4B Q4_K_M (~2,5 GB)
docker compose up -d --build
python scripts\probe.py          # espera /ready y mide TTFT, tok/s, KV cache y memoria
python scripts\chat.py           # chat interactivo con métricas
```

En CPU irá a ~5–13 tok/s (es solo para validar la API); usa el perfil GPU o
el Jetson para velocidad real. Para una prueba rápida puedes bajar un modelo
pequeño: `.\scripts\download-model.ps1 0.6B Q8_0` y `MODEL_PATH=/models/Qwen3-0.6B-Q8_0.gguf` en `.env`.

## Despliegue en el robot (Jetson Orin NX 16 GB)

```bash
# 1) copia el repo (o al menos llm/ + docs/) al robot
scp -r x2_ultra develop0@10.0.1.41:~/          # red del robot por cable

# 2) en el robot: modelo + clocks máximos
cd ~/x2_ultra/llm
./scripts/download-model.sh                     # Qwen3-4B Q4_K_M
sudo nvpmodel -m 0 && sudo jetson_clocks        # +15-25 % de velocidad

# 3) levanta el contenedor con el perfil Jetson
cp .env.example .env                            # ajusta si quieres
docker compose -f docker-compose.yml -f compose.jetson.yml up -d --build
# (si tu AimDK usa Podman: podman-compose -f ... -f ... up -d --build)

# 4) desde cualquier proceso del robot:
curl http://localhost:8000/ready
python3 scripts/probe.py --api http://localhost:8000 \
    --rag-pregunta "¿Qué es UPPERBODY_REMOTE_SPLIT?"
```

Rendimiento esperado en el Orin NX con Qwen3-4B Q4_K_M: **TTFT ~100–300 ms**
(prefijo cacheado) y **~35–40 tok/s** (más con `DRAFT_MODEL`). Vigila
`tegrastats` (throttle a 99 °C). ¿Necesitas más velocidad? `Qwen3-1.7B`
(~70–80 tok/s). ¿Más calidad? `Qwen3-8B Q4_K_M` cabe (~20 tok/s).

Perfil alternativo PC con GPU NVIDIA:
`docker compose -f docker-compose.yml -f compose.x86-cuda.yml up -d --build`.

## Uso desde otros procesos (dentro del robot)

```python
import json, urllib.request

req = urllib.request.Request(
    "http://localhost:8000/chat",
    data=json.dumps({"session_id": "robot1", "message": "¿Qué modos tiene el MC?"}).encode(),
    headers={"Content-Type": "application/json"})
with urllib.request.urlopen(req) as r:
    for line in r:                                  # SSE
        line = line.decode().strip()
        if line.startswith("data:"):
            ev = json.loads(line[5:])
            if ev["type"] == "delta":
                print(ev["text"], end="", flush=True)   # stream de la respuesta
            elif ev["type"] == "done":
                print(f"\n[TTFT {ev['ttft_ms']} ms · {ev.get('tok_s')} tok/s "
                      f"· {ev.get('cached_tokens')} tokens cacheados · RAG {ev['rag_contexts']}]")
```

También tienes `POST /v1/chat/completions` (proxy crudo compatible OpenAI,
sin memoria ni RAG) en `:8000`, y el llama-server directo en `:8080/v1`.

## Endpoints de la API (:8000)

| Endpoint | Descripción |
|---|---|
| `POST /chat` | Conversación con memoria + RAG. Body: `{session_id, message, stream?, max_tokens?, temperature?, use_rag?}`. SSE: eventos `delta` / `done` (con métricas) / `error`. |
| `POST /v1/chat/completions` | Proxy OpenAI crudo (sin memoria/RAG). |
| `GET /health` · `GET /ready` | Estado general · 200 solo cuando el modelo está cargado. |
| `GET /metrics` | TTFT/tok-s medios y de los últimos turnos. |
| `GET /sessions` · `GET/DELETE /sessions/{id}` · `POST /sessions/{id}/summarize` | Gestión de memoria (historial, resumen, borrado). |
| `POST /rag/ingest` `{path}` · `POST /rag/ingest_text` `{source,text}` | Indexar un directorio del contenedor (`/corpus/...`) o un texto. Idempotente por hash. |
| `GET /rag/search?q=…&k=&min_score=` · `GET /rag/stats` · `DELETE /rag` | Búsqueda, estado y vaciado del índice. |
| `GET/POST/DELETE /facts` | Memoria de largo plazo (hechos con embedding). |

Herramientas CLI (solo stdlib, corren en cualquier máquina con red al
contenedor): `scripts/probe.py` (sonda TTFT/tok-s/RAG), `scripts/chat.py`
(chat interactivo), `scripts/ingest.py` (ingesta remota, `--local-dir`).

## Configuración (`.env`)

Knobs principales — todos documentados en [`.env.example`](.env.example):

| Variable | Defecto | Efecto |
|---|---|---|
| `CTX_SIZE` | 16384 (Jetson) / 32768 (x86-cuda) / 4096 (CPU) | Ventana de contexto. KV q8_0: ~75 MB por 1 K tokens. |
| `PARALLEL` | 1 | Slots de llama-server; >1 **reparte** `CTX_SIZE` entre slots. |
| `MODEL_PATH` | `/models/Qwen3-4B-Q4_K_M.gguf` | Modelo GGUF. |
| `DRAFT_MODEL` | – | Qwen3-0.6B Q8_0 → decodificación especulativa (+25–60 % tok/s). |
| `MAX_TOKENS` / `TEMPERATURE` | 512 / 0.7 | Generación (por petición se puede sobrescribir). |
| `ENABLE_THINKING` | false | Qwen3 sin fase de razonamiento (el mayor win de latencia). |
| `HISTORY_MAX_MESSAGES` / `HISTORY_KEEP_MESSAGES` | 24 / 12 | Disparo y conservación del resumen rodante. |
| `FACT_EXTRACT` | false | Hechos de largo plazo (1 llamada LLM extra por turno, en segundo plano). |
| `RAG_TOP_K` / `RAG_MIN_SCORE` | 3 / 0.40 | Cuántos chunks y con qué similitud mínima se inyectan (calibrado con potion: relevantes ~0.42-0.75, ruido <0.35). |
| `EMBED_MODEL` | `minishlab/potion-multilingual-128M` | Embeddings ONNX (~500 MB, se descargan solos a `./data/fastembed`; 256 dims, multilingüe). |
| `RAG_AUTOINGEST` | true | Indexa `/corpus` (docs del repo) si el índice está vacío al arrancar. |
| `HF_ENDPOINT` | – | Espejo de HuggingFace si la red lo necesita (p. ej. `https://hf-mirror.com`). |

## Estructura

```
llm/
├── docker-compose.yml        # servicio único "llm" (perfil CPU por defecto)
├── compose.jetson.yml        # robot X2 Ultra (dustynv/llama_cpp, NGL=99, CTX 16K)
├── compose.x86-cuda.yml      # PC/servidor con GPU NVIDIA (CTX 32K)
├── .env.example
├── server/
│   ├── Dockerfile            # BASE_IMAGE (multi-arch) + capa Python/venv + API
│   ├── entrypoint.sh         # auto-detecta flags de llama.cpp (FA, KV q8_0, jinja…)
│   └── run.sh                # llama-server + uvicorn en el mismo contenedor
├── api/
│   ├── requirements.txt      # fastapi, uvicorn, httpx, numpy, fastembed (sin torch)
│   └── app/
│       ├── main.py           # FastAPI: /chat, proxy OpenAI, sesiones, RAG, facts
│       ├── memory.py         # SQLite: sesiones, mensajes, resumen rodante, hechos
│       ├── rag.py            # chunking, embeddings e5 (ONNX), búsqueda coseno
│       ├── llm_client.py     # cliente SSE de llama-server (warmup, keep-alive)
│       ├── prompts.py        # ensamblado con disciplina de KV cache
│       └── config.py
├── persona/persona.md        # personalidad editable sin recompilar
├── scripts/                  # download-model.(sh|ps1) · probe.py · chat.py · ingest.py
├── tests/                    # pytest: chunking, memoria, API (LLM/embedder falsos)
├── models/                   # GGUF (gitignored)
└── data/                     # SQLite + caché de embeddings (gitignored)
```

## Pruebas

```bash
# unitarias (sin Docker ni GPU; LLM y embedder falsos)
python -m pytest tests -q

# E2E contra el contenedor en marcha
python scripts/probe.py --rag-pregunta "¿Qué es la policy brazos libres?"
```

## Troubleshooting

| Síntoma | Causa / solución |
|---|---|
| `/ready` devuelve 503 mucho tiempo | Modelo grande en CPU, o falta el GGUF: mira `docker compose logs llm` (`[entrypoint] ERROR: modelo no encontrado`). |
| `/chat` → 503 `llm-no-listo` | Igual que arriba; la API arranca antes que el modelo a propósito. |
| `rag: inicializando (descargando...)` | Normal en el primer arranque: descarga el modelo ONNX (~500 MB) y embedea el corpus. El chat responde mientras tanto, sin RAG. Sigue el progreso con `GET /rag/stats` (`chunks_embedded`). |
| `rag: embedder no disponible` | Sin red para descargar el modelo ONNX la primera vez. Solución: arranca una vez con red, o copia `./data/fastembed` desde otro equipo. El chat sigue funcionando sin RAG. |
| Respuestas lentas en tu PC | Normal sin GPU (~5–13 tok/s); usa `compose.x86-cuda.yml` o el Jetson. |
| Puerto ocupado | Cambia `API_PORT` / `LLM_PORT` en `.env`. |
| El robot no responde tras `compose up` en el Jetson | `sudo jetson_clocks`, comprueba `tegrastats` (throttle) y que `runtime: nvidia` existe (`docker info | grep -i runtime`). |
| Descarga de modelos falla en China | `HF_ENDPOINT=https://hf-mirror.com`. |

## Procedencia

Adaptado de `linksoul-local` (mismo equipo): `llm-server/entrypoint.sh`,
perfiles de compose y scripts de descarga provienen de allí; el bridge de
LinkSoul **no** forma parte de este sistema. La API con memoria y RAG es
desarrollo nuevo de este repo.
