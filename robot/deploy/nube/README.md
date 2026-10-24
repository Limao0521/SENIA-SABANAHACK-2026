# Inferencia en la nube (x2-nube) — inicio rápido

Descarga opcional de LLM/ASR/TTS a un VPS con GPU NVIDIA (x86_64, Ubuntu
22.04/24.04, Docker + NVIDIA Container Toolkit). El robot conserva la pila a
bordo como respaldo automático en cada turno. Justificación completa y diseño:
`docs/reto-2/INFERENCIA_NUBE.md`.

## Despliegue (desde el PC de desarrollo)

```bash
deploy/nube/desplegar_nube.sh <host_o_ip_del_VPS> [usuario=ubuntu]
```

Sincroniza el código, genera/envía el token Bearer (`.secretos/token`, nunca se
imprime), descarga los modelos (`descargar_modelos.sh`), arranca la pila
(`docker compose -f compose.nube.yml up -d --build`) y espera la salud del
gateway. Subcomandos: `estado|parar|logs <host>`. Opcional: `NUBE_SSH_KEY`
(clave ssh -i) y `NUBE_LLM_MODELO` (GGUF). En el VPS quedan: gateway Caddy
(único puerto publicado, 8443), llama-server (`/llm/*`) y voz (`/voz/*`).

## Conectividad (elegir un modo)

- **A. Robot SIN internet (relé por el PC):** WireGuard en el PC de desarrollo
  (`wireguard/generar_wg.sh <host_vps>`; servidor en el VPS, cliente en el PC)
  y `pc_relay.ps1` como Administrador. El robot no necesita nada: usa
  `HRI_NUBE_URL=http://10.0.1.50:8443` por el cable Ethernet.
- **B. Robot con internet propio + dominio:** en el `.env` del VPS,
  `NUBE_SITIO=nube.midominio.co`, publicar 80/443 en `compose.nube.yml`, DNS al
  VPS y redeployar. Caddy gestiona Let's Encrypt. `HRI_NUBE_URL=https://nube.midominio.co`.
- **C. WireGuard en el robot:** importar `wg-cliente.conf` en el robot
  (`/etc/wireguard/wg-nube.conf`). `HRI_NUBE_URL=http://10.66.0.1:8443`.

Con `NUBE_SITIO=":8443"` (defecto) el gateway es HTTP plano: úselo SOLO dentro
del túnel WireGuard; para tráfico por internet, modo B (HTTPS).

## Variables en el robot

```bash
export HRI_NUBE_URL=http://10.0.1.50:8443      # según el modo elegido
export HRI_NUBE_TOKEN="$(cat deploy/nube/.secretos/token)"
export HRI_NUBE_ETAPAS=llm,asr,tts             # opcional: subconjunto de etapas
```

## Verificación

```bash
python3 -m x2_ultra.hri_runtime.nube   # desde el robot: salud y RTT de /llm y /voz
```

## Desactivación

`unset HRI_NUBE_URL` (o no definirlo): todo vuelve a la pila a bordo, sin más
cambios. En el VPS, `desplegar_nube.sh parar <host>` detiene la pila.

## Archivos

`compose.nube.yml` (3 servicios), `Caddyfile` (auth + rutas, SSE sin búfer),
`Dockerfile.voz` (faster-whisper + Supertonic), `descargar_modelos.sh`,
`desplegar_nube.sh`, `wireguard/` (plantillas + `generar_wg.sh`), `pc_relay.ps1`.
Secretos y modelos en `.secretos/` y `modelos/` (git-ignorados).
