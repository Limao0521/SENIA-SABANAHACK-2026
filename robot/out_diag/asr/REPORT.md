# T3 — ASR español (receptionista HRI, Club N) — REPORT

**Fecha:** 2026-10-23 22:15 · **Modelo elegido:** `faster-whisper large-v3-turbo` (repo HF `mobiuslabsgmbh/faster-whisper-large-v3-turbo`, MIT) con CTranslate2 **int8 en GPU** + **silero-vad 6.2.3** para endpointing.
**Venv aislada:** `/home/luis/GitHub/x2_ultra-reto2/.venv-asr` (uv, Python 3.11). Versiones en `src/x2_ultra/hri_runtime/asr/requirements.txt`.
**Código:** `src/x2_ultra/hri_runtime/asr/__init__.py` — `Asr.transcribe(wav_path_or_pcm, sr) -> {"text","t_start","t_end","latency_s"}` y `StreamingAsr.feed(chunk) -> dict|None` (fin de utterance vía VAD → transcript; `flush()` al cerrar sesión).

## Transcripciones de los WAV de validación (definen tests WAV1–6 de T6)

WAV convertidos con ffmpeg a 16 kHz mono en `out_diag/asr/wav/wav{1..6}.wav` desde
`~/Downloads/WhatsApp Ptt 2026-10-23 at {9.24.00, 9.24.35, 9.24.50, 9.26.07, 9.26.26, 9.26.52} PM.ogg`.

| WAV | archivo (hora) | dur | transcript |
|---|---|---|---|
| WAV1 | 9.24.00 PM | 4.97 s | Me gustaría hacer algo de ejercicio en el club. ¿Qué opciones hay? |
| WAV2 | 9.24.35 PM | 5.05 s | ¿A dónde podría ir dentro del club? |
| WAV3 | 9.24.50 PM | 4.75 s | Dame información acerca del gimnasio. |
| WAV4 | 9.26.07 PM | 4.97 s | ¿Qué opciones de comida o restaurantes hay en el club? |
| WAV5 | 9.26.26 PM | 4.03 s | ¿Puedo ir a los restaurantes? |
| WAV6 | 9.26.52 PM | 4.41 s | Me gustaría ir al cuarto del staff. |

Coherencia con el spec: WAV3/WAV4 misma intención (PREGUNTA de información/servicios);
WAV5 = petición de destino (restaurantes; WORKER ⇒ **no**, negación cortés + alternativa);
WAV6 = petición de destino (cuarto del staff; VIP/REGULAR/GUEST ⇒ **no**).

## Benchmark (warm, reps=3 por WAV, GPU compartida)

| métrica | valor |
|---|---|
| latencia end-of-audio → texto, **p50** | **0.157 s** |
| latencia **p95** | **0.354 s** (1ª corrida fría wav1: 1.12 s; tras warmup estable) |
| RTF (compute/audio, 28.2 s de audio) | **0.138** |
| VRAM propio (nvidia-smi por PID) | **1146 MiB tras carga, 1222 MiB pico** ≤ 1.5 GB ✔ |
| Peak RSS (host) | 1944 MB |
| carga de modelo + warmup | 3.5 s |
| streaming: endpoint(VAD) → transcript | 0.139–0.152 s (+500 ms de silencio VAD para cerrar ⇒ ~0.65 s percibidos) |

Crudo: `out_diag/asr/bench_results.json` · script: `out_diag/asr/bench.py`
(reproducir: `.venv-asr/bin/python -I out_diag/asr/bench.py 3`).

## Decisiones

- **faster-whisper turbo int8** en vez de Parakeet: instalado y funcionando en ~8 min; Parakeet
  (NeMo) requiere deps torch+CUDA pesadas y no cabía en el presupuesto de tiempo/VRAM compartida;
  onnx-asr/sherpa-onnx quedó pendiente (riesgo de otra descarga ~1.3 GB). Sin pérdida: latencia
  p50 0.16 s ya cumple de sobra el bucle CONVERSATION.
- **greedy (beam_size=1) + without_timestamps + vad_filter**: mínima latencia.
- OJO: el alias `large-v3-turbo` de faster-whisper apunta a `mobiuslabsgmbh/...` (el repo
  `Systran/faster-whisper-large-v3-turbo` **no existe**, da 401).
