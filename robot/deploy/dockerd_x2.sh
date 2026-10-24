#!/usr/bin/env bash
# =============================================================================
# dockerd_x2.sh — arranca un dockerd TEMPORAL en el PC2 del X2 Ultra.
#
# El X2 trae docker.service/docker.socket ENMASCARADOS de fábrica. En vez de
# desenmascararlos, se lanza dockerd a mano sin tocar la red del robot:
#   --iptables=false --ip-forward=false --ip-masq=false --bridge=none
# (sin docker0 ni reglas de firewall; los contenedores usan network_mode: host).
# No sobrevive a un reinicio: tras reiniciar el robot, vuelve a ejecutarlo.
#
#   deploy/dockerd_x2.sh          # pide la contraseña de sudo
# =============================================================================
set -euo pipefail

if docker info >/dev/null 2>&1; then
    echo "dockerd ya está corriendo."
    exit 0
fi

sudo sh -c 'nohup dockerd --iptables=false --ip-forward=false --ip-masq=false --bridge=none \
    --add-runtime nvidia=/usr/bin/nvidia-container-runtime \
    --containerd=/run/containerd/containerd.sock >/tmp/dockerd.log 2>&1 &'

for _ in $(seq 1 30); do
    docker info >/dev/null 2>&1 && break
    sleep 1
done
docker info --format 'dockerd {{.ServerVersion}} listo (log: /tmp/dockerd.log)'
