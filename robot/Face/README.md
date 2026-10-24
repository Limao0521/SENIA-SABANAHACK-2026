# Face — reconocimiento institucional local

Este subproyecto funciona dentro de `X2_ultra` y usa la cámara conectada a la PC. No necesita topics, bridge, túnel SSH ni servicios del robot. Incluye captura webcam, UniFace, BYTETracker, confirmación temporal de identidad, consulta de perfiles en SQLite y dashboard.

## Requisitos del sistema

- Linux x86_64 con escritorio gráfico activo para mostrar la ventana.
- Python 3.12 y `venv`/`pip`.
- Una webcam accesible por OpenCV. En Linux, el usuario debe tener permiso para abrir `/dev/video*`.
- Para aceleración: GPU NVIDIA, controlador instalado y entorno CUDA compatible con los paquetes cu130 de PyTorch y ONNX Runtime. CPU funciona como alternativa, con menor velocidad.
- Los modelos RetinaFace y EdgeFace están en `thirdparty/uniface-models/`. UniFace está incluido en `thirdparty/uniface/uniface/`; no hace falta instalar `uniface` desde PyPI.

## Paquetes Python

Los archivos `requirements/` fijan estas versiones:

| Paquete | Versión | Uso |
| --- | --- | --- |
| `numpy` | 2.5.4 | Vectores de embeddings y cálculo matricial |
| `opencv-python` | 5.0.0.93 | Cámara, procesamiento de imagen y ventana |
| `scikit-image` | 0.26.0 | Utilidades de alineación de UniFace |
| `scipy` | 1.18.1 | Asociación Hungarian de BYTETracker |
| `requests` | 2.34.2 | Descarga de modelos UniFace cuando faltan |
| `tqdm` | 4.70.1 | Descargas de modelos |
| `onnxruntime` **o** `onnxruntime-gpu` | 1.31.0 | Inferencia facial en CPU o NVIDIA GPU |
| `torch` (solo GPU) | 2.14.1+cu130 | Bibliotecas CUDA para el proveedor GPU |
| `torchvision` (solo GPU) | 0.29.1+cu130 | Dependencia de Torch para CUDA 13.0 |

`sqlite3` y el resto de módulos de infraestructura que usa Face vienen con Python; no se instalan con pip. Instala **una sola** variante de ONNX Runtime por entorno: CPU o GPU, nunca ambas.

## Crear e instalar el entorno

Desde `X2_ultra/Face`, crea el entorno con Python del sistema. Usar la ruta absoluta evita mezclar el ejecutable del sistema con la biblioteca estándar de un Conda activo, que puede romper imports binarios como `ctypes` y SciPy.

```bash
cd /home/luis/GitHub/x2_ultra/Face
/usr/bin/python3.12 -m venv .venv-face
.venv-face/bin/python -m pip install --upgrade pip
```

### Opción A: NVIDIA GPU

```bash
.venv-face/bin/python -m pip install -r requirements/live-gpu-cu130.txt
```

Este archivo instala las versiones GPU de PyTorch, TorchVision y ONNX Runtime, además de los paquetes comunes. Si el entorno no encuentra CUDA, ONNX Runtime puede intentar continuar en CPU.

### Opción B: CPU

```bash
.venv-face/bin/python -m pip install -r requirements/live-cpu.txt
```

No instales la variante CPU y GPU en el mismo entorno. Si vas a cambiar de variante, crea un entorno virtual nuevo.

### Verificar la instalación

```bash
.venv-face/bin/python -c 'import ctypes, cv2, numpy, scipy, skimage, onnxruntime; print("Python y dependencias importados")'
.venv-face/bin/python -c 'import onnxruntime as ort; print(ort.get_available_providers())'
```

Para la variante GPU, la lista debe incluir `CUDAExecutionProvider`. Si solo aparece `CPUExecutionProvider`, Face aún puede ejecutarse, pero la inferencia será por CPU.

## Ejecutar la cámara

Desde la raíz de `X2_ultra`:

```bash
cd Face
PYTHONPATH="$PWD/thirdparty/uniface" \
UNIFACE_CACHE_DIR="$PWD/thirdparty/uniface-models" \
.venv-face/bin/python -m live.main
```

El modo predeterminado es webcam local, índice 0, y dashboard institucional. Para otra cámara, añade `--camera-index 1`. También se admite un archivo de video con `--source video --path /ruta/video.mp4`. Q o Esc cierran la ventana; R reinicia el seguimiento.

## Registro y privacidad

`data/institutional_identity.sqlite3` es una copia consistente de la base institucional trasladada. Conserva las cinco personas y sus embeddings registrados. Las plantillas se guardan en SQLite y se cargan a RAM para búsqueda durante la sesión; los cuadros y embeddings no se guardan en los logs.

La base contiene datos biométricos y está versionada en esta rama para que el clon de HRIEGO v2 pueda arrancar con la misma galería. Quien publique o distribuya la rama debe controlar el acceso a estos datos. Los pesos y logs siguen excluidos de Git. En la instalación, se recomienda restringir la base al usuario local (`chmod 600 Face/data/institutional_identity.sqlite3`). El código de UniFace está en `thirdparty/uniface/uniface/` con su licencia MIT.

Comandos de administración desde `Face/`:

```bash
PYTHONPATH="$PWD/thirdparty/uniface" .venv-face/bin/python -m live.registry_cli list
PYTHONPATH="$PWD/thirdparty/uniface" .venv-face/bin/python -m live.registry_cli get P005
PYTHONPATH="$PWD/thirdparty/uniface" .venv-face/bin/python -m live.registry_cli set-rank P005 VIP --changed-by local-admin
```
