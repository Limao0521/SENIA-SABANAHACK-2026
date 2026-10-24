#!/usr/bin/env bash
# Despliegue de la pila de inferencia en la nube (x2-nube) en un VPS con GPU.
#
# Se ejecuta en el PC DE DESARROLLO (Git Bash en Windows o Linux). Sincroniza
# deploy/nube/ y el paquete mínimo de src/x2_ultra/ hacia ~/x2_nube/ en el VPS,
# genera/envía el token Bearer (nunca lo imprime), descarga los modelos, arranca
# docker compose y espera a que el gateway responda.
#
# Uso:
#   deploy/nube/desplegar_nube.sh <host_o_ip> [usuario]      # desplegar (defecto)
#   deploy/nube/desplegar_nube.sh desplegar <host_o_ip> [usuario]
#   deploy/nube/desplegar_nube.sh estado <host_o_ip> [usuario]   # compose ps + salud
#   deploy/nube/desplegar_nube.sh parar  <host_o_ip> [usuario]   # compose down
#   deploy/nube/desplegar_nube.sh logs   <host_o_ip> [usuario]   # compose logs -f
#
# Entorno:
#   NUBE_SSH_KEY     clave SSH privada (opcional; se pasa como ssh -i)
#   NUBE_PUERTO      puerto del gateway (defecto 8443; solo comprobación de salud)
#   NUBE_LLM_MODELO  GGUF a descargar (defecto Qwen3-8B-Q4_K_M.gguf)
#   Otras NUBE_* definidas localmente se copian al .env del VPS.
set -euo pipefail

RUTA_SCRIPT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd -- "$RUTA_SCRIPT/../.." && pwd)"
DIR_NUBE="$RUTA_SCRIPT"
DESTINO="x2_nube"          # ~/x2_nube en el VPS (hardcodeado también en la sonda remota)

# ---------------------------------------------------------------- argumentos
uso() {
    cat >&2 <<'EOF'
Uso:
  deploy/nube/desplegar_nube.sh [desplegar] <host_o_ip> [usuario=ubuntu]
  deploy/nube/desplegar_nube.sh estado <host_o_ip> [usuario=ubuntu]
  deploy/nube/desplegar_nube.sh parar  <host_o_ip> [usuario=ubuntu]
  deploy/nube/desplegar_nube.sh logs   <host_o_ip> [usuario=ubuntu]

Entorno: NUBE_SSH_KEY (clave SSH), NUBE_LLM_MODELO (GGUF), NUBE_PUERTO (defecto 8443).
EOF
}

ACCION="desplegar"
case "${1:-}" in
    desplegar|estado|parar|logs) ACCION="$1"; shift ;;
    -h|--help|ayuda|"") uso; exit 2 ;;
esac
HOST_VPS="${1:-}"
USUARIO="${2:-ubuntu}"
if [ -z "$HOST_VPS" ]; then
    echo "ERROR: falta el host o IP del VPS." >&2
    uso
    exit 2
fi

# ---------------------------------------------------------------- ssh
OPCIONES_SSH=(-o ConnectTimeout=10)
SSH_CMD="ssh -o ConnectTimeout=10"      # cadena para rsync -e (clave sin espacios)
if [ -n "${NUBE_SSH_KEY:-}" ]; then
    OPCIONES_SSH+=(-i "$NUBE_SSH_KEY")
    SSH_CMD="$SSH_CMD -i $NUBE_SSH_KEY"
fi
ssh_vps() { ssh "${OPCIONES_SSH[@]}" "$USUARIO@$HOST_VPS" "$@"; }

# ---------------------------------------------------------------- token
ARCHIVO_TOKEN="$DIR_NUBE/.secretos/token"

asegurar_token() {
    if [ -s "$ARCHIVO_TOKEN" ]; then
        echo "[nube] token existente: deploy/nube/.secretos/token (no se muestra)"
        return 0
    fi
    if ! command -v openssl >/dev/null 2>&1; then
        echo "ERROR: se necesita openssl para generar el token." >&2
        exit 1
    fi
    mkdir -p "$DIR_NUBE/.secretos"
    chmod 700 "$DIR_NUBE/.secretos"
    openssl rand -hex 32 > "$ARCHIVO_TOKEN"
    chmod 600 "$ARCHIVO_TOKEN"
    echo "[nube] token nuevo generado en deploy/nube/.secretos/token (no se muestra)"
}

leer_token() { tr -d ' \r\n' < "$ARCHIVO_TOKEN"; }

# ---------------------------------------------------------------- sincronía
sincronizar() {
    echo "[nube] sincronizando código hacia ~/$DESTINO/ en $USUARIO@$HOST_VPS…"
    if command -v rsync >/dev/null 2>&1 && ssh_vps 'command -v rsync >/dev/null 2>&1'; then
        ssh_vps "mkdir -p ~/$DESTINO/deploy ~/$DESTINO/src/x2_ultra"
        # Sin --delete de .secretos/modelos/.env: quedan excluidos y el VPS conserva
        # los suyos (modelos descargados, token ya escrito).
        rsync -az --delete --exclude '.secretos' --exclude 'modelos' --exclude '.env' \
              --exclude '__pycache__' -e "$SSH_CMD" \
              "$DIR_NUBE/" "$USUARIO@$HOST_VPS:$DESTINO/deploy/nube/"
        rsync -az -e "$SSH_CMD" \
              "$REPO/src/x2_ultra/__init__.py" "$USUARIO@$HOST_VPS:$DESTINO/src/x2_ultra/__init__.py"
        rsync -az --delete --exclude '__pycache__' -e "$SSH_CMD" \
              "$REPO/src/x2_ultra/hri_runtime/" "$USUARIO@$HOST_VPS:$DESTINO/src/x2_ultra/hri_runtime/"
    else
        echo "[nube] rsync no disponible (local o remoto); alternativa: tar sobre ssh"
        ssh_vps "mkdir -p ~/$DESTINO"
        # Mismas exclusiones; conserva rutas relativas (deploy/nube/…, src/x2_ultra/…).
        (cd "$REPO" && tar -cf - \
            --exclude 'deploy/nube/.secretos' \
            --exclude 'deploy/nube/modelos' \
            --exclude 'deploy/nube/.env' \
            --exclude '__pycache__' \
            deploy/nube src/x2_ultra/__init__.py src/x2_ultra/hri_runtime) \
          | ssh_vps "tar -xf - -C ~/$DESTINO"
    fi
    echo "[nube] código sincronizado."
}

# ---------------------------------------------------------------- .env remoto
escribir_env_remoto() {
    echo "[nube] escribiendo ~/x2_nube/deploy/nube/.env en el VPS (token vía stdin;" >&2
    echo "[nube] nunca aparece en la línea de comandos)…"
    {
        printf 'HRI_NUBE_TOKEN=%s\n' "$(leer_token)"
        # Variables NUBE_* definidas localmente: se trasladan al VPS tal cual.
        local v
        for v in NUBE_LLM_MODELO NUBE_LLM_CTX NUBE_LLM_PARALLEL NUBE_LLM_EXTRA \
                 NUBE_ASR_MODELO NUBE_ASR_COMPUTE NUBE_TTS_AUTO_DOWNLOAD \
                 NUBE_TTS_STEPS NUBE_TTS_THREADS NUBE_SITIO NUBE_PUERTO NUBE_BIND; do
            if [ -n "${!v:-}" ]; then printf '%s=%s\n' "$v" "${!v}"; fi
        done
    } | ssh_vps "umask 077; cat > ~/$DESTINO/deploy/nube/.env; chmod 600 ~/$DESTINO/deploy/nube/.env"
}

# ---------------------------------------------------------------- salud
# Sonda ejecutada EN EL VPS: lee el token del .env remoto y consulta el gateway
# por localhost. Modo "poll": reintenta hasta 15 min (carga del GGUF + calentar
# motores de voz). Modo "una": un solo intento informativo (subcomando estado).
sondear_salud() {
    ssh_vps bash -s -- "$1" <<'SONDA'
set -u
cd "$HOME/x2_nube/deploy/nube" || { echo "[nube] ERROR: falta ~/x2_nube/deploy/nube" >&2; exit 2; }
# shellcheck disable=SC1091
. ./.env || { echo "[nube] ERROR: no se pudo leer .env en el VPS" >&2; exit 2; }
PUERTO="${NUBE_PUERTO:-8443}"
MODO="${1:-una}"

probar() {  # probar <ruta> → 0 si el gateway responde 200 con el token Bearer
    curl -fsS -m 8 -H "Authorization: Bearer $HRI_NUBE_TOKEN" "http://127.0.0.1:$PUERTO$1" >/dev/null 2>&1
}

if [ "$MODO" = "poll" ]; then
    fin=$(( $(date +%s) + 900 ))
    listo_llm=0; listo_voz=0
    while [ "$(date +%s)" -lt "$fin" ]; do
        if [ "$listo_llm" -eq 0 ] && probar /llm/health; then listo_llm=1; echo "[nube] LLM responde (/llm/health)"; fi
        if [ "$listo_voz" -eq 0 ] && probar /voz/health; then listo_voz=1; echo "[nube] voz responde (/voz/health)"; fi
        if [ "$listo_llm" -eq 1 ] && [ "$listo_voz" -eq 1 ]; then
            echo "[nube] gateway sano: /llm/health y /voz/health responden con el token."
            exit 0
        fi
        sleep 10
    done
    echo "[nube] ERROR: el gateway no respondió a tiempo (llm=$listo_llm voz=$listo_voz)." >&2
    echo "[nube] Revise en el VPS: docker compose -f compose.nube.yml logs --tail 200" >&2
    exit 1
fi

rc=0
probar /llm/health && echo "[nube] LLM: OK" || { echo "[nube] LLM: sin respuesta"; rc=1; }
probar /voz/health && echo "[nube] voz: OK" || { echo "[nube] voz: sin respuesta"; rc=1; }
exit "$rc"
SONDA
}

# ---------------------------------------------------------------- mensaje final
siguientes_pasos() {
    cat <<'EOF'
[nube] ── Despliegue completado ─────────────────────────────────────────────────
[nube] Gateway activo en el VPS (HTTP plano por defecto, puerto 8443: úselo solo
[nube] dentro del túnel WireGuard; para HTTPS con dominio, vea README.md).
[nube]
[nube] 1) Conectividad — elija un modo (detalle en deploy/nube/README.md):
[nube]    A. Robot SIN internet: túnel WireGuard en el PC de desarrollo
[nube]       (deploy/nube/wireguard/generar_wg.sh) + relé de puertos en el PC
[nube]       (deploy/nube/pc_relay.ps1, como Administrador). En el robot:
[nube]         HRI_NUBE_URL=http://10.0.1.50:8443
[nube]    B. Robot con internet propio y dominio: NUBE_SITIO=nube.midominio.co,
[nube]       puertos 80/443 publicados en compose.nube.yml, DNS apuntando al VPS
[nube]       y vuelva a ejecutar este script. En el robot:
[nube]         HRI_NUBE_URL=https://nube.midominio.co
[nube]    C. WireGuard directamente en el robot (importe wg-cliente.conf).
[nube]       En el robot: HRI_NUBE_URL=http://10.66.0.1:8443
[nube]
[nube] 2) Variables del robot (el token está en deploy/nube/.secretos/token y NO
[nube]    se imprime aquí):
[nube]         export HRI_NUBE_URL=<según el modo elegido>
[nube]         export HRI_NUBE_TOKEN="$(cat deploy/nube/.secretos/token)"
[nube]         export HRI_NUBE_ETAPAS=llm,asr,tts    # opcional: subconjunto de etapas
[nube]
[nube] 3) Verificación desde el robot (salud y RTT de cada endpoint):
[nube]         python3 -m x2_ultra.hri_runtime.nube
[nube]
[nube] 4) Para desactivar la nube: unset HRI_NUBE_URL (todo vuelve a bordo).
[nube] ───────────────────────────────────────────────────────────────────────────
EOF
}

# ---------------------------------------------------------------- acciones
case "$ACCION" in
    desplegar)
        asegurar_token
        sincronizar
        escribir_env_remoto
        echo "[nube] descargando modelos en el VPS (puede tardar varios minutos)…"
        ssh_vps "cd ~/$DESTINO/deploy/nube && bash descargar_modelos.sh '${NUBE_LLM_MODELO:-Qwen3-8B-Q4_K_M.gguf}'"
        echo "[nube] construyendo imagen y arrancando la pila (docker compose up -d --build)…"
        ssh_vps "cd ~/$DESTINO/deploy/nube && docker compose -f compose.nube.yml up -d --build"
        echo "[nube] esperando la salud del gateway (hasta 15 min: carga del GGUF y calentamiento de voz)…"
        sondear_salud poll
        siguientes_pasos
        ;;
    estado)
        ssh_vps "cd ~/$DESTINO/deploy/nube && docker compose -f compose.nube.yml ps"
        sondear_salud una
        ;;
    parar)
        ssh_vps "cd ~/$DESTINO/deploy/nube && docker compose -f compose.nube.yml down"
        echo "[nube] pila detenida en el VPS (los modelos y el token se conservan)."
        ;;
    logs)
        ssh_vps "cd ~/$DESTINO/deploy/nube && docker compose -f compose.nube.yml logs --tail 200 -f"
        ;;
esac
