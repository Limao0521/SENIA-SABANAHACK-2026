#!/usr/bin/env bash
# =============================================================================
# x2_deploy.sh — deploy del HRI con actuación al X2 Ultra DESDE EL PC (Git Bash / Linux).
#
# El robot no tiene internet: todo va por el cable (ssh). Nada de esto mueve el robot;
# las etapas armadas se lanzan después, en el robot, con deploy/hri_club_con_actuacion.sh.
#
#   deploy/x2_deploy.sh conectar        # ping + ssh + arquitectura/L4T del robot
#   deploy/x2_deploy.sh codigo          # commit actual (git archive) → ~/x2_ultra + DEPLOYED
#   deploy/x2_deploy.sh modelos [--forzar]   # ASR, LLM, Supertonic, UniFace, CSV del MC (si faltan)
#   deploy/x2_deploy.sh imagenes        # imágenes externas arm64 que falten (pull en el PC → docker load)
#   deploy/x2_deploy.sh listo           # deploy/x2_listo.sh en el robot (solo lectura)
#   deploy/x2_deploy.sh todo            # conectar → codigo → modelos → imagenes → listo
#   deploy/x2_deploy.sh ssh             # shell en ~/x2_ultra del robot
#
# Variables: X2_HOST (agi@10.0.1.41), X2_DIR (~/x2_ultra en el robot).
# Recomendado: clave ssh copiada al robot (ssh-copy-id) para no teclear la contraseña cada vez.
# =============================================================================
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"
: "${X2_HOST:=agi@10.0.1.41}"
: "${X2_DIR:=x2_ultra}"                       # relativo al $HOME del robot
IP="${X2_HOST#*@}"
SSH=(ssh -o ConnectTimeout=8 -o ServerAliveInterval=15)
[[ -f ~/.ssh/x2_ultra_ed25519 ]] && SSH+=(-i ~/.ssh/x2_ultra_ed25519)   # clave del robot, si existe
SSH+=("$X2_HOST")

# Lo que no está en git y hace falta a bordo (ruta relativa al repo).
MODELOS=(
    models/faster-whisper-large-v3-turbo
    llm/models/Qwen3-4B-Q4_K_M.gguf
    experiments/hriego_v2_tts_eval_20261021/model
    Face/thirdparty/uniface-models
    assets/hri/mc_classic
)
# Imágenes de terceros (arm64) que el robot no puede bajar sin internet.
IMAGENES_EXT=(
    dustynv/faster-whisper:r36.4.0-cu128-24.04
)

rsh() { "${SSH[@]}" "cd ~/$X2_DIR 2>/dev/null || cd ~; $*"; }

conectar() {
    echo "→ ping $IP"
    if ping -c 1 -W 2 "$IP" >/dev/null 2>&1 || ping -n 1 -w 2000 "$IP" >/dev/null 2>&1; then
        echo "  responde"
    else
        echo "ABORTO: $IP no responde. ¿Cable conectado y el PC con IP fija 10.0.1.x?" >&2
        echo "  Linux:   nmcli con up \"X2 robot\"" >&2
        echo "  Windows (PowerShell admin): New-NetIPAddress -InterfaceAlias Ethernet -IPAddress 10.0.1.100 -PrefixLength 24" >&2
        exit 1
    fi
    echo "→ ssh $X2_HOST"
    "${SSH[@]}" 'echo "  $(whoami)@$(hostname) $(uname -m) · $(head -1 /etc/nv_tegra_release 2>/dev/null | cut -c1-60)";
                 echo "  disco libre en ~: $(df -h ~ | awk "NR==2{print \$4}") · RAM disponible: $(free -m | awk "/^Mem:/{print \$7}") MB";
                 docker info >/dev/null 2>&1 && echo "  dockerd: corriendo" || echo "  dockerd: parado (deploy/dockerd_x2.sh en el robot)"'
}

codigo() {
    local branch commit
    branch="$(git rev-parse --abbrev-ref HEAD)"
    commit="$(git rev-parse --short HEAD)"
    if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
        echo "AVISO: hay cambios sin commitear; se despliega el último commit ($commit), no el árbol de trabajo." >&2
    fi
    echo "→ código $branch@$commit → $X2_HOST:~/$X2_DIR"
    git archive --format=tar HEAD | "${SSH[@]}" "mkdir -p ~/$X2_DIR && tar -x -C ~/$X2_DIR -f - &&
        printf 'rama=%s commit=%s fecha=%s\n' '$branch' '$commit' \"\$(date -Iseconds)\" > ~/$X2_DIR/DEPLOYED &&
        cat ~/$X2_DIR/DEPLOYED"
}

modelos() {
    local forzar=0 p
    [[ ${1:-} == --forzar ]] && forzar=1
    for p in "${MODELOS[@]}"; do
        if [[ ! -e $p ]]; then
            echo "  ·  $p: no está en este PC, lo salto"
            [[ $p == assets/hri/mc_classic ]] && echo "     (CSV del MC: scripts/fetch_hri_assets.sh; o el robot usa su mc_param bajo /agibot)"
            continue
        fi
        if (( ! forzar )) && rsh "test -e '$p'"; then
            echo "  ok $p (ya en el robot)"
            continue
        fi
        echo "  →  $p ($(du -sh "$p" | cut -f1))"
        tar -c -f - "$p" | "${SSH[@]}" "mkdir -p ~/$X2_DIR && tar -x -C ~/$X2_DIR -f -"
    done
}

imagenes() {
    local img
    command -v docker >/dev/null || { echo "ABORTO: este PC no tiene docker" >&2; exit 1; }
    for img in "${IMAGENES_EXT[@]}"; do
        if rsh "docker image inspect '$img' >/dev/null 2>&1"; then
            echo "  ok $img (ya en el robot)"; continue
        fi
        echo "  →  $img: pull arm64 en el PC y docker load en el robot (varios GB, tarda)"
        docker pull --platform linux/arm64 "$img"
        docker save "$img" | "${SSH[@]}" "docker load"
    done
    echo "→ imágenes propias (se construyen EN el robot; no se pueden traer de x86):"
    rsh 'for i in x2-onboard:latest x2-hri:latest x2-ultra/llm:jetson x2-ultra/llm:cpu; do
            docker image inspect $i >/dev/null 2>&1 && echo "  ok $i" || echo "  falta $i"; done
         echo "  (x2-onboard: deploy/build_aarch64.sh · x2-hri: deploy/Dockerfile.hri[.offline] ·"
         echo "   LLM: basta una de jetson/cpu; los scripts eligen jetson si existe)"'
}

listo() {
    "${SSH[@]}" -t "cd ~/$X2_DIR && bash deploy/x2_listo.sh ${1:-}"
}

cmd="${1:-}"; shift || true
case "$cmd" in
conectar) conectar ;;
codigo) conectar >/dev/null; codigo ;;
modelos) modelos "$@" ;;
imagenes) imagenes ;;
listo) listo "$@" ;;
todo) conectar; codigo; modelos; imagenes; listo ;;
ssh) "${SSH[@]}" -t "cd ~/$X2_DIR && exec bash -l" ;;
*) sed -n "2,20p" "$0"; exit 2 ;;
esac
