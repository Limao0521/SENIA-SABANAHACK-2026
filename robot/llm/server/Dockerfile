# Imagen única de inferencia local: llama-server (llama.cpp) + API FastAPI
# (memoria + RAG) en el MISMO contenedor.
#
# Base por defecto: llama.cpp server CPU (x86/arm64) para desarrollo.
# Overrides de compose cambian BASE_IMAGE:
#   - Jetson (robot X2 Ultra): dustynv/llama_cpp:r36.4.0   (ARM64 + CUDA)
#   - x86 con GPU:             ghcr.io/ggml-org/llama.cpp:server-cuda12
# Todas las bases son Ubuntu (apt) y multi-plataforma salvo dustynv (arm64).
ARG BASE_IMAGE=ghcr.io/ggml-org/llama.cpp:server
FROM ${BASE_IMAGE}

# Python + curl (healthcheck). Las bases ggml-org son mínimas; las dustynv ya
# traen python3, pero venv/pip pueden faltar.
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-pip python3-venv curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# venv aislado: no toca el python del sistema (las imágenes dustynv traen
# torch/opencv del jetson-containers que no queremos pisar)
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

COPY api/requirements.txt /tmp/requirements.txt
# dustynv fija PIP_INDEX_URL al índice de jetson-ai-lab (sin fastapi & co.); la API
# solo usa wheels multi-arquitectura de PyPI.
RUN pip install --no-cache-dir --index-url https://pypi.org/simple -r /tmp/requirements.txt

COPY api/app /app/app
COPY server/entrypoint.sh /entrypoint.sh
COPY server/run.sh /run.sh
# Tolerancia a CRLF (por si el build context viene de un checkout en Windows)
RUN sed -i 's/\r$//' /entrypoint.sh /run.sh && chmod +x /entrypoint.sh /run.sh

WORKDIR /app

# 8080: llama-server (API OpenAI cruda) · 8000: API con memoria + RAG
EXPOSE 8080 8000

# run.sh lanza llama-server y uvicorn, y reenvía señales a ambos
ENTRYPOINT ["/bin/bash", "/run.sh"]
