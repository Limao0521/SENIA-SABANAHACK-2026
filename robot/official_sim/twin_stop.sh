#!/usr/bin/env bash
# Para el gemelo y el MC dentro del contenedor x2-twin. Va en un archivo aparte: con "bash -c 'pkill -f patrón'"
# el patrón aparece en la línea de órdenes de la propia shell y pkill la mata a ella también.
pkill -INT -f em_run.sh
pkill -INT -f aima-mc-app
pkill -INT -f "x2_ultra.twin.run"
sleep 2
pkill -9 -f "aima-mc-app|aimrt_main|iox-roudi|mc_main|x2_ultra.twin" || true
exit 0
