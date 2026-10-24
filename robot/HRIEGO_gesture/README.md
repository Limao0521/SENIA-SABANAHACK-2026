# HRIEGO_gesture: gestos de brazo elegidos por LLM sobre HRIEGO v2

HRIEGO_gesture extiende HRIEGO v2 (`hriego_v2/`, solo lectura) con gestos de brazo comunicativos.

**Principio de diseno:** la FSM decide el acto comunicativo; el LLM solo elige un gesto dentro de un enum por estado. Asi el modelo pequeno (Qwen3-4B) no puede inventar comportamientos fuera de catalogo.

- Contrato: `rcp01c.intervention.v2` (`contract/schema.py`, catalogo en `contract/catalog.json`, lexico de anclas en `anchor_lexicon`).
- Actos por estado: GREETING -> GREETING, GUIDANCE -> GUIDE, DIALOGUE -> RESPOND, FAREWELL -> CLOSING (`const` en el esquema).
- Gestos por estado:
  - GREETING: `GREETING_<rango>` o NONE.
  - GUIDANCE: POINT_RIGHT, POINT_LEFT, POINT_FRONT.
  - DIALOGUE: NEGATION_NORMAL, NEGATION_STRONG, CONFIRMATION, THUMBS_UP, SOCIAL_HEART, APPLAUSE, NONE.
  - FAREWELL: SOCIAL_HEART para VIP; FAREWELL_WAVE u otros, o NONE.
- `trigger_text` es un fragmento literal del habla (1-4 palabras, palabra completa). Un anclaje CTC lo situa en el audio y un planificador dispara el preset o el movimiento en el instante justo.
- `type="NONE"` (con `trigger_text` vacio) se normaliza a `motor_intents=[]`.

**Contenido de este repositorio:** codigo del pipeline, contrato, evaluacion del LLM, visitas, alineacion CTC y planificador, workers persistentes, validacion 4d, los resultados en JSON/CSV/Markdown pequenos, el gemelo X2 (`twin/`: codigo, `SETUP.md` y el replay pack `twin/replay/` con movimiento y audio de visitas y del show comercial) y `motion/` (scripts vendorizados de retargeting, SONIC y StreamTalk). **No contiene** los audios de generacion masiva, trazas crudas, videos ni los runs crudos del gemelo (ver seccion 3.6 y seccion 6). Los datos externos y modelos se describen en la seccion 5.

## 1. Pipeline

```
 visitante (texto)
      |
      v
 FSM (hriego_v2) decide estado y acto  ----------------------------------+
      |   bypass de reconocimiento facial: run_visit.PROFILES            |
      v                                                                  |
 ContextAssembler (gesture_context.py, subclase de hriego_v2)            |
      |   sobre de contexto + enum de gestos del estado                  |
      v                                                                  |
 Qwen3-4B Q4_K_M en el contenedor x2-llm  (llama-server, json_schema     |
      |   strict, T=0.2, max_tokens 320; un reintento si falla validador)|
      |   speech + motor_intents[type, trigger_text]                     |
      v                                                                  |
 validador (contract/schema.py: literal, cue de ancla, <6 palabras,      |
      |   rango, direccion de POINT, frase de STAFF)                     |
      v                                                                  |
 VisitGestureManager (cooldown, sin repetir emote, max 2 SOCIAL_HEART    |
      |   y 2 APPLAUSE por visita; supresiones en decision.suppressed)   |
      v                                                                  |
 Supertonic M1 (TTS, tts.py) -> WAV 44.1 kHz + sha256 en el manifiesto   |
      |                                                                  |
      v                                                                  |
 QuartzNet 15x5 CTC (align/anchor_plan.py) -> ancla del trigger (20 ms)  |
      |                                                                  |
      v                                                                  |
 planificador de presets (c09 con latencias c08) -> instante de disparo  |
      |                                                                  |
      +--> preset SDK (emote, dominio 77)                                |
      +--> SONIC offline (StreamTalk -> GMR -> SONIC) para NONE y POINT  |
```

- Los gestos POINT_* tienen `motion_id: null`: el apuntado lo ejecuta otro sistema (estado `anchor_only_no_preset`).
- Los gestos NONE usan la cadena SONIC offline (`none_sonic`); no hay preset.
- Los workers persistentes StreamTalk, GMR y SONIC (`workers/`) mantienen los modelos cargados entre peticiones.
- La cara (reconocimiento facial) esta **desactivada**: el perfil del visitante se inyecta en `run_visit.PROFILES`. No hay imports de Face/camara/UniFace en el codigo incluido.

## 2. Estructura

| ruta | contenido |
|---|---|
| `run_visit.py` | una visita (un rango, varios turnos). Salidas: `visit_log.json`, `tts_manifest.jsonl`, WAV, `anchor_plan/` |
| `run_visits_v2.py` | lote de 5 visitas (VIP, REGULAR, GUEST, STAFF, EXTERNAL) |
| `gesture_context.py` | escenarios (`SCENARIOS`) y `GestureContextAssembler` |
| `llm_bridge.py` | puente al contenedor `x2-llm` (`docker exec`, llama-server) |
| `tts.py` | `GestureTTS` sobre `SupertonicTTSRunner` de hriego_v2 |
| `report_visits.py` | genera `visits_v2/report.md` y actualiza `visit_log.json` |
| `contract/` | esquema, catalogo y validador (`schema.py`, `catalog.json`) |
| `eval/` | evaluacion del LLM: casos (`cases.json`, `cases_v2.json`), `run_eval.py`, `metrics.py`; resultados `results/` (v1) y `results_v2/` (v2.1) |
| `visits_v2/` | visitas v2: `visit_log.json`, `tts_manifest.jsonl`, `anchor_plan/`, `report.md`, perfiles de recursos |
| `align/` | `anchor_plan.py` (CTC + planificador), `gen_test_wavs.py`, fixtures `test_wavs/` (11 WAV), `test_manifest.jsonl`, `test_out/` |
| `workers/` | `st_worker.py`, `gmr_worker.py`, `sonic_worker.py`, `common.py`, `wlib.py`, `bench.py`, `REPORT.md`, `bench/*.json|csv` |
| `validation/` | `trace_check.py`, `failclosed.py` + `failclosed_anchor.py`, `latency_ctc.py`, `latency_budget.py` y sus JSON/CSV |
| `VALIDATION_4d.md` | informe de validacion 4d |
| `paths.py` | raiz de rutas (`HRIEGO_GESTURE_ROOT`) y variables de entorno para interpretes y raices externas (seccion 7) |
| `align/vendor/` | copias de los modulos c09 (`align_anchor`, `common`, `event_builder`, `preset_scheduler`) y del aligner E4 (CTC), con `PROVENANCE.md` (sha256 de cada original) |
| `config/` | `preset_latency_c08.json` (copia byte a byte tomada del gemelo, sha256 `d35c250a...412f3`) y `PROVENANCE.md` |
| `requirements/` | exportaciones de entornos y `CONFLICTS.md` (seccion 4) |
| `tools/` | `relativize_paths.py [--dry-run]`: normaliza a rutas relativas las rutas absolutas de artefactos de texto ya generados (idempotente) |

## 3. Resultados

Cifras del host de medidas (22 GB RAM, 12 GB VRAM NVIDIA, swap 2 GB, Linux 6.14). **Aviso de version:** las cifras de alineacion (3.2) y de trazabilidad (3.5) se midieron antes de la portabilidad (`paths.py`, `align/vendor/`), antes de anadir G1 y antes de cambiar el defecto de `--align` a `auto`. Solo `validation/failclosed.json` se ha regenerado tras anadir G1 (39/39). La traza (1635/1635) y los planes almacenados no se han vuelto a calcular; los planes almacenados son de politica onset (ver 3.2). Todas las cifras de LLM son de **v2.1** (`eval/results_v2/summary.json`, 52 casos x 2 replicas = 104 ejecuciones). Las cifras de la v1 (94% validez, 92% exactitud, NONE 0/16) **no** describen el contrato actual y se conservan solo como historia en `eval/results/summary.json`.

### 3.1 LLM: contrato v2.1 (`eval/results_v2/summary.json`)

| metrica | valor |
|---|---|
| JSON valido | 100% |
| contrato valido, final (tras reintento) | 86.5% |
| contrato valido, primer intento | 73.1% (reintento en 26.9%; recupera 14 casos) |
| exactitud en dialogo (exact) | 84.4% |
| trigger literal (subcadena exacta del habla) | 96.7% (primer intento: 95.6% literal y palabra completa) |
| gestos espurios en NONE | 0 |
| NONE devuelto como INVALID | 3/16 (18.8%); no produce gesto |
| POINT, direccion correcta | 91.7% (11/12; el fallo es "frente a mi", menos de 6 palabras) |
| STAFF, frase obligatoria | 100% |
| GREETING, emote de rango | 100% |
| VIP FAREWELL SOCIAL_HEART valido | 2/4 (los otros dos son INVALID, fail-closed) |
| latencia LLM p50 / p95 (primer intento) | 1.32 s / 1.55 s; con reintento p95 2.93 s |
| TTS Supertonic p50 / p95 | 0.67 s / 0.96 s (RTF medio 0.17) |

Precision/recall por emote (dialogo): NEGATION_NORMAL 1/1, NEGATION_STRONG 1/1, CONFIRMATION 1/1, THUMBS_UP 1/0.875, APPLAUSE 1/0.75, SOCIAL_HEART 1/0.50, NONE 1/0.81. La precision es 1.0 en todos: el sistema prefiere no disparar un gesto antes que dispararlo mal. Los fallos son rechazos del validador (frases de menos de 6 palabras, triggers parafraseados, triggers fuera del lexico).

Comparativa v1 frente a v2.1 (`eval/results/summary.json` frente a `eval/results_v2/summary.json`): el contrato v2.1 es mas estricto, por eso baja la validez final (94% -> 86.5%) y sube la validez del primer intento (30% -> 73%).

### 3.2 Anclas CTC y planificador

- Visitas v2: 25/25 gestos alineados (100%). Todas las filas: `planned` (presets), `anchor_only_no_preset` (POINT), `none_sonic` (NONE). Sin errores de alineacion.
- Retardo de inicio de audio para presets (planes onset almacenados): p50 0.37 s, max 0.56 s (cap 1.5 s).
- CTC: 0.086 s p50 por clip en CPU (2 hilos), resolucion 20 ms. Las anclas **no estan validadas por humanos**.
- Politica: el defecto es `--align auto` (stroke; si el retardo de inicio de audio que necesita supera 1.5 s, midpoint; y si no hay lead usable, onset). La politica usada queda por fila en `align_policy_used` (`summary.json`, `align_policy_used_counts`) y en `plans/*.json` (`policy.align_policy_used`, `policy.align_policy_tried`). **Los planes almacenados no se regeneraron**: `visits_v2/**/anchor_plan/`, `align/test_out/` y los runs del gemelo 4c/4e son de `--align onset` (`policy.align == "onset"`), que es lo que se ejecuto en el gemelo. c12 y p4 usaron `--align stroke`. Regenerar con el defecto da planes stroke (en las 5 visitas: stroke salvo `v2visit_external_t00_greeting`, que pasa a midpoint con retardo 1.05 s); los retardos de inicio de audio pasan de <= 0.58 s (onset) a <= 1.24 s (stroke).

### 3.3 Visitas v2 (`visits_v2/report.md`, `visits_v2/resource_profile.json`)

5 visitas, 30 turnos. VRAM pico 3.9 GB (util GPU pico 99%); RAM del contenedor `x2-llm` pico 5.7 GB; Supertonic RSS pico 505 MB. La intencion de emote de los 6 tipos no se cubrio por completo: el modelo a veces responde con la frase de guia en vez de la frase del visitante.

### 3.4 Workers persistentes (`workers/REPORT.md`)

| medida | valor |
|---|---|
| cadena en frio (StreamTalk + GMR + SONIC) | 31.0 s por clip |
| cadena en caliente (p50) | 6.3 s (4.9x) |
| salida caliente vs frio | identica bit a bit con la misma semilla |
| pipeline, rendimiento | 2.1x |
| RAM estable de los 3 workers | 7.9 GB (pico ~10 GB con pipeline) |
| VRAM | 2.3 GB (solo StreamTalk) |

Etapas en frio medidas: StreamTalk 20.6 s (RTF 4.7-8.7), GMR 4.9 s, convert 1.7 s, rollout 2.6 s, prep 0.8 s. **Esta cadena no es tiempo real.**

### 3.5 Validacion 4d (`VALIDATION_4d.md`)

- Trazabilidad: 1635/1635 comprobaciones sin discrepancias (medido antes de G1 y de `auto`; `validation/trace_check.py` toma el retardo (lead) de la politica de cada plan)
- Fail-closed: 39/39 inyecciones requeridas pasan y 0 sondas extra (`validation/failclosed.json`). Incluye G1: `anchor_plan.py` no comprobaba `speech[start:end] == trigger_text`; esa sonda fallo en la primera medida y despues se anadio la guarda (estado `rejected_trigger_span`). Tambien el caso de fallback de `--align auto`. Ningun turno real se vio afectado.
- Seguridad: 0 caidas en 60 turnos, pelvis z min 0.684 m, 0 gestos espurios, 0 fugas de rango.
- NONE con SONIC: error de brazo 0.0088-0.0097 rad (gate 0.015).
- Calidad abierta: POINT en el instante del ancla supera 15 grados en 6 de 10 ejecuciones del gemelo (la media en el hold es 4-8 grados); recall bajo en SOCIAL_HEART y APPLAUSE; alucinaciones del LLM en NONE (F6 de `VALIDATION_4d.md`).

**Importante:** `validation/trace_check.py` y `validation/latency_budget.py` leen trazas y metricas del gemelo (`twin/runs/...`). Ese directorio **no esta en este commit**, asi que esos dos scripts no se pueden repetir desde el repositorio solo. Los JSON de sus resultados si estan incluidos.

### 3.6 Experimentos del gemelo X2

Los experimentos de ejecucion en el gemelo X2 MuJoCo (MC v1.0.0) estan en `twin/`. Incluidos: codigo, `twin/SETUP.md`, `twin/PROVENANCE.md`, metricas y REPORT de `twin/runs/` y el replay pack (`twin/replay/`, ver su README). **No incluidos**: trazas CSV, `*_qpos.npz`, videos y motion crudo de los runs. Cifras principales:

- 4aa, apuntado: error medio en el hold con base READY de 2.2 grados (RIGHT, LEFT) y 4.0-4.4 (FRONT).
- 4c, 5 visitas x 6 turnos x 2 replicas: 20/20 presets ejecutados; onset-ancla mediana 7.0 / 9.4 ms; 0 caidas.
- 4e, misiones VIP/STAFF/GUEST: 18/18 turnos; caminata 1.51 m; giro 88.7-93.4 grados (objetivo 90 +- 5).
- c12, transiciones SONIC <-> preset: con V2 la mediana de brazos quietos baja a 0.00 s (V0 2.17 s; V1 1.42 s).
- p4, comercial en 2 robots (19 lineas): error de disparo de emotes +1 a +19 ms; sincronia entre robots -14/+6 ms.
- Batch SONIC: 19 clips comerciales PASS, 0 caidas.

Nada de esto se ha ejecutado en el robot real. El MC del gemelo es v1.0.0 y el robot es v1.1.4.

## 4. Requisitos

| componente | version | donde esta |
|---|---|---|
| Linux x86_64 con GPU NVIDIA | probado en 22 GB RAM, 12 GB VRAM | - |
| Docker (contenedor `x2-llm`) | `llm/docker-compose.yml` + `compose.x86-cuda.yml` | `llm_bridge.py` usa `docker exec` |
| venv `Face/.venv-face` (Python 3.12.14) | torch 2.14.1+cu130, onnxruntime-gpu 1.31.0, numpy 2.5.4, opencv 5.0.0.93 | `requirements/face-venv.txt` |
| venv Supertonic (Python 3.12.3) | supertonic 1.3.1, onnxruntime 1.31.0 CPU, numpy 2.5.4 | `experiments/hriego_v2_tts_eval_20261021/.venv`; `requirements/supertonic-venv.txt` |
| conda `physicalai` (Python 3.12.14) | torch 2.11.0+cu128, onnxruntime 1.30.0 + gpu 1.26.0, numpy 2.3.1, mujoco 3.11.0, transformers 5.17.0, librosa 1.0.0, smplx 0.1.28 | `requirements/physicalai.yml` y `.txt` |
| conda `gmr` (Python 3.10.21) | torch 2.14.0, onnxruntime 1.23.2, mujoco 3.13.0, mink 1.3.0, GMR `bb1bbe4`, smplx git `1265df7` | `requirements/gmr.yml` y `.txt` |
| conda `laplace` | no lo usa el codigo incluido | - |

- Las versiones y los conflictos entre entornos estan en `requirements/CONFLICTS.md`. **Un entorno unico no es viable con estas versiones**; se usan 4 entornos (Face, Supertonic, physicalai, gmr).
- `requirements/environment.total.yml` es un borrador unificado **no resuelto ni probado**.
- Los `.yml` de `physicalai` y `gmr` ya no tienen la linea `prefix:` (ruta de la maquina de origen). Los `.txt` siguen con dependencias desde git sin hash fijado (GMR, smplx, IsaacLab), y `environment.total.yml` comenta un wheel local `file://` de pandas en physicalai: **no son reproducibles tal cual**.

## 5. Datos externos, modelos y licencias

Estos archivos **no estan en el repositorio**. Son prerequisitos. Donde dice "verificado" se leyo la licencia en la fuente o en un archivo local; donde dice "no verificado" queda pendiente de comprobar.

| # | recurso | que es / donde se usa | donde obtenerlo | licencia y terminos | donde colocarlo (hoy) |
|---|---|---|---|---|---|
| 1 | **SMPL-X neutral** `SMPLX_NEUTRAL.npz` (167 MB) | cuerpo SMPL-X para retargeting GMR y para la recuperacion de StreamTalk (`workers/st_worker.py:14`, `workers/bench.py:104`) | registro en https://smpl-x.is.tue.mpg.de/ | el modelo SMPL-X tiene licencia propia y requiere registro (uso de investigacion). La pagina de la licencia del **cuerpo** SMPL-X indica CC BY 4.0 (`bodylicense.html`). Los terminos del `.npz` del modelo **no verificados** en esta sesion (la pagina no se pudo leer completa). No redistribuir. | `SemanticCorporeaHRI/third_party/GMR/assets/body_models/smplx/SMPLX_NEUTRAL.npz`. Ruta fija en codigo. Variable propuesta: `SMPLX_NPZ` |
| 2 | **BEAT2 English v2.0.0**, carpeta `smplxflame_30` y `train_test_split.csv` | datos de movimiento para la recuperacion (retrieval) de StreamTalk (`workers/st_worker.py:15`) | https://huggingface.co/datasets/H-Liu1997/BEAT2 (`hf download H-Liu1997/BEAT2 --include "beat_english_v2.0.0/**"`) | el dataset en Hugging Face declara **Apache-2.0** (verificado en la pagina). La tarjeta de datos no tiene texto de terminos. El CSV `wayne069_audio_only_20261014` usado por `workers/` **no verificado** (no es un fichero oficial de BEAT2 por el nombre). | `SemanticCorporeaHRI/data/raw/BEAT2/beat_english_v2.0.0/`. Variable propuesta: `BEAT2_ROOT` |
| 3 | **Checkpoint StreamTalk** `streamtalk_speaker_all_e0940_cfg3.pt` (sha256 `3a0e3558d788fd3f11a5a87a8198b42725104b5b9b24f95a4e7c84c14395c9a5`, segun `SHA256SUMS`) | modelo de gestos co-habla (`workers/st_worker.py`, `twin/sonic_batch.py:12`) | https://huggingface.co/X-Zhang/StreamTalk (archivo `streamtalk_speaker_all_e0940_cfg3.pt`) | MIT (tarjeta del modelo, copia local `checkpoints/pretrained/MODEL_CARD.md`, verificado) | `Laplace/Gesture/third_party/StreamTalk/checkpoints/pretrained/`. Ruta fija en codigo. Variable propuesta: `STREAMTALK_ROOT` |
| 4 | **Codigo StreamTalk** | `workers/st_worker.py` importa `Common.*`, `FindNearest` y `ce.main` de este arbol | repositorio de StreamTalk (URL de origen no anotada en el arbol local; hay `CITATION.cff`) | MIT (`LICENSE` local, copyright 2026, verificado). El README indica que los datos y modelos de terceros tienen sus propios terminos. | `$LAPLACE_ROOT/Gesture/third_party/StreamTalk` |
| 5 | **WavLM Large (LibriSpeech 100 h)**, copia `wavlm-libri-clean-100h-large` | encoder de audio de StreamTalk (`--wavlm_model`) | https://huggingface.co/patrickvonplaten/wavlm-libri-clean-100h-large (ajuste fino de microsoft/wavlm-large) | la tarjeta **no declara licencia**; enlaza la licencia del repositorio UniSpeech de Microsoft. El tipo de licencia **no verificado** en esta sesion. Pendiente. | `Laplace/Gesture/third_party/StreamTalk/assets/wavlm-libri-clean-100h-large/` |
| 6 | **GMR** (General Motion Retargeting), commit `bb1bbe4` | retargeting humano -> robot (`workers/gmr_worker.py`, `x2_retarget`) | https://github.com/YanjieZe/GMR | MIT (`LICENSE` local en `SemanticCorporeaHRI/third_party/GMR`, verificado). Ademas, el retargeter de AgiBot (`x2_external_evidence/agibot_x2_retargeter_bb582d97`) es Apache-2.0 (`LICENSE` local, verificado) | `SemanticCorporeaHRI/third_party/GMR/` |
| 7 | **Politica SONIC** `x2_sonic_14000_g1.onnx` (58 MB) | politica de seguimiento de cuerpo entero para X2 en MuJoCo (`workers/sonic_worker.py:16`) | bundle `sonic-x2` en `Laplace/mimo/X2/vendor/x2_external_evidence/sonic_x2_c9959443/models/` (origen: NVIDIA GEAR-SONIC segun su README) | El repositorio de NVIDIA GR00T-WholeBodyControl declara codigo Apache-2.0 y **pesos bajo NVIDIA Open Model License** (verificado en GitHub). Si el ONNX de X2 cae bajo esa licencia **no verificado**. El README del bundle indica que los **movimientos** provienen de BONES-SEED (licencia propia, dataset con acceso condicionado; ver fila 11). Sha256 **pendiente de calcular**. | `X.SONIC_ROOT/models/` (ruta de Laplace c03; `workers/sonic_worker.py:8`) |
| 8 | **QuartzNet 15x5 CTC espanol**, ONNX (75 MB), revision `openvoiceos@8f9307e93cdb05a06fcaa0b977312495e96f405d`, sha256 del ONNX `e66ee9f0798f0faca8f426b9124c66c20df76d6dadb3a2371c808cff597c3931` | alineacion CTC de las anclas (`align/anchor_plan.py`, c09) | colección OpenVoiceOS STT/ASR ONNX en Hugging Face (`https://huggingface.co/collections/OpenVoiceOS/stt-asr-onnx-699321e8732462509c642fbe`); base NVIDIA `stt_es_quartznet15x5` | el model card local (`models/quartznet15x5_es/README.md`) declara **cc-by-4.0**. La pagina de NVIDIA respondio 401, licencia base **no verificada en esta sesion**. | `QUARTZNET_ONNX` (por defecto en `$LAPLACE_ROOT/voice/experimental/hri_temporal_alignment_20261020/E4_COMPACT_CTC/models/quartznet15x5_es/model.onnx`). Sha fijado en `align/vendor/align_anchor.py` |
| 9 | **Supertonic 3** (pesos ONNX + voces M1) | TTS (`tts.py`, `hriego_v2/supertonic_worker.py`) | https://huggingface.co/Supertone/supertonic-3 , revision `724fb5abbf5502583fb520898d45929e62f02c0b` (dada por el autor; no verificada en la pagina) | **OpenRAIL-M** para el modelo (verificado en la pagina y en `model/LICENSE` local, con restricciones de uso que hay que leer). Codigo de muestra MIT. | `experiments/hriego_v2_tts_eval_20261021/model/`. Sin sha256 fijado; `auto_download=False` (`hriego_v2/supertonic_worker.py:19`) |
| 10 | **Qwen3-4B Q4_K_M** (GGUF, 2.5 GB, `Qwen3-4B-Q4_K_M.gguf`) | LLM del contrato (contenedor `x2-llm`) | `llm/scripts/download-model.sh 4B Q4_K_M` (el repositorio GGUF de origen no esta anotado) | **Apache-2.0** para Qwen3-4B (verificado en el model card de Qwen/Qwen3-4B-GGUF). | `llm/models/Qwen3-4B-Q4_K_M.gguf`. **sha256 fijado: `7485fe6f11af29433bc51cab58009521f205840f5b4ae3a32fa7f92e8534fdf5`** en `scripts/setup_hriego_v2.sh:82` |
| 11 | **BONES-SEED** (movimientos de captura de Bones Studio) | origen de los movimientos del bundle SONIC; no lo lee el codigo incluido directamente | https://huggingface.co/datasets/bones-studio/seed (acceso condicionado) | licencia propia de Bones Studio (`licensing@bones.studio`). Texto no leido en esta sesion: **no verificado**. No redistribuir movimientos fuera de esa licencia. | no se copia al repo |
| 12 | **URDF de AgiBot X2**, commit `575cc6b988f976c23550e0db85aa1e5475d3652d` | robot X2 Ultra; renderizado del gemelo (no usado por el codigo incluido) | https://github.com/AgibotTech/agibot_x2_urdf | **MulanPSL-2** (`LICENSE` local, verificado) | `~/robots/agibot_x2_urdf` (`X2_URDF_ROOT`, solo gemelo) |
| 13 | **AimDK / MC del X2** (MC v1.0.0, `animation_player.yaml` con `preplaying_tick: 500`) y overlays `/tmp/aimdk_overlay`, `/tmp/aimdk_mc_overlay` | solo el gemelo (excluido de este commit) | fuente de AgiBot, no localizada en un repositorio abierto | **no verificado**. `~/robots/x2_simulation` y `x2_ultra-real-robot-teleop` no son repos git | no incluido |
| 14 | **MuJoCo** (3.11.0 en physicalai, 3.13.0 en gmr) | rollout SONIC y renderizado | PyPI | Apache-2.0 (licencia del proyecto; no releida en esta sesion) | entornos conda |

Notas:
- Ninguno de los modelos ni datasets anteriores se redistribuye en este repositorio.
- La licencia de BEAT2 y de SMPL-X se debe confirmar antes de publicar cualquier derivado.
- Hay que aceptar terminos de descarga en Hugging Face y en el sitio de SMPL-X antes de descargar (la autenticacion no se automatiza aqui).

## 6. Que queda fuera del commit (y donde vive)

- **Gemelo X2, runs crudos** (`twin/runs/`): trazas CSV, `*_qpos.npz`, videos MP4, logs y muestras de SONIC. Solo se incluyen REPORT, metricas (`metrics/*.json|csv`) y planes (`plans/*.json`). Para repetir `trace_check.py` o `latency_budget.py` hacen falta las trazas, que no estan en el repo.
- **Replay pack del gemelo** (`twin/replay/`): **si** incluido (audio de 19 clips comerciales y 31 turnos de visita en 16 kHz, arms SONIC `*_arms.npz`, timelines, metricas c08). Las CSV de animacion de presets (BOW, HEART, APPLAUSE, FAREWELL...) son assets externos de AgiBot MC y no estan incluidas.
- **WAV de visitas y de evaluacion** (`visits_v2/**/wav/`, `eval/results*/**/wav/`): 186 archivos, ~56 MB. Los 31 WAV de turno a 16 kHz (`visits_v2/*/sonic/*/wav16/`) se copian a `twin/replay/visits/` junto a sus arms. Regenerables con Supertonic; el manifiesto `tts_manifest.jsonl` guarda el sha256. Los fixtures de `align/test_wavs/` (11 WAV, 2.9 MB) **si** se incluyen para la prueba reproducible del planificador.
- **Motion SONIC** (`visits_v2/**/sonic/`, `workers/bench/**/*.npz|pkl`): regenerables con la cadena offline.
- **Logs y locks** (`logs/`, `*.log`, `.x2_heavy_job.lock`), `__pycache__/`.
- **Muestras crudas de benchmark** (`workers/bench/cold/`, `workers/bench/pipelined/`, `samples_*.csv`): el resumen queda en `workers/bench/*.json` y `resource_latency.csv`.

## 7. Preparacion (setup)

Esta guia es de referencia. Las rutas de los scripts son relativas a la raiz del repositorio o se leen de las variables de la tabla siguiente. **No verificado en un clon limpio.**

```bash
cd "$X2_ULTRA_ROOT"            # raiz del repositorio (x2_ultra)
export PYTHONPATH=$PWD

# 1. Contenedores y modelo LLM (ver tambien scripts/setup_hriego_v2.sh, que automatiza parte de esto)
cd llm && docker compose -f docker-compose.yml -f compose.x86-cuda.yml up -d && cd ..
sha256sum llm/models/Qwen3-4B-Q4_K_M.gguf   # debe ser 7485fe6f11af29433bc51cab58009521f205840f5b4ae3a32fa7f92e8534fdf5 (seccion 5, fila 10)

# 2. Venv de HRIEGO (Face) y venv de Supertonic: los crea scripts/setup_hriego_v2.sh
#    Face/.venv-face       <- Face/requirements/live-gpu-cu130.txt
#    experiments/hriego_v2_tts_eval_20261021/.venv <- hriego_v2/requirements-supertonic.txt

# 3. Entornos conda physicalai y gmr: NO hay script. Crear desde requirements/*.txt con pip
#    y revisar CONFLICTS.md antes. [no verificado]

# 4. Datos externos (seccion 5): SMPL-X, BEAT2, StreamTalk (checkpoint + WavLM), GMR, SONIC, QuartzNet, Supertonic.
# 5. Laplace: rcp01c02/c03/c07/c08/c09 y voice/experimental/hri_temporal_alignment_20261020/{E4_COMPACT_CTC,E7_X2_PROPOSAL_PILOT}
```

Variables de entorno (todas opcionales; los defectos estan en `paths.py`):

| variable | valor por defecto | uso |
|---|---|---|
| `HRIEGO_GESTURE_ROOT` | directorio de `HRIEGO_gesture/` | raiz contra la que se resuelven las rutas relativas guardadas |
| `X2_ULTRA_ROOT` | padre de `HRIEGO_gesture/` | raiz del repositorio x2_ultra (defecto de `SUPERTONIC_PY`) |
| `LAPLACE_ROOT` | `~/GitHub/Laplace` | repo Laplace: scripts c02/c03/c07/c08, StreamTalk y E4 QuartzNet (`QUARTZNET_ONNX`); base por defecto de los externos de abajo |
| `X2_EXTERNAL_ROOT` | `$LAPLACE_ROOT/mimo/X2/vendor/x2_external_evidence` | checkouts externos X2 (GMR/, agibot_x2_retargeter_bb582d97/, sonic_x2_c9959443/) usados por `motion/` |
| `X2_IK_DIR` | `$LAPLACE_ROOT/mimo/X2/results/v2m_02_x2_ultra` | modelo IK de toe-proxy auditado (`motion/`) |
| `X2_MOTION_ROOT` | directorio de `motion/` | directorio por defecto de la CLI de `motion/x2_*.py` |
| `STREAMTALK_ROOT` | `$LAPLACE_ROOT/Gesture/third_party/StreamTalk` | checkpoints/ y assets/ de StreamTalk |
| `STREAMTALK_RETRIEVAL_CACHE` | cache c02 en `$LAPLACE_ROOT/experiments/RCP-01/runs/20261019_rcp01c02_fixedcalm/cache/streamtalk_retrieval` | KD-tree de recuperacion (externo, `*.npz`) |
| `STREAMTALK_TRAIN_CSV` | `$LAPLACE_ROOT/Gesture/experiments/wayne069_audio_only_20261014/configs/streamtalk_train_excluding_wayne069.csv` | CSV de entrenamiento para la recuperacion |
| `X2_TWIN_SHIM_DIR` | `~/.local/share/hriego/shim` | shim docker->podman en el PATH de los scripts de `twin/` |
| `X2_TELEOP_ROOT` | `~/GitHub/x2_ultra-real-robot-teleop` | checkout con `x2_ultra.robot.joints` (`<root>/src`), usado por el gemelo |
| `X2_URDF_ROOT` | `~/robots/agibot_x2_urdf` | URDF de AgiBot X2 (solo gemelo) |
| `X2_SIM_ROOT` | `$X2_ULTRA_ROOT/official_sim` | simulador oficial X2 usado por el gemelo |
| `SEMANTIC_CORPOREA_ROOT` | `~/GitHub/SemanticCorporeaHRI` | repo SemanticCorporeHRI: assets de GMR y BEAT2 (workers/) |
| `HRIEGO_PY_PHYSICALAI` | `$CONDA_PREFIX_PHYSICALAI/bin/python`, o `$CONDA_BASE/envs/physicalai/bin/python` | python del entorno physicalai (CTC, StreamTalk, SONIC) |
| `CONDA_PREFIX_PHYSICALAI` | sin defecto (si no se define, se usa el valor anterior) | prefijo del entorno physicalai |
| `HRIEGO_PY_GMR` | `$CONDA_PREFIX_GMR/bin/python`, o `$CONDA_BASE/envs/gmr/bin/python` | python del entorno gmr (GMR) |
| `CONDA_PREFIX_GMR` | sin defecto (si no se define, se usa el valor anterior) | prefijo del entorno gmr |
| `CONDA_BASE` | `~/anaconda3` | instalacion de conda |
| `SUPERTONIC_PY` | `$X2_ULTRA_ROOT/experiments/hriego_v2_tts_eval_20261021/.venv/bin/python` | python de la TTS Supertonic |
| `QUARTZNET_ONNX` | `$LAPLACE_ROOT/voice/experimental/hri_temporal_alignment_20261020/E4_COMPACT_CTC/models/quartznet15x5_es/model.onnx` | modelo CTC; sha256 comprobado al cargar (`vocab.txt` en el mismo directorio) |
| `QUARTZNET_ALLOW_UNPINNED` | sin definir | `1` salta la comprobacion de sha256 (solo pruebas) |
| `SMPLX_NEUTRAL_NPZ` | `$SEMANTIC_CORPOREA_ROOT/third_party/GMR/assets/body_models/smplx/SMPLX_NEUTRAL.npz` | SMPL-X neutral (workers/) |
| `BEAT2_SMPLX_DIR` | `$SEMANTIC_CORPOREA_ROOT/data/raw/BEAT2/beat_english_v2.0.0/smplxflame_30` | datos BEAT2 para la recuperacion de StreamTalk (workers/) |
| `COMMERCIAL_SHOW_DIR` | `$LAPLACE_ROOT/voice/experiments/x2_commercial_show_indextts_corregido_20261022` | audio y movimiento del show comercial (workers/bench.py) |
| `ST_LOAD_SEED` | `1` | semilla de carga de StreamTalk (workers/st_worker.py) |

Puntos de entrada que las usan: `run_visit.py` (python physicalai para `anchor_plan.py`), `validation/failclosed.py` y `failclosed_anchor.py`, `workers/wlib.py`, `workers/*_worker.py`, `workers/bench.py`.

Los workers necesitan ademas los arboles externos de Laplace y SemanticCorporeHRI (seccion 11.1). Las rutas del propio repositorio ya no estan fijas.

`twin/` usa `COMMERCIAL_SHOW_DIR`, `CONDA_EXE` (conda para los entornos del gemelo) y las variables `X2_*` de arriba. Lo que queda por cerrar del gemelo: los scripts `analyze_*` leen las metricas c08 desde `$LAPLACE_ROOT` (copia en `twin/replay/presets_c08/`); las CSV de animacion de presets son assets externos de AgiBot; SONIC y StreamTalk no tienen sha fijado; las licencias del gemelo siguen por revisar; y las cifras de `trace_check` son anteriores a G1 y al modo auto.

## 8. Como ejecutar

Todo se ejecuta desde la raiz del repositorio con `PYTHONPATH=$PWD`. Los flags estan verificados contra `add_argument` / `sys.argv` de cada script.

### 8.1 Una visita (`run_visit.py`)

```bash
Face/.venv-face/bin/python -m HRIEGO_gesture.run_visit --rank VIP \
    --scenario reception_lab_right \
    --utterance "Gracias, me alegra haber podido ayudarte" \
    --out HRIEGO_gesture/eval/results/visits/vip
```

- `--rank {VIP,REGULAR,GUEST,STAFF,EXTERNAL}` (de `PROFILES`), por defecto VIP.
- `--scenario` (de `SCENARIOS` en `gesture_context.py`); por defecto `reception_lab_right`.
- `--utterance` repetible (una por turno DIALOGUE). `--out` obligatorio.
- Llama a `align/anchor_plan.py` por subproceso con el interprete de `HRIEGO_PY_PHYSICALAI`.

### 8.2 Lote de visitas v2 (`run_visits_v2.py`)

```bash
Face/.venv-face/bin/python -m HRIEGO_gesture.run_visits_v2 --ranks VIP REGULAR GUEST STAFF EXTERNAL \
    --out HRIEGO_gesture/visits_v2
```

- `--ranks` (lista) y `--out` (por defecto `HRIEGO_gesture/visits_v2`). Las frases por rango estan en `VISITS` del propio script.

### 8.3 Informe de visitas (`report_visits.py`)

```bash
Face/.venv-face/bin/python -m HRIEGO_gesture.report_visits
```

- Sin argumentos. Escribe `visits_v2/report.md` y **reescribe** `visits_v2/*/visit_log.json` (fusiona el resultado del planificador).

### 8.4 Evaluacion del LLM (`eval/run_eval.py`)

```bash
Face/.venv-face/bin/python -m HRIEGO_gesture.eval.run_eval --reps 2 \
    --cases HRIEGO_gesture/eval/cases_v2.json --out HRIEGO_gesture/eval/results_v2
```

- Flags: `--reps` (defecto 2), `--limit`, `--no-tts`, `--out` (defecto `results_v2`, **relativo al directorio actual**), `--cases` (defecto `cases_v2.json`), `--no-retry`.
- Salidas: `summary.json`, `llm_runs.jsonl`, `tts_manifest.jsonl`, `wav/`.
- Regenerar casos: `eval/make_cases.py` (v1) y `eval/make_cases_v2.py` (v2), ejecutados desde `eval/` (escriben `cases.json` / `cases_v2.json` en el directorio actual).

### 8.5 Alineacion CTC y planificador (`align/anchor_plan.py`)

```bash
export QUARTZNET_ONNX=/ruta/a/quartznet15x5_es/model.onnx     # sha256 pinned; vocab.txt en el mismo directorio
"$HRIEGO_PY_PHYSICALAI" HRIEGO_gesture/align/anchor_plan.py \
    --manifest HRIEGO_gesture/align/test_manifest.jsonl \
    --out HRIEGO_gesture/align/test_out_rerun \
    --align auto --stat median --max-initial-delay-s 1.5      # --latency defecto: config/preset_latency_c08.json
```

- `align/vendor/` contiene las copias de los modulos c09 y E4/E7 que usa el planificador: no hace falta Laplace para alinear. Solo el ONNX es externo (`QUARTZNET_ONNX`).
- Flags (verificados en `anchor_plan.py`)
- Estados de salida: `planned`, `none_sonic`, `anchor_only_no_preset`, `rejected_hash`, `rejected_resources`, `rejected_horizon`, `rejected_conflict`, `rejected_trigger_span` (G1), `alignment_<status>`. La politica usada queda en `align_policy_used`.
- Prueba minima: `align/test_manifest.jsonl` con los 11 WAV de `align/test_wavs/`. Las rutas del manifiesto se guardan relativas a la raiz de HRIEGO_gesture; si un manifiesto antiguo trae rutas absolutas, `tools/relativize_paths.py --dry-run` las muestra y luego se normalizan sin dry-run.
- Generar los WAV de prueba: `experiments/hriego_v2_tts_eval_20261021/.venv/bin/python HRIEGO_gesture/align/gen_test_wavs.py` (usa Supertonic).

### 8.6 Validacion 4d

```bash
Face/.venv-face/bin/python HRIEGO_gesture/validation/trace_check.py    # necesita twin/runs (no incluido)
Face/.venv-face/bin/python HRIEGO_gesture/validation/failclosed.py     # lanza el python de HRIEGO_PY_PHYSICALAI
"$HRIEGO_PY_PHYSICALAI" HRIEGO_gesture/validation/latency_ctc.py   # necesita QUARTZNET_ONNX
python3 HRIEGO_gesture/validation/latency_budget.py                    # necesita twin/runs (no incluido)
```

- `validation/*.json` y `trace_check_table.csv` son las salidas de referencia.
- Los scripts de `validation/` no tienen argparse; los parametros estan en el codigo.
- `failclosed_anchor.py` crea su directorio de trabajo con `tempfile.mkdtemp()`; no depende de rutas de sesion.

### 8.7 Workers persistentes (benchmark)

```bash
# bench.py necesita COMMERCIAL_SHOW_DIR, LAPLACE_ROOT (c02/c03/c07) y SEMANTIC_CORPOREA_ROOT (seccion 7)
"$HRIEGO_PY_PHYSICALAI" HRIEGO_gesture/workers/bench.py warm
"$HRIEGO_PY_PHYSICALAI" HRIEGO_gesture/workers/bench.py pipe 12
"$HRIEGO_PY_PHYSICALAI" HRIEGO_gesture/workers/bench.py cold 003_ULTRA_bienvenida,008_X2_original_serie_d
"$HRIEGO_PY_PHYSICALAI" HRIEGO_gesture/workers/bench.py compare 003_ULTRA_bienvenida,008_X2_original_serie_d
```

- Modos verificados en `bench.py:132-136`: `warm`, `pipe [N]` (defecto 12), `cold a,b,...`, `compare a,b,...`.
- Requiere x2-llm **y** el gemelo parados (ver seccion 9). Los clips de entrada vienen de `COMMERCIAL_SHOW_DIR` (por defecto dentro de Laplace), que **no es un prerequisito publico**: documentar o sustituir antes de usar.

## 9. Reglas de memoria y recursos (obligatorias en el host de 22 GB)

1. **Un proceso pesado a la vez.** StreamTalk (4.3 GB RSS), GMR (3.9 GB) y el contenedor del LLM (5.7 GB) no caben juntos con el gemelo: el conjunto medido llego a ~18.8 GB pico frente a 22 GB de RAM sin margen.
2. Antes de cualquier etapa comprobar `MemAvailable >= pico de la etapa + 4 GB` (StreamTalk necesita >= 8 GB libres).
3. **StreamTalk y GMR solo con x2-llm o el gemelo parados** (lo ideal: los dos parados). Reiniciar el LLM despues del batch.
4. 3 workers + x2-llm: ~13.6-14.3 GB estable, ~15.7 GB pico, ~1.8 GB libres: solo con el navegador y la cache pequenos.
5. Eval y visitas (LLM + TTS + CTC): x2-llm arriba, StreamTalk y gemelo abajo.
6. QuartzNet con 2 hilos de CPU y su guardia de memoria (preflight 2 GiB, stop 1.5 GiB; `align_anchor.py`).
7. VRAM no es el limite (LLM 3.9 GB + StreamTalk 2.3 GB < 12 GB). La RAM si lo es; el swap es de 2 GB.
8. El Orin NX 16 GB **no esta medido**. No asumir que caben estas configuraciones.

## 10. Solucion de problemas

| sintoma | causa | solucion |
|---|---|---|
| `FileNotFoundError` en el interprete de physicalai | `HRIEGO_PY_PHYSICALAI` no apunta a un python valido | exportar la variable (seccion 7) |
| `QuartzNet ONNX not found` o error de sha256 | `QUARTZNET_ONNX` no definido o modelo distinto del pinned | apuntar a `e66ee9f0...c3931` (seccion 5, fila 8); no usar `QUARTZNET_ALLOW_UNPINNED` salvo prueba |
| `CUDA` o `onnxruntime` no usa GPU en physicalai | `onnxruntime` y `onnxruntime-gpu` instalados a la vez | dejar una sola variante (ver `requirements/CONFLICTS.md`, punto 2) |
| `assert ... == ["CPUExecutionProvider"]` | QuartzNet pedido en GPU en el entorno equivocado | usar `physicalai` (CPU); el aligner fuerza CPU |
| `docker: command not found` o `docker exec x2-llm` falla | Docker no instalado o el contenedor no esta arriba | `docker compose ... up -d` en `llm/`; comprobar `curl /ready` dentro del contenedor |
| `podman exec x2-twin` falla | el gemelo usa podman, el LLM docker | solo afecta a `twin/` (excluido) |
| `LLM responde sin sentido` (p. ej. "Gracias por visitarnos" en FAREWELL) | 4B pequeno; el prompt pide 7-14 palabras | el reintento lo intenta; si persiste, revisar el ejemplo del prompt |
| `INVALID` con "speech must have at least 6 words" | frase corta del LLM | es el comportamiento esperado (fail-closed); no se repara |
| `trigger_text must be a literal substring of speech` | el LLM parafrasea el trigger | rechazo esperado; se reporta en `decision` |
| fila `rejected_trigger_span` en `summary.json` | `speech[start:end]` no coincide con `trigger_text` (guarda G1) | es el comportamiento esperado: corregir el manifiesto |
| CTC alinea mal los digitos | QuartzNet no cubre digitos ni simbolos | escribir numeros en palabras en `speech` (`align/README.md`) |
| `OOM` o swap al correr workers | RAM insuficiente | seccion 9 |
| `trace_check.py` o `latency_budget.py` no encuentran archivos | dependen de `twin/runs` (no incluido) | regenerar desde el gemelo o usar solo los JSON de `validation/` |
| `eval` escribe en un sitio raro | `--out` por defecto es relativo al cwd | pasar `--out` absoluto o ejecutar desde `HRIEGO_gesture/eval` |

## 11. Limitaciones

### 11.1 Bloqueos conocidos (lo que queda)

- **Workers:** `workers/` necesitan los arboles externos de Laplace (StreamTalk, c02/c03/c07, ONNX de SONIC) y de SemanticCorporeHRI (GMR, SMPL-X, BEAT2). Solo las rutas son configurables; no se incluyen copias en el repositorio.
- **Gemelo:** `validation/trace_check.py` y `validation/latency_budget.py` necesitan `twin/runs/` (no incluido). Sus JSON de salida si estan.
- **Sha256 sin fijar en el codigo:** el checkpoint de StreamTalk (sha en `SHA256SUMS`, no comprobado al cargar) y el ONNX de SONIC (pendiente). QuartzNet si esta fijado y se comprueba al cargar. Supertonic no tiene sha (`auto_download=False`).
- **Licencias por revisar:** SMPL-X (terminos del `.npz` no verificados), BEAT2 (el CSV `wayne069_audio_only_20261014` no verificado), WavLM (licencia no declarada), SONIC (pesos bajo NVIDIA Open Model License; si el ONNX de X2 cae bajo ella, no verificado), BONES-SEED (texto no leido), base NVIDIA de QuartzNet (pagina 401, no verificada). El repositorio no tiene `LICENSE` propio (seccion 5).
- **Entornos:** no hay script para los entornos conda `physicalai` y `gmr`; no se ha probado un clon limpio.


- **Robot real no probado.** Todo lo medido es en el gemelo (MC v1.0.0). El robot usa v1.1.4. Latencias, preroll de 1.0 s y planes deben medirse y regenerarse en el robot.
- **URS no sale limpio en el robot** por SDK segun `x2_ultra-real-robot-teleop/docs/09-robot-real.md` (lineas 171, 239). Hay que volver a `STAND_DEFAULT` con el mando o la app.
- **Velocidad real del brazo: 0.35 rad/s** (`docs/09-robot-real.md:157`). El overlay de apuntado del gemelo llega a 2 rad/s; el timing no se transfiere sin replanificar.
- **No es tiempo real** para NONE ni POINT: la cadena StreamTalk -> GMR -> SONIC tarda ~31 s en frio y ~6.3 s en caliente. Los presets si son tiempo real (~1.1 s desde la respuesta del LLM hasta el inicio de audio, de los que 0.37 s son preroll del MC).
- **Recall bajo** en SOCIAL_HEART (50%), APPLAUSE (75%) y NONE (81%). Las frases fuera del lexico no se reconocen.
- **Alucinaciones:** en NONE el LLM inventa datos (horario, clima) o repite la pregunta. El contrato valida forma y gesto, no contenido factual.
- **Apuntado en FRONT:** error en el instante del ancla de 14-20 grados en el gemelo para algunos casos; la media en el hold es 4-8 grados.
- **Presets mas largos que el habla:** en 15 de 20 turnos del c12 el preset dura hasta el final de la frase, asi que no queda hueco para reanudar SONIC. El stroke llega ~0.7 s tarde con la politica onset.
- **Cabeza congelada durante presets** (el MC no mueve la cabeza en presets).
- **Anclas CTC sin validacion humana** (resolucion 20 ms) y dataset de prueba pequeno (4 frases por emote), escrito por el mismo autor que el prompt. Las tasas de recall son optimistas.
- **Reconocimiento facial desactivado.** Es una limitacion de alcance, no una prueba del sistema de cara.
- **Sin audio en el gemelo.** El WAV se muxa en el video en los instantes medidos.
- **Orin NX 16 GB sin medir.**
- **Politica de alineacion:** el defecto es `--align auto`. Los planes almacenados de 4c/4e y de `visits_v2` son onset; c12 y p4 se midieron con `--align stroke`. Para reproducir cada resultado, pasar el `--align` correspondiente explicitamente.
- **Rutas en datos:** manifiestos y JSON generados usan rutas relativas a la raiz de HRIEGO_gesture (`tools/relativize_paths.py`). Los artefactos del gemelo, que no se incluyen, conservan sus rutas originales.
- **Sin `LICENSE` propio de este repositorio.** Los terceros estan en la seccion 5.
- **Codigo en movimiento:** la portabilidad (`paths.py`, `align/vendor/`, `config/`) y el cambio de politica (`auto`, G1) son posteriores a las medidas. Repetir `validation/` y `anchor_plan` antes de citar cifras nuevas.

## 12. Documentos relacionados

- `VALIDATION_4d.md`: validacion 4d completa (trazabilidad, inyecciones, latencias, recursos, gates).
- `requirements/CONFLICTS.md`: versiones y conflictos entre entornos; veredicto sobre un entorno unico.
- `align/README.md`, `workers/REPORT.md`, `visits_v2/report.md`, `contract/`: detalle de cada parte.
- `twin/SETUP.md` y `twin/replay/README.md`: montaje del gemelo X2 y replay pack.
- `motion/`: scripts vendorizados (ver `motion/PROVENANCE.md`).
- `INTEGRACION_RETOS.md`: diseno de integracion de HRIEGO_gesture en los retos 1 y 2 (Autoriente).
