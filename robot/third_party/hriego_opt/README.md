# hriego_opt — código del movimiento en línea (vendorizado, sin pesos)

Copia **solo de código y reportes** (`*.py`, `*.sh`, `*.md`, `*.yaml`, `*.txt`) de
`x2_ultra/HRIEGO_gesture/opt` (rama `Facial,-LLM-and-Gesture-integration`), para que esta rama
contenga la cadena StreamTalk → GMR → SONIC en línea que usa
`src/x2_ultra/hri_runtime/motion_online/server_proc.py`. Ese archivo apunta aquí por defecto;
`HRIEGO_OPT=<ruta>` lo redirige.

| Carpeta | Qué es |
|---|---|
| `m2_motion_server/` | motion server en un proceso (StreamTalk, GMR `native_qp` 15 Hz, SONIC en línea, primer bloque partido) |
| `o3_brain_motor/` | cerebro/motor (protocolo de cuadros 50 Hz) |
| `motor_fix/` | interlock de caída y transiciones (diagnóstico del turno 24) |
| `o1b_onnx/` | export/conversión de StreamTalk a ONNX fp16 |
| `o1a_meshes/` | construcción de los modelos `.mjb` con mallas V2 |
| `o2_cuda_stack/` | definición del entorno `.venv-hri` (ORT GPU, sin torch) |
| `t3_gmr_sonic/`, `m1_memory/` | herramientas de GMR/SONIC y de memoria usadas por el server |

## Qué NO está aquí (hay que proveerlo en la máquina que ejecuta)

- Pesos y modelos: StreamTalk ONNX fp16 (~2,75 GB), `.mjb` de IK/SONIC (`o1a_meshes/models`, ~1 GB),
  política SONIC `x2_sonic_14000_g1.onnx`, `rng_prod.pt`.
- Entornos: `.venv-hri` (x86, ORT CUDA 13) y el entorno con mink/smplx parcheado (`site_gmr`).
- Módulos y repos externos que el código importa: `paths`, `st_worker`, `x2_sonic`
  (`x2_ultra/HRIEGO_gesture`), y las raíces `GMR_ROOT`, `SONIC_ROOT`, `RETARGETER`.

## A bordo (todo local en el X2)

El objetivo es correr todo en el PC2 del robot (Jetson Orin NX 16 GB, aarch64). Este código es
portable, pero sus dependencias hoy son x86: falta construir para aarch64 ORT GPU (JetPack 6),
MuJoCo, el entorno GMR, y copiar los modelos. Ver `docs/reto-2/REPORT_DEPLOY_X2.md` §8.
