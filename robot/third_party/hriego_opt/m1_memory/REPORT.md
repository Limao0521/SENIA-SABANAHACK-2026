# M1 — Auditoría y dieta de memoria (x86, 2026-10-22)

Medido en la PC de desarrollo (x86, RTX 12 GB). En la Orin NX la memoria es unificada: "unificado" = RAM (PSS) + VRAM.
Corridas únicas, sin varianza. El orquestador no se midió (se asume 0.5 GB). Scripts y logs en esta carpeta (`*_probe.py`, `merged.py`, `mj_*.py`, `cmp*.py`, `logs/`). `out/` (1.1 GB) no se commitea.

## Hallazgos principales (por GB ahorrado)
1. **El parseo del XML de MuJoCo es el mayor costo oculto.** IK de X2 y MJCF de SONIC comparten mallas (~977k vértices); cada parseo cuesta ~1.8 GB de RSS (caché global de assets + heap que glibc no devuelve). GMR reparsea por clip y SONIC dos veces por clip.
   Con un `.mjb` precompilado (`mj_saveModel` → `MjModel.from_binary_path`) la salida es idéntica (qpos y traza: diff 0.0).
   GMR estable 2644 → 919 MB (pico 4258 → 1502); SONIC 1835 → 285 MB (pico 3481 → 1001). Quitar geoms/mallas solo visuales: modelo IK 340 → 227 MB, misma IK. El `.mjb` depende de la versión de MuJoCo.
2. **StreamTalk + GMR + SONIC en un solo proceso (physicalai), en versión liviana: 2.72 GB RAM + 1.35 GB VRAM = 4.07 GB unificados** (3 procesos livianos ≈ 5.2 GB; los 3 workers actuales ≈ 9.3 GB).
3. **Cada proceso con CUDA paga ~1.1 GB fijos de RAM** (contexto + cuBLAS/cuBLASLt/cuDNN). Importar torch/transformers/librosa suma 0.86 GB. Solo compartir proceso lo elimina.
4. **Qwen (llama-server solo): 0.78 GB PSS + 3.93 GB VRAM = 4.7 GB.** uvicorn/RAG (1.1 GB) no se usa en la ruta de gestos.
5. **UniFace sin modificar: 1.65 GB PSS + 0.50 GB VRAM = 2.15 GB** (medido, no tocado).

## 1. StreamTalk
| paso | PSS MB | VRAM MB |
|---|---|---|
| import torch/scipy/pandas | 634 | 0 |
| import transformers/librosa/runner | 859 | 0 |
| contexto CUDA | 983 | 156 |
| pesos del generador (fp32) | 1162 | 510 |
| SMPL-X | 1163 | 606 |
| retrieval en CPU (160 173 cuadros, cKDTree) | 1612 | 606 |
| retrieval en GPU | 1420 | 994 |
| WavLM-large (fp32) | 1426 | 2094 |
| generación de calentamiento | 2421 | 2288 |
| tras 3 clips | 2489 | 2288 |

Variantes (3 clips, semilla 1; error medio en grados contra producción fp16; referencia entre semillas 10.7–12.7°):
| variante | PSS | VRAM | unificado | pico RSS | carga | error |
|---|---|---|---|---|---|---|
| producción | 2489 | 2288 | 4.66 GB | 4252 | 19.6 s | – |
| `.half()` tras cargar | 2496 | 2004 | 4.4 | 4279 | 19.4 | 3.82 / 0.64 / 0.21 |
| fp16 directo (`torch_dtype`) | 3095 | 1544 | 4.5 | 4184 | 20.0 | igual |
| sin cKDTree ni arrays CPU | 2318 | 2288 | 4.4 | 4012 | 19.7 | 0.007 (idéntico) |
| lean = `.half()` + sin cKDTree + trim + empty_cache | 2276 | 1564 | 3.7 | 4016 | 20.2 | igual a `.half()` |
| **lean + tabla de poses mmap** | **2277** | **1348** | **3.63** | **2355** | **8.0 s** | igual a `.half()` |

- El retrieval sí se necesita en cada pedido (elige la pose clave); solo la tabla L1 en GPU (53 MB fp16). La tabla 6D (211 MB) se puede mapear desde `.npy` con salida idéntica. El cKDTree y los arrays CPU no se usan tras cargar.
- `.half()`: 0.2–0.6° en 2 clips y 3.8° en uno (cambio de pose clave en el retrieval, no deriva); −12 % de latencia. Cargar WavLM con `torch_dtype` cuesta +0.6 GB de RAM: usar `.half()` después. `SimpleSpeechModel` queda en fp32. Restaurar el estado RNG de producción (`logs/rng_prod.pt`).

## 2. GMR (env gmr, py3.10, torch en CPU)
- Actual: import torch 496 → mujoco/mink/GMR ~588 → SMPL-X 668 → **MjModel desde XML 2538 (+1.87 GB)**; cada pedido reparsea (3.2–3.7 GB, pico 4258). Un segundo SMPL-X neutro (+75 MB) se crea perezosamente.
- Lean (`ik_lean.mjb` precompilado, mismo SMPL-X, trim): estable 919, pico 1502, salida idéntica, 1.22–2.01 s por clip (antes 1.51–2.30 s).
- GMR sin torch es factible (FK en numpy con 55 articulaciones de reposo y dos constantes de estatura); ahorraría ~0.6 GB solo si GMR sigue como proceso aparte. No implementado.

## 3. SONIC (physicalai, ONNX en CPU, sin torch)
- Sesión ONNX: 211–268 MB. Worker actual: 1771 estable (pico 3481), por el parseo XML en `convert_one` y el rollout.
- Lean (`sonic_x2_ultra.mjb`): 221 entre pedidos, pico 1001. En línea (ONNX + modelo + buffer) ≈ 0.65 GB.

## 4. Proceso único (`merged.py`)
- 2725 RSS (anon 2049, file 657) + 1348 VRAM = **4.07 GB**; pico RSS 3572; carga ~27 s. Acumulado: StreamTalk 2288 → +GMR 2642 → +SONIC 2725.
- GMR en physicalai sin instalar nada requiere: `mink` (no está en physicalai), el `smplx` parcheado (el 0.1.28 de physicalai solo acepta 10 betas) y un `.mjb` para mujoco 3.11. Con symlinks de PYTHONPATH a los paquetes del env gmr: QC pasa en 3 clips, qpos a 0.03–0.05 rad del run en 3.13 (daqp distinto). **GMR ~2× más lento ahí (2.8–4.8 s)**, causa no investigada.
- Riesgos: hay que vendorizar mink y smplx parcheado (o usar FK numpy), hilos para GPU (StreamTalk) y QP (GMR), un supervisor (una caída tumba todo), serializar GMR/SONIC con trim.

## 5. Qwen (solo llama-server, 16k, KV q8_0, ngl 99)
| estado | RSS | PSS | anon | file | VRAM |
|---|---|---|---|---|---|
| reposo | 741 | 739 | 234 | 458 | 3.90 GB |
| tras 3 pedidos | 778 | 776 | 265 | 463 | 3.93 GB |

Total 4.7 GB unificados. No usar `--no-mmap`.

## 6. UniFace (sin modificar; `LiveRecognizer` sobre 8 imágenes fijas)
| paso | PSS | VRAM |
|---|---|---|
| import (uniface, onnxruntime-gpu) | 731 | 0 |
| recognizer construido (CUDA EP) | 934 | 212 |
| tras la primera inferencia | 1653 | 496 |

2.15 GB; ~18 ms por imagen en caliente. `.venv-face` usa CUDA 13 (torch 2.14) y physicalai cu128: no comparten librerías hoy. Decisión del dueño de Face (CPU EP, TensorRT o alinear CUDA).

## 7. Supertonic y QuartzNet
Supertonic: PSS 463–504, ~0.95 s para 6.4 s de audio. QuartzNet: +~89 MB por sesión, 44 ms. Juntos ~0.65 GB.

## 8. Qué se puede quitar o cambiar sin riesgo
| cambio | ahorro | evidencia | riesgo |
|---|---|---|---|
| `.mjb` precompilado (IK y SONIC) + vaciar caché de assets | ~1.7 GB (GMR) y 1.5 GB (SONIC) estables | qpos y traza idénticos | regenerar por versión de MuJoCo |
| quitar geoms/mallas visuales del modelo IK | 110 MB | diff 0.0 | mantener el XML auditado como fuente |
| SMPL-X del sujeto = el canónico | 75 MB | diff 0.0 | solo género neutro |
| StreamTalk sin cKDTree ni arrays CPU | 170 MB | idéntico | ninguno |
| tabla 6D mmap + caché `.npy` | 0.21 GB VRAM; carga 19.6 → 8 s; pico 4.25 → 2.36 GB | idéntico | paso de construcción + hash |
| `.half()` generador y WavLM | 0.8 GB VRAM, −12 % latencia | 0.2–3.8° | repetir aceptación T2/T3 a nivel X2 |
| llama-server sin uvicorn | 1.1 GB | medido | la ruta de gestos no usa RAG |

No conviene: int8 en StreamTalk, quitar el retrieval, `--no-mmap`, bf16.

## 9. Presupuesto proyectado a la Orin (GB unificados, con números x86)
| componente | actual | lean | meta |
|---|---|---|---|
| Qwen | 5.8 | 4.7 | 4.0 |
| cadena de movimiento | 9.3 | 4.07 | 2.5–3.0 |
| Supertonic + CTC | 0.65 | 0.65 | 0.6 |
| UniFace (sin modificar) | 2.15 | 2.15 | 1.0–1.5 |
| orquestador (supuesto) | 0.5 | 0.5 | 0.5 |
| **Total** | **18.4** | **12.1** | **8–10** |

Brecha: 2.1–4.1 GB. Palancas sin medir: contexto de Qwen 16k → 8k (~−0.6 GB VRAM), exportar WavLM/StreamTalk a ORT/TensorRT (~−1 GB), UniFace (decisión del dueño), menos procesos CUDA. Aterrizaje plausible ~10 GB; 8 GB requiere reexportar WavLM/StreamTalk o un LLM más pequeño.

## 10. Recomendaciones para M2 (motion server)
1. Un solo proceso physicalai persistente con StreamTalk, GMR y SONIC, secuencial por pedido, `malloc_trim` tras cada etapa, con supervisor.
2. Construir en la instalación (con hash y regenerados al cambiar mujoco/torch): `ik_lean_<mujoco>.mjb`, `sonic_x2_ultra_<mujoco>.mjb` y la caché de retrieval (`poses6d.npy`, `joints_f16.npy`).
3. Nunca `MjModel.from_xml_path` en el camino de servicio; además `mujoco.mj_setCacheCapacity(mujoco.mj_getCache(), 0)`.
4. Vendorizar `mink` y `smplx` parcheado (o FK numpy) y revisar la latencia de GMR con daqp 0.9.1 fijado.
5. Adoptar fp16 solo tras la aceptación T2/T3 a nivel X2 en los 10 clips; `.half()` después de cargar; `SimpleSpeechModel` en fp32.
6. `empty_cache` solo tras la carga; mantener el manejo de RNG de `st_worker`.
7. Repetir las mediciones de `merged.py` en la Orin primero (en memoria unificada la VRAM es RAM; las librerías CUDA de JetPack pueden cambiar el costo fijo de 1.1 GB).

Nota: había un `ollama serve` corriendo antes de M1; no lo tocó M1.
