#!/usr/bin/env bash
# =============================================================================
# boveda.sh — bóveda cifrada (gocryptfs) para ~/x2_ultra + registro de accesos
#             con cadena de hashes + alarma hablada por los altavoces del X2.
#
# Por qué cifrado: en el robot hay varias cuentas con sudo y contraseña compartida,
# así que los permisos de archivo no protegen nada. Solo protege el cifrado con una
# frase de paso que NO se guarda en el robot (se teclea en cada `abrir`).
#
# Disposición:
#   ~/.x2_boveda          texto cifrado (gocryptfs; incluye gocryptfs.conf)
#   ~/x2_ultra            punto de montaje (vacío con la bóveda cerrada)
#   ~/.x2_boveda_tools/   FUERA de la bóveda: esta copia del script, gocryptfs,
#                         alarma (py + wav), confianza.conf, accesos.log, pid del vigilante
#
# Uso (primero desde el repo, después SIEMPRE desde la copia instalada):
#   deploy/boveda.sh instalar                 # copia todo a ~/.x2_boveda_tools/
#   B=~/.x2_boveda_tools/boveda.sh
#   $B crear                    # una vez: cifra ~/x2_ultra (migración, pide SI)
#   $B abrir                    # pide la frase de paso y monta (sudo, -allow_other)
#   $B cerrar [--forzar]        # para x2-hri/x2-llm y desmonta
#   $B estado [N]               # abierta/cerrada, sesiones, vigilante, últimas N entradas
#   $B auditar [--desde 24h]    # logins SSH (aceptados/fallidos): CONFIABLE / DESCONOCIDO
#   $B vigilar | vigilar-parar  # vigilante en segundo plano (root, nohup)
#   $B verificar-log            # comprueba la cadena de hashes de accesos.log
#   $B copiar-log               # imprime el scp para bajar el registro al PC
#   $B generar-alarma ["texto"] # crea alarma.wav con Supertonic (bóveda abierta)
#   $B probar-alarma            # hace sonar la alarma (sin límite de frecuencia)
#   $B borrar-original          # borra ~/x2_ultra.SIN_CIFRAR tras `crear`
#
# Este script NO toca SSH, usuarios, grupos, sudoers ni servicios del sistema.
# =============================================================================
set -uo pipefail
umask 077

# ----------------------------------------------------------------------------- rutas
# Bajo sudo HOME pasa a ser /root: el "dueño" se fija con BOVEDA_OWNER_HOME o SUDO_USER.
if [[ -n ${BOVEDA_OWNER_HOME:-} ]]; then
    OWNER_HOME=$BOVEDA_OWNER_HOME
elif [[ $EUID -eq 0 && -n ${SUDO_USER:-} ]]; then
    OWNER_HOME=$(getent passwd "$SUDO_USER" | cut -d: -f6)
else
    OWNER_HOME=$HOME
fi
TOOLS=${BOVEDA_TOOLS:-$OWNER_HOME/.x2_boveda_tools}
CIPHER=${BOVEDA_CIPHER:-$OWNER_HOME/.x2_boveda}
MNT=${BOVEDA_MNT:-$OWNER_HOME/x2_ultra}
ORIGINAL="$MNT.SIN_CIFRAR"
MIGRAR_MNT="$OWNER_HOME/.x2_boveda_migrar"
GOCRYPTFS="$TOOLS/gocryptfs"
LOG="$TOOLS/accesos.log"
LOCK="$TOOLS/accesos.lock"
CONF="$TOOLS/confianza.conf"
PIDFILE="$TOOLS/vigilar.pid"
WAV="$TOOLS/alarma.wav"
ALARMA_ULT="$TOOLS/alarma.ultima"
AUTHLOG=${BOVEDA_AUTHLOG:-/var/log/auth.log}
GOCRYPTFS_SHA256=${GOCRYPTFS_SHA256:-2381c8555c8bb5ba9a12388fd16e3a35c0a1898863117cd2a3d2c4c120ab077c}
GENESIS=0000000000000000000000000000000000000000000000000000000000000000
HRI_IMAGE=${HRI_IMAGE:-x2-hri:latest}
SUPERTONIC_MODEL_REL=experiments/hriego_v2_tts_eval_20261021/model
DDS_XML=/agibot/software/entry/cfg/ros_dds_configuration.xml
TEXTO_ALARMA="Alerta. Se ha detectado un intento de acceso no autorizado al contenido de otro equipo. Acceder a la información de otros equipos sin permiso es ilegal. Este acceso ha quedado registrado."
ROBOT_SSH=${ROBOT_SSH:-agi@10.0.1.41}
SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"

die() { echo "ERROR: $*" >&2; exit 1; }
info() { echo "→ $*"; }

# ----------------------------------------------------------------------------- configuración
# confianza.conf: una entrada por línea "<tipo> <valor>"; '#' = comentario.
CONF_DEFECTO='# Identidades de confianza de la bóveda (una por línea: <tipo> <valor>).
# Un login SSH es CONFIABLE si coincide su huella de clave, su IP de origen o la MAC
# de esa IP (según `ip neigh`) con alguna entrada. Todo lo demás es DESCONOCIDO.
# Ojo: en el cable Ethernet una IP (y una MAC) se pueden suplantar; la huella de
# clave no. Si solo entras con tu clave, puedes borrar la línea "ip".
huella SHA256:7EHEBLSXqwkXx/knC8EmKtRo7fikzmEuDIdahvxkRsg
ip 10.0.1.50
# mac aa:bb:cc:dd:ee:ff
#
# Contenedores propios que pueden usar la bóveda abierta (por imagen, exacta).
imagen x2-hri:latest
imagen x2-ultra/llm:jetson
imagen x2-ultra/llm:cpu
#
# Procesos SIN sesión SSH que pueden usar la bóveda (ERE sobre el nombre del
# comando, /proc/PID/comm). Vacío por defecto: cualquiera de ellos genera alerta.
# proceso ^mi_servicio$
#
# Ajustes del vigilante
intervalo_s 5            # cada cuántos segundos se revisan los procesos
alarma_min_s 120         # como mucho una alarma hablada cada N segundos
alarma_repeticiones 1    # veces que se repite el mensaje
alarma_fallo_login 1     # 1 = alarma también con logins FALLIDOS desde IPs no confiables
alarma_abrir_fallido 1   # 1 = alarma con cada `abrir` fallido (frase incorrecta)
latido_min 60            # entrada LATIDO cada N minutos (0 = nunca); huecos = vigilante caído
'

declare -a CONF_IP=() CONF_HUELLA=() CONF_MAC=() CONF_IMAGEN=() CONF_PROCESO=()
INTERVALO=5 ALARMA_MIN_S=120 ALARMA_REP=1 ALARMA_FALLO_LOGIN=1 ALARMA_ABRIR_FALLIDO=1 LATIDO_MIN=60

cargar_conf() {
    CONF_IP=() CONF_HUELLA=() CONF_MAC=() CONF_IMAGEN=() CONF_PROCESO=()
    [[ -f $CONF ]] || return 0
    local tipo valor _resto
    while read -r tipo valor _resto; do
        [[ -z $tipo || $tipo == \#* ]] && continue
        case "$tipo" in
        ip) CONF_IP+=("$valor") ;;
        huella) CONF_HUELLA+=("$valor") ;;
        mac) CONF_MAC+=("${valor,,}") ;;
        imagen) CONF_IMAGEN+=("$valor") ;;
        proceso) CONF_PROCESO+=("$valor") ;;
        intervalo_s) [[ $valor =~ ^[0-9]+$ ]] && INTERVALO=$valor ;;
        alarma_min_s) [[ $valor =~ ^[0-9]+$ ]] && ALARMA_MIN_S=$valor ;;
        alarma_repeticiones) [[ $valor =~ ^[0-9]+$ ]] && ALARMA_REP=$valor ;;
        alarma_fallo_login) ALARMA_FALLO_LOGIN=$valor ;;
        alarma_abrir_fallido) ALARMA_ABRIR_FALLIDO=$valor ;;
        latido_min) [[ $valor =~ ^[0-9]+$ ]] && LATIDO_MIN=$valor ;;
        esac
    done <"$CONF"
    (( INTERVALO < 1 )) && INTERVALO=1
    return 0
}

en_lista() {  # en_lista valor elem...
    local v=$1 e; shift
    for e in "$@"; do [[ $v == "$e" ]] && return 0; done
    return 1
}

# CONFIABLE si coincide huella, IP o MAC con confianza.conf.
es_confiable() {  # es_confiable ip huella mac
    local ip=${1:--} huella=${2:--} mac=${3:--}
    [[ $huella != - ]] && en_lista "$huella" "${CONF_HUELLA[@]}" && return 0
    [[ $ip != - ]] && en_lista "$ip" "${CONF_IP[@]}" && return 0
    [[ $mac != - ]] && en_lista "${mac,,}" "${CONF_MAC[@]}" && return 0
    return 1
}

mac_de() {  # MAC de una IP vecina (solo si sigue en la tabla ARP/NDP; '-' si no)
    local ip=${1:--} m=
    [[ $ip == - ]] || m=$(ip neigh show "$ip" 2>/dev/null | awk '{for(i=1;i<NF;i++) if($i=="lladdr"){print $(i+1); exit}}')
    echo "${m:--}"
}

# ----------------------------------------------------------------------------- registro
# accesos.log: una línea por evento, campos "clave=valor" separados por TAB, y al final
# h=sha256(hash_anterior + línea_sin_h). Borrar, editar o reordenar líneas rompe la
# cadena y `verificar-log` lo detecta. LÍMITE HONESTO: root (cualquier cuenta con sudo)
# puede borrar el archivo entero o recalcular la cadena; la cadena solo demuestra
# manipulación si tienes una copia anterior fuera del robot (ver `copiar-log`): tu
# copia del PC debe ser siempre un PREFIJO exacto del registro del robot.
limpiar() { local v=${1//$'\t'/ }; v=${v//$'\n'/ }; v=${v//$'\r'/ }; printf '%s' "$v"; }

registrar() {  # registrar EVENTO NIVEL clave=valor...
    local evento=$1 nivel=$2 kv linea prev h
    shift 2
    linea="ts=$(date '+%Y-%m-%dT%H:%M:%S%z')"$'\t'"evento=$evento"$'\t'"nivel=$nivel"
    for kv in "$@"; do linea+=$'\t'"$(limpiar "$kv")"; done
    [[ -d $TOOLS ]] || { echo "$linea" >&2; return 0; }
    {
        flock -x 9 2>/dev/null || true
        prev=$(tail -n 1 "$LOG" 2>/dev/null | awk -F'\t' '{print $NF}')
        prev=${prev#h=}
        [[ $prev =~ ^[0-9a-f]{64}$ ]] || prev=$GENESIS
        h=$(printf '%s%s' "$prev" "$linea" | sha256sum | cut -d' ' -f1)
        printf '%s\th=%s\n' "$linea" "$h" >>"$LOG"
    } 9>>"$LOCK"
    # Si escribe root (vigilante), los archivos siguen siendo del dueño.
    [[ $EUID -eq 0 ]] && chown --reference="$TOOLS" "$LOG" "$LOCK" 2>/dev/null
    [[ $nivel == ALERTA ]] && echo "ALERTA: $evento $*" >&2
    return 0
}

verificar_log() {
    [[ -f $LOG ]] || die "no existe $LOG"
    python3 -I - "$LOG" "$GENESIS" <<'PY'
import hashlib, sys
path, prev = sys.argv[1], sys.argv[2]
n = malas = 0
with open(path, "rb") as f:
    for n, raw in enumerate(f, 1):
        linea = raw.rstrip(b"\n").decode("utf-8", "replace")
        cuerpo, sep, h = linea.rpartition("\th=")
        calc = hashlib.sha256((prev + cuerpo).encode("utf-8")).hexdigest()
        if not sep or h != calc:
            malas += 1
            print(f"ROTA en la línea {n}: {linea[:160]}")
        prev = h if sep else prev
print(f"{n} entradas · {'CADENA ÍNTEGRA' if malas == 0 else f'{malas} ROTURAS'} · último hash {prev}")
sys.exit(0 if malas == 0 else 1)
PY
}

# ----------------------------------------------------------------------------- sesiones
# Login SSH asociado a un PID de sshd ("sshd[PID]: Accepted ..."): "metodo usuario ip puerto huella"
declare -A LOGIN=()   # caché del vigilante: pid_sshd -> "metodo usuario ip puerto huella"

parse_accepted() {  # línea -> "pid metodo usuario ip puerto huella" (o nada)
    local re='sshd\[([0-9]+)\]: Accepted ([^ ]+) for ([^ ]+) from ([^ ]+) port ([0-9]+)( ssh2(: [^ ]+ ([^ ]+))?)?'
    [[ $1 =~ $re ]] || return 1
    echo "${BASH_REMATCH[1]} ${BASH_REMATCH[2]} ${BASH_REMATCH[3]} ${BASH_REMATCH[4]} ${BASH_REMATCH[5]} ${BASH_REMATCH[8]:--}"
}

buscar_login() {  # buscar_login PID_SSHD
    local pid=$1 l r
    [[ -n ${LOGIN[$pid]:-} ]] && { echo "${LOGIN[$pid]}"; return 0; }
    l=$(journalctl -q --no-pager -o short-iso "_PID=$pid" -t sshd 2>/dev/null | grep -a ": Accepted " | tail -n 1)
    [[ -z $l && -r $AUTHLOG ]] && l=$(grep -a "sshd\[$pid\]: Accepted " "$AUTHLOG" 2>/dev/null | tail -n 1)
    [[ -n $l ]] || return 1
    r=$(parse_accepted "$l") || return 1
    LOGIN[$pid]=${r#* }
    echo "${r#* }"
}

# Sesión de un proceso: sube por sus padres buscando SSH_CONNECTION en el entorno y el
# sshd que registró el login. Deja el resultado en las variables S_*.
sesion_de() {  # sesion_de PID
    local p=$1 n=0 env_ssh comm datos
    S_IP=- S_PUERTO=- S_METODO=- S_HUELLA=- S_LOGIN_USER=- S_SSHD=-
    while [[ $p =~ ^[0-9]+$ ]] && (( p > 1 && n < 40 )); do
        if [[ $S_IP == - ]]; then
            env_ssh=$( { tr '\0' '\n' <"/proc/$p/environ"; } 2>/dev/null | sed -n 's/^SSH_CONNECTION=//p' | head -n 1)
            [[ -n $env_ssh ]] && read -r S_IP S_PUERTO _ _ <<<"$env_ssh"
        fi
        comm=$(cat "/proc/$p/comm" 2>/dev/null)
        if [[ $comm == sshd ]] && datos=$(buscar_login "$p"); then
            read -r S_METODO S_LOGIN_USER S_IP S_PUERTO S_HUELLA <<<"$datos"
            S_SSHD=$p
            break
        fi
        p=$(awk '/^PPid:/{print $2}' "/proc/$p/status" 2>/dev/null)
        n=$((n + 1))
    done
    S_MAC=$(mac_de "$S_IP")
}

SES=()
sesion_actual() {  # rellena SES con los campos de registro de quien ejecuta este comando
    [[ ${#SES[@]} -gt 0 ]] && return 0
    sesion_de $$
    local t; t=$(tty 2>/dev/null) || t=-
    SES=("usuario=$(id -un)${SUDO_USER:+(sudo de $SUDO_USER)}" "ip=$S_IP" "puerto=$S_PUERTO" "mac=$S_MAC"
         "metodo=$S_METODO" "huella=$S_HUELLA" "tty=$t" "pid=$$")
}

# ----------------------------------------------------------------------------- utilidades
montada() { mountpoint -q "$MNT" 2>/dev/null; }

docker_ok() { docker info >/dev/null 2>&1; }

contenedores_corriendo() {  # nombres de x2-hri / x2-llm activos
    docker_ok || return 0
    docker ps --format '{{.Names}}' 2>/dev/null | grep -xE 'x2-hri|x2-llm' || true
}

# PIDs con cwd o descriptores abiertos dentro de la bóveda (necesita root para ver
# los de otros usuarios). Un solo `find`, barato aunque se llame cada pocos segundos.
pids_en_boveda() {
    find /proc/[0-9]*/cwd /proc/[0-9]*/fd/ -maxdepth 1 \( -lname "$MNT" -o -lname "$MNT/*" \) \
        2>/dev/null | cut -d/ -f3 | sort -un
}

requiere_instalada() {
    [[ $SELF == "$TOOLS/boveda.sh" ]] || die "ejecuta la copia instalada: $TOOLS/boveda.sh $* (primero: deploy/boveda.sh instalar)"
    [[ -x $GOCRYPTFS ]] || die "falta $GOCRYPTFS (deploy/boveda.sh instalar)"
}

confirmar_si() {  # confirmar_si "pregunta"
    local r
    read -r -p "$1 Escribe SI (en mayúsculas) para continuar: " r
    [[ $r == SI ]]
}

# ----------------------------------------------------------------------------- alarma
# Rate-limited (alarma_min_s). Se lanza en segundo plano para no bloquear al vigilante.
alarma() {  # alarma "motivo" [forzar]
    local motivo=$1 forzar=${2:-} ahora ult=0
    ahora=$(date +%s)
    [[ -f $ALARMA_ULT ]] && ult=$(cat "$ALARMA_ULT" 2>/dev/null)
    [[ $ult =~ ^[0-9]+$ ]] || ult=0
    if [[ -z $forzar ]] && (( ahora - ult < ALARMA_MIN_S )); then
        return 0
    fi
    echo "$ahora" >"$ALARMA_ULT" 2>/dev/null
    [[ $EUID -eq 0 ]] && chown --reference="$TOOLS" "$ALARMA_ULT" 2>/dev/null
    if ! docker_ok; then
        registrar ALARMA aviso "motivo=$motivo" "detalle=alarma sin audio: dockerd parado"
        return 0
    fi
    if [[ ! -f $WAV ]]; then
        registrar ALARMA aviso "motivo=$motivo" "detalle=alarma sin audio: falta $WAV (generar-alarma)"
        return 0
    fi
    if ! docker image inspect "$HRI_IMAGE" >/dev/null 2>&1; then
        registrar ALARMA aviso "motivo=$motivo" "detalle=alarma sin audio: falta la imagen $HRI_IMAGE"
        return 0
    fi
    (
        out=$(docker run --rm --name "x2-boveda-alarma-$ahora" \
            --network host --ipc host \
            -e RMW_IMPLEMENTATION=rmw_fastrtps_cpp \
            -e FASTRTPS_DEFAULT_PROFILES_FILE="$DDS_XML" \
            -e ROS_LOCALHOST_ONLY=0 -e ROS_LOG_DIR=/tmp/ros_logs \
            -v "$DDS_XML:$DDS_XML:ro" \
            -v "$TOOLS:/boveda:ro" \
            "$HRI_IMAGE" python3 /boveda/boveda_alarma.py /boveda/alarma.wav "$ALARMA_REP" 2>&1)
        rc=$?
        if (( rc == 0 )); then
            registrar ALARMA_SONADA info "motivo=$motivo"
        else
            registrar ALARMA aviso "motivo=$motivo" "detalle=fallo al reproducir (rc=$rc): $(tail -c 300 <<<"$out")"
        fi
    ) </dev/null >/dev/null 2>&1 &
    disown 2>/dev/null || true
    return 0
}

# ----------------------------------------------------------------------------- auditar
# Logins SSH desde el journal (persistente; agi lo lee por el grupo adm) o, si no hay
# journalctl, desde auth.log*. La MAC solo aparece si la IP sigue en `ip neigh` AHORA
# (el vigilante sí la anota en el momento del login).
lineas_ssh() {  # lineas_ssh desde(ej. 24h, 7d, 2026-10-20)
    local desde=$1 since
    if [[ $desde =~ ^[0-9]+[mhd]$ ]]; then
        since="-${desde/m/min}"
    else
        since=$desde
    fi
    # journalctl solo si de verdad devuelve algo (sin permisos sale con 0 pero vacío).
    if [[ -z ${BOVEDA_AUTHLOG:-} ]] && command -v journalctl >/dev/null 2>&1 \
        && [[ -n $(journalctl -q --no-pager -n 1 -t sshd 2>/dev/null) ]]; then
        journalctl -q --no-pager -o short-iso -t sshd --since "$since" 2>/dev/null
        return
    fi
    # Respaldo: auth.log (+ rotados). Filtro de fecha aproximado con date -d por línea.
    local limite f
    if [[ $desde =~ ^([0-9]+)([mhd])$ ]]; then
        local n=${BASH_REMATCH[1]} u=${BASH_REMATCH[2]}
        case $u in m) u=minutes ;; h) u=hours ;; d) u=days ;; esac
        limite=$(date -d "-$n $u" +%s)
    else
        limite=$(date -d "$desde" +%s 2>/dev/null || echo 0)
    fi
    # shellcheck disable=SC2045  # rutas fijas del sistema; se quiere orden por fecha
    for f in $(ls -1tr "$AUTHLOG"* 2>/dev/null); do
        case $f in *.gz) zcat -f "$f" ;; *) cat "$f" ;; esac
    done 2>/dev/null | grep -a 'sshd\[' | while IFS= read -r l; do
        local fecha t
        if [[ $l =~ ^([0-9]{4}-[0-9-]+T[^ ]+) ]]; then fecha=${BASH_REMATCH[1]}; else fecha=${l:0:15}; fi
        t=$(date -d "$fecha" +%s 2>/dev/null || echo 0)
        (( t >= limite )) && echo "$l"
    done
}

fecha_de_linea() {  # ISO (short-iso / rsyslog RFC3339) o syslog clásico "Oct 23 10:00:00"
    local l=$1
    if [[ $l =~ ^([0-9]{4}-[0-9-]+T[^ ]+) ]]; then echo "${BASH_REMATCH[1]}"; else echo "${l:0:15}"; fi
}

# Clasifica una línea de sshd. Salida: "TIPO|fecha|pid|metodo|usuario|ip|puerto|huella"
clasificar_linea_ssh() {
    local l=$1 f re
    f=$(fecha_de_linea "$l")
    re='sshd\[([0-9]+)\]: Accepted ([^ ]+) for ([^ ]+) from ([^ ]+) port ([0-9]+)( ssh2(: [^ ]+ ([^ ]+))?)?'
    if [[ $l =~ $re ]]; then
        echo "ACEPTADO|$f|${BASH_REMATCH[1]}|${BASH_REMATCH[2]}|${BASH_REMATCH[3]}|${BASH_REMATCH[4]}|${BASH_REMATCH[5]}|${BASH_REMATCH[8]:--}"
        return
    fi
    re='sshd\[([0-9]+)\]: Failed ([^ ]+) for (invalid user )?([^ ]*) from ([^ ]+) port ([0-9]+)'
    if [[ $l =~ $re ]]; then
        local u=${BASH_REMATCH[4]}
        [[ -n ${BASH_REMATCH[3]} ]] && u="(inválido)$u"
        echo "FALLIDO|$f|${BASH_REMATCH[1]}|${BASH_REMATCH[2]}|$u|${BASH_REMATCH[5]}|${BASH_REMATCH[6]}|-"
        return
    fi
    re='sshd\[([0-9]+)\]: Invalid user ([^ ]*) from ([^ ]+) port ([0-9]+)'
    if [[ $l =~ $re ]]; then
        echo "USUARIO_INVALIDO|$f|${BASH_REMATCH[1]}|-|${BASH_REMATCH[2]}|${BASH_REMATCH[3]}|${BASH_REMATCH[4]}|-"
        return
    fi
    re='sshd\[([0-9]+)\]: Disconnected from user ([^ ]+) ([^ ]+) port ([0-9]+)'
    if [[ $l =~ $re ]]; then
        echo "DESCONEXION|$f|${BASH_REMATCH[1]}|-|${BASH_REMATCH[2]}|${BASH_REMATCH[3]}|${BASH_REMATCH[4]}|-"
        return
    fi
    return 1
}

auditar() {
    local desde=24h
    [[ ${1:-} == --desde ]] && desde=${2:?falta valor de --desde}
    cargar_conf
    declare -A macs=()
    local l c tipo f pid metodo usuario ip puerto huella mac conf n=0 desc=0
    printf '%-25s %-9s %-11s %-14s %-15s %-6s %-22s %-17s %s\n' \
        FECHA RESULTADO CONFIANZA USUARIO IP PUERTO METODO MAC HUELLA
    while IFS= read -r l; do
        c=$(clasificar_linea_ssh "$l") || continue
        IFS='|' read -r tipo f pid metodo usuario ip puerto huella <<<"$c"
        [[ $tipo == ACEPTADO || $tipo == FALLIDO ]] || continue
        [[ -n ${macs[$ip]:-} ]] || macs[$ip]=$(mac_de "$ip")
        mac=${macs[$ip]}
        if es_confiable "$ip" "$huella" "$mac"; then conf=CONFIABLE; else conf=DESCONOCIDO; desc=$((desc + 1)); fi
        printf '%-25s %-9s %-11s %-14s %-15s %-6s %-22s %-17s %s\n' \
            "$f" "$tipo" "$conf" "$usuario" "$ip" "$puerto" "$metodo" "$mac" "$huella"
        n=$((n + 1))
    done < <(lineas_ssh "$desde")
    echo "--- $n intentos de login en las últimas $desde · $desc DESCONOCIDOS"
    (( desc == 0 ))
}

# ----------------------------------------------------------------------------- vigilante
declare -A VISTO=()          # pid:inicio -> 1 (cada proceso se registra una vez)
declare -A CONT_VISTO=()     # id contenedor -> 1
CONT_OK_RE='^$'              # ERE de IDs de contenedores confiables con la bóveda montada
ESTADO_MONTAJE=-

procesar_linea_auth() {
    local l=$1 c tipo f pid metodo usuario ip puerto huella mac
    c=$(clasificar_linea_ssh "$l") || {
        # Fallos de sudo: solo se registran (pueden ser cuentas del fabricante).
        if [[ $l == *"sudo:"*"authentication failure"* ]]; then
            local u=- ru=-
            [[ $l =~ \ user=([^ ]+) ]] && u=${BASH_REMATCH[1]}
            [[ $l =~ ruser=([^ ]*) ]] && ru=${BASH_REMATCH[1]}
            registrar SUDO_FALLO aviso "usuario=${ru:--}" "objetivo=$u" "detalle=$(fecha_de_linea "$l")"
        fi
        return 0
    }
    IFS='|' read -r tipo f pid metodo usuario ip puerto huella <<<"$c"
    mac=$(mac_de "$ip")
    case $tipo in
    ACEPTADO)
        LOGIN[$pid]="$metodo $usuario $ip $puerto $huella"
        if es_confiable "$ip" "$huella" "$mac"; then
            registrar LOGIN info "confianza=CONFIABLE" "usuario=$usuario" "ip=$ip" "puerto=$puerto" \
                "mac=$mac" "metodo=$metodo" "huella=$huella" "sshd=$pid"
        else
            registrar LOGIN_DESCONOCIDO ALERTA "confianza=DESCONOCIDO" "usuario=$usuario" "ip=$ip" \
                "puerto=$puerto" "mac=$mac" "metodo=$metodo" "huella=$huella" "sshd=$pid" \
                "boveda=$(montada && echo abierta || echo cerrada)"
            alarma "login desconocido $usuario@$ip"
        fi ;;
    FALLIDO)
        if es_confiable "$ip" - "$mac"; then
            registrar LOGIN_FALLIDO aviso "confianza=CONFIABLE" "usuario=$usuario" "ip=$ip" "puerto=$puerto" \
                "mac=$mac" "metodo=$metodo" "sshd=$pid"
        else
            registrar LOGIN_FALLIDO ALERTA "confianza=DESCONOCIDO" "usuario=$usuario" "ip=$ip" \
                "puerto=$puerto" "mac=$mac" "metodo=$metodo" "sshd=$pid"
            [[ $ALARMA_FALLO_LOGIN == 1 ]] && alarma "login fallido $usuario@$ip"
        fi ;;
    USUARIO_INVALIDO)
        registrar USUARIO_INVALIDO aviso "usuario=$usuario" "ip=$ip" "puerto=$puerto" "mac=$mac" "sshd=$pid" ;;
    DESCONEXION)
        registrar LOGOUT info "usuario=$usuario" "ip=$ip" "puerto=$puerto" "sshd=$pid" ;;
    esac
}

precargar_logins() {  # sesiones de los últimos días, para resolver procesos ya abiertos
    local l r
    while IFS= read -r l; do
        r=$(parse_accepted "$l") || continue
        LOGIN[${r%% *}]=${r#* }
    done < <(lineas_ssh 7d | grep -a ': Accepted ')
}

revisar_contenedores() {  # contenedores que montan algo de la bóveda
    CONT_OK_RE='^$'
    docker_ok || return 0
    local id img nombre src ids_ok=()
    while read -r id img nombre; do
        [[ -n $id ]] || continue
        [[ ${img##*/} == *:* ]] || img="$img:latest"
        src=$(docker inspect -f '{{range .Mounts}}{{.Source}} {{end}}' "$id" 2>/dev/null)
        [[ " $src" == *" $MNT "* || " $src" == *" $MNT/"* ]] || continue
        if en_lista "$img" "${CONF_IMAGEN[@]}"; then
            ids_ok+=("$id")
            if [[ -z ${CONT_VISTO[$id]:-} ]]; then
                CONT_VISTO[$id]=1
                registrar CONTENEDOR info "nombre=$nombre" "imagen=$img" "id=${id:0:12}" "detalle=contenedor propio monta la bóveda"
            fi
        elif [[ -z ${CONT_VISTO[$id]:-} ]]; then
            CONT_VISTO[$id]=1
            registrar CONTENEDOR_NO_CONFIABLE ALERTA "nombre=$nombre" "imagen=$img" "id=${id:0:12}" "montajes=$src"
            alarma "contenedor no confiable $nombre ($img)"
        fi
    done < <(docker ps --no-trunc --format '{{.ID}} {{.Image}} {{.Names}}' 2>/dev/null)
    (( ${#ids_ok[@]} )) && CONT_OK_RE=$(IFS='|'; echo "${ids_ok[*]}")
    return 0
}

escanear() {
    local p clave inicio uid usuario comm cmd tty_p cgroup rx conf mac motivo
    revisar_contenedores
    for p in $(pids_en_boveda); do
        [[ $p == "$$" || $p == "$BASHPID" ]] && continue
        inicio=$(awk '{print $22}' "/proc/$p/stat" 2>/dev/null) || continue
        [[ -n $inicio ]] || continue
        clave="$p:$inicio"
        [[ -n ${VISTO[$clave]:-} ]] && continue
        VISTO[$clave]=1
        comm=$(cat "/proc/$p/comm" 2>/dev/null)
        [[ $comm == gocryptfs ]] && continue
        uid=$(awk '/^Uid:/{print $2}' "/proc/$p/status" 2>/dev/null)
        usuario=$(getent passwd "$uid" | cut -d: -f1); usuario=${usuario:-$uid}
        cmd=$( { tr '\0' ' ' <"/proc/$p/cmdline"; } 2>/dev/null | head -c 200)
        tty_p=$(ps -o tty= -p "$p" 2>/dev/null | tr -d ' '); tty_p=${tty_p:--}
        cgroup=$(cat "/proc/$p/cgroup" 2>/dev/null)
        # Procesos dentro de un contenedor (rutas en el host solo si el montaje coincide).
        if [[ $cgroup =~ (docker[-/]|/docker/)([0-9a-f]{64}) ]]; then
            if [[ ${BASH_REMATCH[2]} =~ $CONT_OK_RE ]]; then
                continue
            fi
            registrar ACCESO_CONTENEDOR ALERTA "usuario=$usuario" "pid=$p" "cmd=$cmd" "id=${BASH_REMATCH[2]:0:12}"
            alarma "proceso de contenedor no confiable"
            continue
        fi
        sesion_de "$p"
        mac=$S_MAC
        if [[ $S_IP != - ]]; then
            if es_confiable "$S_IP" "$S_HUELLA" "$mac"; then
                registrar ACCESO info "confianza=CONFIABLE" "usuario=$usuario" "ip=$S_IP" "puerto=$S_PUERTO" \
                    "mac=$mac" "metodo=$S_METODO" "huella=$S_HUELLA" "tty=$tty_p" "pid=$p" "cmd=$cmd" "sshd=$S_SSHD"
                continue
            fi
            motivo="acceso desde sesión no confiable $S_IP"
            registrar ACCESO_NO_CONFIABLE ALERTA "confianza=DESCONOCIDO" "usuario=$usuario" "ip=$S_IP" \
                "puerto=$S_PUERTO" "mac=$mac" "metodo=$S_METODO" "huella=$S_HUELLA" "tty=$tty_p" \
                "pid=$p" "cmd=$cmd" "sshd=$S_SSHD" "login=$S_LOGIN_USER"
            alarma "$motivo"
            continue
        fi
        # Sin sesión SSH: consola local, servicios, cron... Solo se aceptan los de confianza.conf.
        for rx in "${CONF_PROCESO[@]}"; do
            if [[ $comm =~ $rx ]]; then
                registrar ACCESO info "confianza=PROCESO_CONFIABLE" "usuario=$usuario" "tty=$tty_p" "pid=$p" "cmd=$cmd"
                continue 2
            fi
        done
        registrar ACCESO_SIN_SESION ALERTA "confianza=DESCONOCIDO" "usuario=$usuario" "tty=$tty_p" \
            "pid=$p" "cmd=$cmd" "detalle=proceso sin sesión SSH identificable"
        alarma "acceso sin sesión SSH ($usuario: $comm)"
    done
}

vigilar_bucle() {
    [[ $EUID -eq 0 ]] || die "el bucle del vigilante debe correr como root (usa: boveda.sh vigilar)"
    cd / || exit 1
    cargar_conf
    echo $$ >"$PIDFILE"; chown --reference="$TOOLS" "$PIDFILE" 2>/dev/null
    local fd tailpid fuente linea ahora ult_escaneo=0 ult_latido ult_conf=0
    ult_latido=$(date +%s)
    if [[ -r $AUTHLOG ]]; then
        fuente=$AUTHLOG
        exec {fd}< <(exec tail -n 0 -F "$AUTHLOG" 2>/dev/null)
    else
        fuente=journal
        exec {fd}< <(exec journalctl -q -f -n 0 -o short-iso -t sshd -t sudo 2>/dev/null)
    fi
    tailpid=$!
    trap 'registrar VIGILANCIA_FIN aviso "pid=$$" "detalle=señal recibida"; kill $tailpid 2>/dev/null; rm -f "$PIDFILE"; exit 0' TERM INT HUP
    registrar VIGILANCIA_INICIO info "pid=$$" "fuente=$fuente" "intervalo_s=$INTERVALO" \
        "confiables=ip:${CONF_IP[*]:-} huella:${CONF_HUELLA[*]:-} mac:${CONF_MAC[*]:-}"
    precargar_logins
    while :; do
        if IFS= read -r -t "$INTERVALO" -u "$fd" linea; then
            procesar_linea_auth "$linea"
        elif (( $? <= 128 )); then
            # EOF: tail/journalctl murió; se relanza.
            registrar VIGILANCIA aviso "detalle=lector de $fuente terminado; se relanza"
            exec {fd}<&-
            if [[ $fuente == journal ]]; then
                exec {fd}< <(exec journalctl -q -f -n 0 -o short-iso -t sshd -t sudo 2>/dev/null)
            else
                exec {fd}< <(exec tail -n 0 -F "$AUTHLOG" 2>/dev/null)
            fi
            tailpid=$!
            sleep 1
        fi
        ahora=$(date +%s)
        if (( ahora - ult_conf >= 60 )); then cargar_conf; ult_conf=$ahora; fi
        if (( ahora - ult_escaneo >= INTERVALO )); then
            ult_escaneo=$ahora
            if montada; then
                [[ $ESTADO_MONTAJE == abierta ]] || registrar BOVEDA_ABIERTA_DETECTADA info "punto=$MNT"
                ESTADO_MONTAJE=abierta
                escanear
            else
                [[ $ESTADO_MONTAJE == cerrada ]] || registrar BOVEDA_CERRADA_DETECTADA info "punto=$MNT"
                ESTADO_MONTAJE=cerrada
                VISTO=() CONT_VISTO=()
            fi
        fi
        if (( LATIDO_MIN > 0 && ahora - ult_latido >= LATIDO_MIN * 60 )); then
            ult_latido=$ahora
            registrar LATIDO info "pid=$$" "boveda=$ESTADO_MONTAJE"
        fi
    done
}

vigilante_pid() {  # pid si el vigilante está vivo
    local p
    [[ -f $PIDFILE ]] || return 1
    p=$(cat "$PIDFILE" 2>/dev/null)
    [[ $p =~ ^[0-9]+$ ]] && ps -p "$p" -o args= 2>/dev/null | grep -q "boveda.sh _vigilar" && { echo "$p"; return 0; }
    return 1
}

# ----------------------------------------------------------------------------- comandos
cmd_instalar() {
    local origen gocry=
    origen="$(dirname "$SELF")"
    while (( $# )); do
        case $1 in
        --gocryptfs) gocry=$2; shift 2 ;;
        *) die "opción desconocida: $1" ;;
        esac
    done
    [[ -n $gocry ]] || gocry="$origen/.tools/gocryptfs"
    [[ -f $gocry || -x $GOCRYPTFS ]] || die "no encuentro gocryptfs en $gocry (usa --gocryptfs RUTA)"
    for f in boveda_alarma.py boveda_generar_alarma.py; do
        [[ -f $origen/$f || -f $TOOLS/$f ]] || die "falta $origen/$f"
    done
    mkdir -p "$TOOLS" && chmod 700 "$TOOLS" || die "no puedo crear $TOOLS"
    [[ $SELF == "$TOOLS/boveda.sh" ]] || install -m 700 "$SELF" "$TOOLS/boveda.sh"
    for f in boveda_alarma.py boveda_generar_alarma.py; do
        [[ $origen/$f -ef $TOOLS/$f ]] || [[ ! -f $origen/$f ]] || install -m 600 "$origen/$f" "$TOOLS/$f"
    done
    if [[ -f $gocry && ! $gocry -ef $GOCRYPTFS ]]; then
        local sum
        sum=$(sha256sum "$gocry" | cut -d' ' -f1)
        [[ $sum == "$GOCRYPTFS_SHA256" ]] || die "sha256 de gocryptfs inesperado: $sum (esperado $GOCRYPTFS_SHA256)"
        install -m 700 "$gocry" "$GOCRYPTFS"
    fi
    [[ -f $CONF ]] || printf '%s' "$CONF_DEFECTO" >"$CONF"
    touch "$LOG" "$LOCK" "$ALARMA_ULT"
    chmod 600 "$LOG" "$LOCK" "$CONF" "$ALARMA_ULT"
    "$GOCRYPTFS" -version 2>/dev/null | head -n 1
    sesion_actual
    registrar INSTALADO info "${SES[@]}" "detalle=herramientas en $TOOLS"
    echo "Instalado en $TOOLS. Revisa $CONF (identidades de confianza)."
    echo "A partir de ahora usa: $TOOLS/boveda.sh <comando>"
}

cmd_crear() {
    requiere_instalada crear
    [[ -e /dev/fuse ]] || die "no hay /dev/fuse"
    [[ -e $CIPHER ]] && die "$CIPHER ya existe: la bóveda ya está creada (o borra a mano un intento fallido)"
    [[ -e $ORIGINAL ]] && die "$ORIGINAL ya existe (de una migración anterior)"
    [[ -d $MNT && ! -L $MNT ]] || die "$MNT no es un directorio"
    montada && die "$MNT ya es un punto de montaje"
    case "$PWD/" in "$MNT/"*) die "sal de $MNT antes (cd ~)";; esac
    local corriendo
    corriendo=$(contenedores_corriendo)
    [[ -z $corriendo ]] || die "hay contenedores corriendo: $corriendo — páralos antes (deploy/hri_sin_actuacion.sh parar)"
    sudo -v || die "hace falta sudo"
    local ocupados
    ocupados=$(sudo bash -c "$(declare -f pids_en_boveda); MNT='$MNT'; pids_en_boveda" | tr '\n' ' ')
    for p in $ocupados; do [[ $p == "$$" ]] || die "hay procesos usando $MNT: $ocupados (ps -fp ...); ciérralos antes"; done
    local tam libre
    tam=$(sudo du -sb "$MNT" | cut -f1)
    libre=$(df --output=avail -B1 "$OWNER_HOME" | tail -n 1 | tr -d ' ')
    (( libre > tam + tam / 10 + 500000000 )) || die "espacio insuficiente: ocupa $((tam / 1048576)) MiB, libres $((libre / 1048576)) MiB"
    cat <<EOF

Se va a CIFRAR $MNT ($((tam / 1048576)) MiB):
  1. gocryptfs -init $CIPHER          (tecleas la frase de paso dos veces)
  2. se monta en $MIGRAR_MNT (sudo) y se copia todo con cp -a (conserva dueños)
  3. se compara la copia: lista de archivos/tamaños/dueños + contenido (diff -rq)
  4. si coincide: $MNT -> $ORIGINAL (copia SIN cifrar, se borra al final si confirmas)
  5. $MNT queda vacío como punto de montaje y se abre la bóveda ahí
Necesitas ~$((tam / 1048576)) MiB extra temporalmente. No guardes la frase de paso en el robot.
EOF
    confirmar_si "¿Continuar?" || die "cancelado"

    info "1/5 gocryptfs -init (al terminar, gocryptfs imprime la CLAVE MAESTRA)"
    mkdir -m 700 "$CIPHER" || die "no puedo crear $CIPHER"   # -init exige un directorio vacío
    "$GOCRYPTFS" -init "$CIPHER" || { rmdir "$CIPHER" 2>/dev/null; die "gocryptfs -init falló"; }
    cat <<EOF

IMPORTANTE: copia la clave maestra que acaba de imprimir gocryptfs FUERA del robot
(papel o gestor de contraseñas del PC) y NUNCA la guardes en el robot. Con ella se
recuperan los datos si olvidas la frase de paso o se corrompe gocryptfs.conf.
Guarda también una copia de $CIPHER/gocryptfs.conf en el PC:
  scp $ROBOT_SSH:${CIPHER#"$OWNER_HOME"/}/gocryptfs.conf ./x2_boveda_gocryptfs.conf
EOF
    read -r -p "Pulsa Enter cuando hayas guardado la clave maestra... " _
    sesion_actual
    registrar BOVEDA_CREADA info "${SES[@]}" "cifrado=$CIPHER"

    info "2/5 montaje temporal en $MIGRAR_MNT (frase de paso otra vez)"
    mkdir -p "$MIGRAR_MNT" || die "no puedo crear $MIGRAR_MNT"
    sudo "$GOCRYPTFS" -allow_other -q "$CIPHER" "$MIGRAR_MNT" || die "no se pudo montar la bóveda nueva"
    info "copiando (puede tardar varios minutos)..."
    if ! sudo cp -a "$MNT/." "$MIGRAR_MNT/"; then
        sudo fusermount3 -u "$MIGRAR_MNT"
        die "la copia falló; los originales siguen intactos en $MNT"
    fi
    info "3/5 verificando la copia"
    local lista='\( -type d -printf "%P\td\t%U:%G\n" \) -o -printf "%P\t%y\t%s\t%U:%G\n"'
    local a b
    a=$(sudo bash -c "cd '$MNT' && find . $lista | LC_ALL=C sort" | sha256sum)
    b=$(sudo bash -c "cd '$MIGRAR_MNT' && find . $lista | LC_ALL=C sort" | sha256sum)
    local na nb
    na=$(sudo find "$MNT" | wc -l); nb=$(sudo find "$MIGRAR_MNT" | wc -l)
    echo "   entradas: original $na · cifrada $nb"
    if [[ $a != "$b" ]] || ! sudo diff -rq --no-dereference "$MNT" "$MIGRAR_MNT"; then
        sudo fusermount3 -u "$MIGRAR_MNT"
        die "la copia NO coincide; originales intactos en $MNT. Revisa y borra $CIPHER para reintentar."
    fi
    echo "   OK: misma lista de archivos, tamaños, dueños y contenido."
    info "4/5 $MNT -> $ORIGINAL"
    sudo fusermount3 -u "$MIGRAR_MNT" || die "no se pudo desmontar $MIGRAR_MNT"
    rmdir "$MIGRAR_MNT"
    mv "$MNT" "$ORIGINAL" || die "no se pudo renombrar $MNT"
    mkdir -m 700 "$MNT" || die "no se pudo crear el punto de montaje $MNT"
    sesion_actual
    registrar MIGRACION info "${SES[@]}" "entradas=$nb" "bytes=$tam" "original=$ORIGINAL"
    info "5/5 abriendo la bóveda en $MNT"
    cmd_abrir || echo "Ábrela luego con: $TOOLS/boveda.sh abrir"
    echo
    echo "La copia SIN cifrar sigue en $ORIGINAL."
    if confirmar_si "¿Borrarla ya? (comprueba antes que $MNT funciona)"; then
        cmd_borrar_original --ya
    else
        echo "Bórrala cuando quieras con: $TOOLS/boveda.sh borrar-original"
    fi
}

cmd_borrar_original() {
    [[ -d $ORIGINAL ]] || die "no existe $ORIGINAL"
    if [[ ${1:-} != --ya ]]; then
        confirmar_si "Se borrará $ORIGINAL (copia sin cifrar)." || die "cancelado"
    fi
    sudo rm -rf --one-file-system "$ORIGINAL" || die "no se pudo borrar $ORIGINAL"
    sesion_actual
    registrar ORIGINAL_BORRADO info "${SES[@]}" "ruta=$ORIGINAL"
    echo "Borrado. Aviso: en SSD/ext4 un borrado normal no garantiza que los bloques no se"
    echo "puedan recuperar forensemente; lo que ya estuvo en claro en el disco pudo quedar."
}

cmd_abrir() {
    requiere_instalada abrir
    cargar_conf
    [[ -f $CIPHER/gocryptfs.conf ]] || die "no hay bóveda en $CIPHER (boveda.sh crear)"
    montada && { echo "La bóveda ya está abierta en $MNT."; return 0; }
    [[ -d $MNT ]] || mkdir -m 700 "$MNT"
    [[ -z $(ls -A "$MNT" 2>/dev/null) ]] || die "$MNT no está vacío; no se monta encima"
    sesion_actual
    sudo -v || { registrar ABRIR_FALLO aviso "${SES[@]}" "detalle=sudo denegado"; die "hace falta sudo"; }
    local rc
    # La frase de paso la pide gocryptfs en la terminal: no pasa por este script ni se guarda.
    (cd / && sudo "$GOCRYPTFS" -allow_other -q "$CIPHER" "$MNT")
    rc=$?
    if (( rc == 0 )) && montada; then
        sesion_actual
        registrar ABRIR_OK info "${SES[@]}"
        echo "Bóveda ABIERTA en $MNT. Mientras esté abierta, cualquiera con sudo puede leerla."
        echo "Ciérrala al terminar: $TOOLS/boveda.sh cerrar"
        vigilante_pid >/dev/null || echo "Aviso: el vigilante no está corriendo ($TOOLS/boveda.sh vigilar)."
        return 0
    fi
    local det="gocryptfs rc=$rc"
    (( rc == 12 )) && det="frase de paso incorrecta (rc=12)"
    sesion_actual
    registrar ABRIR_FALLO ALERTA "${SES[@]}" "detalle=$det"
    [[ $ALARMA_ABRIR_FALLIDO == 1 ]] && alarma "abrir fallido"
    echo "ERROR: no se pudo abrir: $det" >&2
    return 1
}

parar_contenedores() {
    docker_ok || { echo "dockerd parado: no hay contenedores que parar."; return 0; }
    if [[ -f $MNT/deploy/compose.hri.yml ]]; then
        (cd "$MNT" && docker compose -f deploy/compose.hri.yml down) || true
        (cd "$MNT" && docker compose -f llm/docker-compose.yml -f deploy/compose.llm.onboard.yml down) || true
    fi
    # Cualquier otro contenedor que monte algo de la bóveda (run --rm, pruebas...).
    local id src
    for id in $(docker ps -q 2>/dev/null); do
        src=$(docker inspect -f '{{range .Mounts}}{{.Source}} {{end}}' "$id" 2>/dev/null)
        if [[ " $src" == *" $MNT "* || " $src" == *" $MNT/"* ]]; then
            echo "parando contenedor $(docker inspect -f '{{.Name}}' "$id") (monta la bóveda)"
            docker stop -t 10 "$id" >/dev/null || true
        fi
    done
}

cmd_cerrar() {
    requiere_instalada cerrar
    local forzar=${1:-}
    cd / || exit 1   # que este script no cuente como "proceso usando la bóveda"
    if ! montada; then echo "La bóveda ya está cerrada."; return 0; fi
    info "parando x2-hri / x2-llm"
    parar_contenedores
    sudo -v || die "hace falta sudo"
    local ocupados p lista=()
    ocupados=$(sudo bash -c "$(declare -f pids_en_boveda); MNT='$MNT'; pids_en_boveda")
    for p in $ocupados; do [[ $p == "$$" ]] || lista+=("$p"); done
    if (( ${#lista[@]} )); then
        echo "Procesos que usan la bóveda:"
        ps -o pid,user,tty,args -p "$(IFS=,; echo "${lista[*]}")" 2>/dev/null
        if [[ $forzar != --forzar ]]; then
            die "ciérralos (o sal de $MNT con cd ~) y repite, o usa: boveda.sh cerrar --forzar"
        fi
        sudo kill -TERM "${lista[@]}" 2>/dev/null; sleep 3
        sudo kill -KILL "${lista[@]}" 2>/dev/null
    fi
    sudo fusermount3 -u "$MNT" 2>/dev/null || sudo umount "$MNT" || true
    sleep 1
    if montada || pgrep -f "gocryptfs.*$CIPHER" >/dev/null; then
        sesion_actual
        registrar CERRAR_FALLO aviso "${SES[@]}"
        die "sigue montada (mira: mount | grep $MNT)"
    fi
    sesion_actual
    registrar CERRAR info "${SES[@]}"
    echo "Bóveda CERRADA: $MNT vacío, datos solo cifrados en $CIPHER."
}

cmd_estado() {
    local n=${1:-15}
    cargar_conf
    if montada; then echo "Bóveda: ABIERTA en $MNT"; else echo "Bóveda: cerrada"; fi
    local p
    if p=$(vigilante_pid); then echo "Vigilante: activo (pid $p)"; else echo "Vigilante: PARADO"; fi
    echo "Contenedores x2: $(contenedores_corriendo | tr '\n' ' ')"
    echo "Sesiones ahora:"
    local usr tty_ fecha hora resto ip
    while read -r usr tty_ fecha hora resto; do
        [[ -n $usr ]] || continue
        ip=$(grep -oE '\([^)]*\)' <<<"$resto" | tr -d '()' | head -n 1)
        if [[ -z $ip ]]; then
            echo "  $usr $tty_ $fecha $hora (local)"
        elif es_confiable "$ip" - "$(mac_de "$ip")"; then
            echo "  $usr $tty_ $fecha $hora $ip CONFIABLE"
        else
            echo "  $usr $tty_ $fecha $hora $ip DESCONOCIDO"
        fi
    done < <(who 2>/dev/null)
    echo "Últimas $n entradas de $LOG:"
    tail -n "$n" "$LOG" 2>/dev/null | sed -E 's/\th=[0-9a-f]{64}$//; s/\t/  /g; s/^/  /'
    verificar_log 2>/dev/null | tail -n 1 | sed 's/^/Registro: /'
}

cmd_vigilar() {
    requiere_instalada vigilar
    local p
    if p=$(vigilante_pid); then echo "El vigilante ya está corriendo (pid $p)."; return 0; fi
    sudo -v || die "hace falta sudo (el vigilante corre como root para ver /proc de todos)"
    # shellcheck disable=SC2024  # a propósito: vigilar.out lo abre el dueño, no root
    sudo -b -- env BOVEDA_OWNER_HOME="$OWNER_HOME" ${BOVEDA_AUTHLOG:+BOVEDA_AUTHLOG="$BOVEDA_AUTHLOG"} nohup "$TOOLS/boveda.sh" _vigilar \
        </dev/null >>"$TOOLS/vigilar.out" 2>&1
    for _ in $(seq 1 10); do p=$(vigilante_pid) && break; sleep 0.5; done
    [[ -n ${p:-} ]] || die "el vigilante no arrancó; mira $TOOLS/vigilar.out"
    echo "Vigilante activo (pid $p). Salida: $TOOLS/vigilar.out · Parar: $TOOLS/boveda.sh vigilar-parar"
    echo "No sobrevive a un reinicio (no se instala ningún servicio)."
}

cmd_vigilar_parar() {
    local p
    p=$(vigilante_pid) || { echo "El vigilante no está corriendo."; rm -f "$PIDFILE" 2>/dev/null; return 0; }
    sudo kill -TERM "$p" || die "no se pudo parar el pid $p"
    for _ in $(seq 1 10); do vigilante_pid >/dev/null || break; sleep 0.5; done
    echo "Vigilante parado."
}

cmd_generar_alarma() {
    requiere_instalada generar-alarma
    local texto=${1:-$TEXTO_ALARMA}
    montada || die "abre la bóveda: el modelo Supertonic está en $MNT/$SUPERTONIC_MODEL_REL"
    [[ -d $MNT/$SUPERTONIC_MODEL_REL ]] || die "no encuentro $MNT/$SUPERTONIC_MODEL_REL"
    docker_ok || die "dockerd no está corriendo (deploy/dockerd_x2.sh)"
    local gen="$TOOLS/gen"
    mkdir -p "$gen" && chmod 700 "$gen"
    docker run --rm --network none --user "$(id -u):$(id -g)" -e HOME=/tmp \
        -v "$MNT/$SUPERTONIC_MODEL_REL:/model:ro" \
        -v "$TOOLS/boveda_generar_alarma.py:/boveda_generar_alarma.py:ro" \
        -v "$gen:/out" \
        "$HRI_IMAGE" python3 /boveda_generar_alarma.py /model /out/alarma.wav "$texto" \
        || die "falló la síntesis"
    mv -f "$gen/alarma.wav" "$WAV" && chmod 600 "$WAV" && rmdir "$gen" 2>/dev/null
    sesion_actual
    registrar ALARMA_GENERADA info "${SES[@]}" "detalle=$texto"
    echo "Listo: $WAV. Pruébala con: $TOOLS/boveda.sh probar-alarma"
}

cmd_probar_alarma() {
    requiere_instalada probar-alarma
    cargar_conf
    docker_ok || die "dockerd no está corriendo (deploy/dockerd_x2.sh)"
    [[ -f $WAV ]] || die "falta $WAV (boveda.sh generar-alarma)"
    sesion_actual
    registrar PRUEBA_ALARMA info "${SES[@]}"
    docker run --rm --network host --ipc host \
        -e RMW_IMPLEMENTATION=rmw_fastrtps_cpp -e FASTRTPS_DEFAULT_PROFILES_FILE="$DDS_XML" \
        -e ROS_LOCALHOST_ONLY=0 -e ROS_LOG_DIR=/tmp/ros_logs \
        -v "$DDS_XML:$DDS_XML:ro" -v "$TOOLS:/boveda:ro" \
        "$HRI_IMAGE" python3 /boveda/boveda_alarma.py /boveda/alarma.wav "$ALARMA_REP"
}

cmd_copiar_log() {
    local rel=${LOG#"$OWNER_HOME"/}
    echo "Desde el PC (Git Bash), guarda una copia fechada del registro:"
    echo "  scp $ROBOT_SSH:$rel ./boveda_accesos_\$(date +%Y%m%d_%H%M).log"
    echo "Cada copia nueva debe empezar EXACTAMENTE por el contenido de la anterior; si no,"
    echo "alguien ha editado o recortado el registro en el robot. Último hash ahora:"
    tail -n 1 "$LOG" 2>/dev/null | awk -F'\t' '{print "  " $NF}'
}

cmd="${1:-}"; shift || true
case "$cmd" in
instalar)        cmd_instalar "$@" ;;
crear)           cmd_crear ;;
abrir)           cmd_abrir || exit 1 ;;
cerrar)          cmd_cerrar "$@" ;;
estado)          cmd_estado "$@" ;;
auditar)         auditar "$@" ;;
vigilar)         cmd_vigilar ;;
vigilar-parar)   cmd_vigilar_parar ;;
_vigilar)        vigilar_bucle ;;
verificar-log)   verificar_log ;;
copiar-log)      cmd_copiar_log ;;
generar-alarma)  cmd_generar_alarma "$@" ;;
probar-alarma)   cmd_probar_alarma ;;
borrar-original) cmd_borrar_original ;;
*)               sed -n '2,32p' "$0"; exit 2 ;;
esac
