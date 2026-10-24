#!/usr/bin/env bash
# El club del reto 2 con las gafas Meta Quest 3: quien las lleva es el huésped (docs/16-reto-2-vr.md).
# Con --hri, la persona hace el check-in en la webcam del PC antes de ponerse las gafas.
# Abre en el navegador de las Quest la dirección que imprime (https://IP-del-PC:8443/) y acepta el certificado.
# Por cable USB: adb reverse tcp:8443 tcp:8443 y https://localhost:8443/
set -eo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec "$REPO/scripts/run_club.sh" --vr "$@"
