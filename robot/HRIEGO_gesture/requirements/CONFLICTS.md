# Conflictos de versiones entre entornos (HRIEGO_gesture)

Fecha: 2026-10-22. Analisis de las versiones congeladas exportadas en este mismo directorio. **No se ha creado, resuelto ni instalado ningun entorno** (solo lectura: `conda env export --no-builds` y `pip freeze`). Los archivos de origen son:

- `physicalai.yml`, `physicalai.txt` (conda `physicalai`, Python 3.12.14)
- `gmr.yml`, `gmr.txt` (conda `gmr`, Python 3.10.21)
- `face-venv.txt` (`Face/.venv-face`, Python 3.12.14)
- `supertonic-venv.txt` (`experiments/hriego_v2_tts_eval_20261021/.venv`, Python 3.12.3)
- `environment.total.yml` (borrador de entorno unico, no resuelto)

Conda `laplace` no se usa por el codigo incluido (solo `twin/phaseA_sonic_emote.py`, excluido del commit). No se exporta.

## 1. Matriz de versiones clave

| paquete | Face `.venv-face` | Supertonic venv | conda `physicalai` | conda `gmr` | referencia upstream |
|---|---|---|---|---|---|
| Python | 3.12.14 | 3.12.3 | 3.12.14 | **3.10.21** | StreamTalk `environment.yml`: **3.9**; E4 CTC `environment/requirements.txt`: 3.12.3 |
| torch | 2.14.1+cu130 | - | 2.11.0+cu128 | 2.14.0 (libs nvidia cu13) | StreamTalk: 2.0.0 (cu11.8) |
| torchvision | 0.29.1+cu130 | - | 0.26.0+cu128 | - | - |
| onnxruntime | - | 1.31.0 (CPU) | **1.30.0 (CPU) y 1.26.0 (gpu) a la vez** | 1.23.2 (CPU) | E4 CTC: 1.23.2 (CPU) |
| onnxruntime-gpu | 1.31.0 | - | 1.26.0 | - | - |
| onnx | - | - | 1.23.0 | - | E4 CTC: 1.19.0 |
| numpy | 2.5.4 | 2.5.4 | 2.3.1 | 2.2.6 | E4 CTC: 1.26.4; StreamTalk: 1.26.4 |
| scipy | 1.18.1 | - | 1.17.0 | 1.15.3 | - |
| mujoco | - | - | 3.11.0 (+ mujoco-warp 3.11.0) | 3.13.0 | - |
| mink | - | - | - | 1.3.0 | - |
| smplx | - | - | 0.1.28 | git `1265df7` | - |
| transformers | - | - | 5.17.0 | - | StreamTalk: no fijado |
| huggingface_hub | - | 2.2.0 | 1.32.0 | - | - |
| librosa | - | - | 1.0.0 | - | StreamTalk: no fijado |
| supertonic | - | 1.3.1 | - | - | - |
| pandas | - | - | local `file:///...croot` (no portable) | 2.3.3 | - |
| pillow | 13.0.0 | - | 12.3.0 | 12.3.0 | - |
| opencv | opencv-python 5.0.0.93 | - | opencv-python-headless-noffmpeg 4.14.0.94rc1 | opencv-python 5.0.0.93 | - |
| GMR | - | - | - | git `bb1bbe4` | - |

Fuentes: los freezes citados; StreamTalk `Gesture/third_party/StreamTalk/environment.yml`; E4 `voice/experimental/hri_temporal_alignment_20261020/E4_COMPACT_CTC/environment/requirements.txt`.

## 2. Conflictos concretos (ordenados por gravedad)

1. **Python 3.10 frente a 3.12** (`gmr` frente al resto). GMR se usa en su propio proceso (`workers/wlib.py` lanza cada worker con su propio `python`), asi que no hay conflicto de importacion. Pero el entorno unico no puede tener 3.10 y 3.12 a la vez. No se ha probado GMR en 3.12 (el commit `bb1bbe4` no lo declara).
2. **`physicalai` tiene `onnxruntime` (CPU) y `onnxruntime-gpu` instalados a la vez.** Los dos paquetes comparten el modulo `onnxruntime`; el wheel gpu sobrescribe al CPU y es una instalacion inconsistente. La validacion del CTC (`anchor_plan.py`) exige `CPUExecutionProvider` (`Aligner` en `align_anchor.py`, que hace `assert ... == ["CPUExecutionProvider"]`), asi que hay que comprobar cual queda activo. **Accion:** dejar una sola variante por entorno.
3. **Versiones de `torch` y CUDA**: cu130 (Face, gmr) frente a cu128 (physicalai); `torch` 2.14.1 frente a 2.11.0. Un solo entorno solo puede tener un indice CUDA. Los workers usan GPU (StreamTalk, SONIC): el driver del host debe soportar la version elegida. **No verificado** en este host.
4. **`numpy`**: 2.5.4 (Face, Supertonic), 2.3.1 (physicalai), 2.2.6 (gmr), 1.26.4 (E4 CTC y StreamTalk upstream). `numba==0.67.0` (physicalai) y `librosa 1.0.0` deben soportar numpy 2.5; **no verificado**. La cadena QuartzNet (E4) se valido con numpy 1.26.4 y onnxruntime 1.23.2, no con 2.3.1 ni 2.5.4.
5. **`onnxruntime` 1.23.2 (E4 CTC y gmr) frente a 1.30/1.31 (physicalai, Face)**. La alineacion CTC medida en `VALIDATION_4d.md` se ejecuto con `physicalai` (ORT 1.30.0 CPU segun el freeze). El entorno E4 se valido con 1.23.2. **No hay prueba de que los anclajes sean identicos con otra version de ORT.** Accion: si se unifica, repetir `validation/latency_ctc.py` y comparar anclajes.
6. **`mujoco`**: 3.11.0 (physicalai, donde se ejecuta el rollout SONIC en `sonic_worker.py`) frente a 3.13.0 (gmr). `mujoco-warp 3.11.0` esta fijado a 3.11. Unificar a 3.11 rompe gmr solo si GMR usa API nueva (no verificado).
7. **`huggingface_hub`**: 2.2.0 (Face/Supertonic) frente a 1.32.0 (physicalai). `transformers 5.17.0` (WavLM de StreamTalk) exige un hub reciente. **Probable conflicto** de resolucion en un entorno unico. No verificado.
8. **`smplx`**: 0.1.28 (PyPI, physicalai) frente a git `1265df7` (gmr). Distintas APIs posibles; `workers/st_worker.py` importa `smplx` (StreamTalk). Verificar cual usa cada worker.
9. **`scipy`**: 1.18.1 (Face), 1.17.0 (physicalai), 1.15.3 (gmr). Sin conflicto duro entre si, pero no hay un valor comun probado.
10. **`pandas`**: en `physicalai` es un wheel local (`file:///home/task_179000109303435/croot/...`), no reproducible en otra maquina. Usar `pandas==2.3.3` (gmr) o la version de PyPI equivalente.
11. **`opencv`**: physicalai usa `opencv-python-headless-noffmpeg 4.14.0.94rc1` y Face/gmr usan `opencv-python 5.0.0.93`. Ambos exponen el modulo `cv2`; no hay duplicado dentro de un mismo entorno, pero el set unico debe elegir uno (y la version de `cv2` no se ha probado con el resto).
12. **`pillow`**: 13.0.0 (Face) frente a 12.3.0 (physicalai, gmr). Menor.
13. **uniface** no aparece en ningun freeze: Face lo carga desde `Face/thirdparty/uniface` via `PYTHONPATH` (`setup_hriego_v2.sh`). Solo lo usa el sistema de cara, que esta desactivado (`run_visit.PROFILES`); el codigo incluido no lo importa directamente. No es un conflicto de version, pero hay que documentarlo.
14. **StreamTalk upstream (py3.9, torch 2.0.0)** es incompatible con todos los entornos locales. Nuestro worker corre en `physicalai` (torch 2.11), no con el `environment.yml` upstream. Esto no es un bug, pero el `environment.yml` de StreamTalk **no** describe el entorno usado en las medidas.

Fuera de Python (no son conflictos de version, pero afectan a la reproduccion): Docker para `x2-llm` (`llm_bridge.py`), VRAM (3.9 GB LLM + 2.3 GB StreamTalk), y el driver NVIDIA (no versionado en el repo).

## 3. Entornos que necesita el codigo incluido

| entorno | quien lo usa (incluido) | motivo |
|---|---|---|
| `Face/.venv-face` (py 3.12) | `run_visit.py`, `run_visits_v2.py`, `eval/run_eval.py`, `validation/trace_check.py`, `validation/failclosed.py` | HRIEGO v2 (`hriego_v2.*`), contrato, validador, LLM bridge |
| venv Supertonic (py 3.12.3) | `tts.py` via `hriego_v2/v2_workers.py:439` (`supertonic_python`), `align/gen_test_wavs.py` | TTS M1 |
| `physicalai` (py 3.12.14) | `align/anchor_plan.py` (subprocess desde `run_visit.py:128`), `validation/latency_ctc.py`, `validation/failclosed_anchor.py`, `workers/st_worker.py`, `workers/sonic_worker.py`, `workers/bench.py` | QuartzNet CTC (c09), StreamTalk, SONIC |
| `gmr` (py 3.10.21) | `workers/gmr_worker.py` (via `workers/wlib.py`) | GMR retargeting |
| `laplace` | ninguno (solo twin, excluido) | - |

## 4. Verdict

**Un entorno unico NO es viable con las versiones medidas.** Bloqueadores: (a) Python 3.10 frente a 3.12; (b) torch cu130 frente a cu128; (c) `onnxruntime` y `onnxruntime-gpu` juntos; (d) `huggingface_hub` 2.2.0 frente a 1.32.0 y `transformers` 5.17; (e) `mujoco` 3.11 frente a 3.13 y `smplx` PyPI frente a git.

**Set minimo recomendado: 4 entornos, que es lo que el codigo ya asume** (los workers corren como procesos separados con su propio interprete, `workers/wlib.py:4-5`):
1. `Face/.venv-face` (HRIEGO, eval, validacion trace/failclosed).
2. venv Supertonic (TTS).
3. `physicalai` (CTC, StreamTalk, SONIC). Quitar `onnxruntime` CPU o `onnxruntime-gpu`, dejar uno; quitar `opencv-python-headless-noffmpeg`; reemplazar `pandas` local.
4. `gmr` (solo GMR).

**Posible reduccion a 3:** unir Supertonic dentro de `Face/.venv-face` (ambos en py 3.12, numpy 2.5.4, onnxruntime 1.31). Es plausible pero **no probado**; requiere instalar `supertonic==1.3.1` en el venv de Face y repetir `eval/run_eval` con `--no-retry` para comparar. No se hizo.

**Para un entorno unico hay que decidir y validar**, en este orden: (1) la version de CUDA/torch del host, (2) una sola `onnxruntime`, (3) `huggingface_hub`/`transformers` compatibles con StreamTalk, (4) GMR en py3.12 con `mujoco` comun. Hasta entonces, `environment.total.yml` es solo un borrador.

## 5. Archivos de este directorio

- `physicalai.yml`, `physicalai.txt`, `gmr.yml`, `gmr.txt`, `face-venv.txt`, `supertonic-venv.txt`: exportaciones reales (sin builds).
- `environment.total.yml`: borrador unificado, no resuelto.
- `CONFLICTS.md`: este documento.
