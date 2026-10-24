#!/usr/bin/env bash
# Generación de claves y configuraciones WireGuard para la nube (x2-nube).
#
# Un servidor (el VPS, 10.66.0.1/24 en UDP 51820) y UN cliente (10.66.0.2/32:
# el PC de desarrollo en el modo A, o el robot en el modo C). Las claves se
# guardan en deploy/nube/.secretos/wg/ (git-ignorado; nunca se imprimen) y las
# configuraciones finales se renderizan desde las *.plantilla de este directorio.
#
# Uso (desde cualquier cwd):
#   deploy/nube/wireguard/generar_wg.sh <host_o_ip_del_VPS>
#
# Idempotente: si las claves ya existen, se reutilizan y solo se re-renderizan
# las configuraciones (p. ej. para actualizar el Endpoint).
set -euo pipefail

HOST_VPS="${1:-}"
if [ -z "$HOST_VPS" ]; then
    echo "Uso: deploy/nube/wireguard/generar_wg.sh <host_o_ip_del_VPS>" >&2
    exit 2
fi

if ! command -v wg >/dev/null 2>&1; then
    echo "ERROR: no se encontró el comando 'wg' (wireguard-tools)." >&2
    echo "  - Ubuntu/VPS:  sudo apt install wireguard-tools" >&2
    echo "  - Windows:     viene con el cliente WireGuard (C:\\Program Files\\WireGuardTools\\wg.exe);" >&2
    echo "                 añádalo al PATH o ejecute este script en el VPS/WSL." >&2
    exit 1
fi

RUTA_SCRIPT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DIR_WG="$(cd -- "$RUTA_SCRIPT/.." && pwd)/.secretos/wg"
mkdir -p "$DIR_WG"
chmod 700 "$DIR_WG"

# ---------------------------------------------------------------- claves
generar_clave() {   # generar_clave <archivo_privada>
    local f="$1"
    if [ -s "$f" ]; then
        echo "[wg] clave existente, se reutiliza: $(basename -- "$f")"
    else
        wg genkey > "$f"
        chmod 600 "$f"
        echo "[wg] clave privada generada: $(basename -- "$f")"
    fi
    wg pubkey < "$f" > "${f%.privada}.publica"
    chmod 600 "${f%.privada}.publica"
}

generar_clave "$DIR_WG/servidor.privada"
generar_clave "$DIR_WG/cliente.privada"

PRIV_SERVIDOR="$(tr -d ' \r\n' < "$DIR_WG/servidor.privada")"
PUB_SERVIDOR="$(tr -d ' \r\n' < "$DIR_WG/servidor.publica")"
PRIV_CLIENTE="$(tr -d ' \r\n' < "$DIR_WG/cliente.privada")"
PUB_CLIENTE="$(tr -d ' \r\n' < "$DIR_WG/cliente.publica")"

# ---------------------------------------------------------------- renderizado
renderizar() {   # renderizar <plantilla> <destino>
    local contenido
    contenido="$(< "$1")"
    # Sustitución pura de bash (sin sed): las claves base64 pueden contener / + =
    contenido="${contenido//__CLAVE_PRIVADA_SERVIDOR__/$PRIV_SERVIDOR}"
    contenido="${contenido//__CLAVE_PUBLICA_SERVIDOR__/$PUB_SERVIDOR}"
    contenido="${contenido//__CLAVE_PRIVADA_CLIENTE__/$PRIV_CLIENTE}"
    contenido="${contenido//__CLAVE_PUBLICA_CLIENTE__/$PUB_CLIENTE}"
    contenido="${contenido//__HOST_VPS__/$HOST_VPS}"
    printf '%s\n' "$contenido" > "$2"
    chmod 600 "$2"
    echo "[wg] configuración escrita: $2"
}

renderizar "$RUTA_SCRIPT/wg-nube.conf.plantilla"    "$DIR_WG/wg-nube.conf"
renderizar "$RUTA_SCRIPT/wg-cliente.conf.plantilla" "$DIR_WG/wg-cliente.conf"

# ---------------------------------------------------------------- instrucciones
cat <<EOF
[wg] ── Configuraciones listas (contienen claves: no las comparta ni las versione) ──

1) SERVIDOR (VPS). Copie wg-nube.conf y actívelo como wg0:
     scp deploy/nube/.secretos/wg/wg-nube.conf ${HOST_VPS}:/tmp/wg0.conf
     ssh ${HOST_VPS} 'sudo mv /tmp/wg0.conf /etc/wireguard/wg0.conf \\
                      && sudo chmod 600 /etc/wireguard/wg0.conf \\
                      && sudo systemctl enable --now wg-quick@wg0'
   Abra el puerto UDP 51820 en el cortafuegos/proveedor del VPS.
   (No hace falta NAT ni reenvío: el cliente solo accede al propio VPS.)

2) CLIENTE — un solo sitio según el modo de conectividad:
   - Modo A (robot sin internet): importe wg-cliente.conf en WireGuard for
     Windows en el PC de desarrollo (Add Tunnel → Import from file) y actívelo;
     después ejecute deploy/nube/pc_relay.ps1 como Administrador.
   - Modo C (túnel en el robot): copie wg-cliente.conf al robot
     (/etc/wireguard/wg-nube.conf, chmod 600) y levántelo:
       sudo wg-quick up wg-nube   (o systemctl enable --now wg-quick@wg-nube)

3) Comprobación desde el cliente:
     ping 10.66.0.1
     curl -H "Authorization: Bearer \$HRI_NUBE_TOKEN" http://10.66.0.1:8443/llm/health
EOF
