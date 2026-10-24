# deploy · Fase F1 — contenedor `x2-onboard` (aarch64)

Contenedor Docker para correr **en el PC2 del robot** (Jetson Orin NX 16 GB, L4T r36.4.x /
JetPack 6 / Ubuntu 22.04 + ROS 2 Humble) los programas de `src/x2_ultra/real/`:

- `check` (solo lectura) — healthcheck de solo lectura del robot.
- `andar` — marcha WASD/QE (locomoción oficial del MC). **MUEVE EL ROBOT**.
- `brazos` — brazos articulación a articulación (URS). **MUEVE EL ROBOT**.
- `movimientos` — gestos, LinkCraft y bailes de fábrica. **MUEVE EL ROBOT**.

El FastDDS del robot solo escucha en develop0 (10.0.1.41): el contenedor debe correr **en** el PC2
para que el descubrimiento sea local. El PC de desarrollo queda solo para el gemelo MuJoCo.

> Plan completo: [`docs/14-plan-despliegue-a-bordo.md`](../docs/14-plan-despliegue-a-bordo.md).
> Esta carpeta cubre **F1** (contenedor aarch64). F2 (puente player→robot) y fases siguientes quedan
> fuera.

## 0. Qué se lleva y qué NO

| Lleva | No lleva |
|---|---|
| `ros:humble-ros-base` arm64 (imagen base) | MuJoCo (el gemelo no va a bordo) |
| AimDK SDK aarch64 (`install-space` del zip oficial) | torch (no se necesita en F1) |
| `aimdk_msgs` compilado con extras del robot (McBodyPose, SetMcMotion…) | Face/LLM (F6) |
| `python3-tk`, `python3-numpy`, `python3-yaml`, `python3-pil` | `x2-ultra-sim:humble` (x86, solo del PC de desarrollo) |
| source de `x2_ultra` (montado desde el repo) | `sim_mujoco` / `mc` (x86) |
| assets de HRI precomputados (`assets/hri/`) | modelos Onnx de políticas (no para real) |

Target opcional `bench`: añade `mujoco`, `onnxruntime` y `psutil` para correr `x2-bench` en el Orin
y medir el factor real vs la banda ×1,8-2,2 estimada.

## 1. Copiar el repo al Orin

Desde el PC de desarrollo (con el cable del robot apagado o sin él, no importa):

```bash
# rsync es lo más rápido si ya lo tenías copiado antes; ignora downloads pesados
rsync -av --delete \
    --exclude 'logs/' \
    --exclude '.venv*/' \
    --exclude 'x2_downloads/' \
    --exclude '~/robots/x2_simulation' \
    ./  agi@10.0.1.41:~/x2_ultra/
# o scp si prefieres:
# scp -r ./  agi@10.0.1.41:~/x2_ultra/
```

`agi` es el usuario típico del PC2 del X2; ajusta si el tuyo se llama distinto. Una vez en el Orin,
el `X2_SIM_ROOT` por defecto es `~/robots/x2_simulation` (como en el PC de desarrollo).

## 2. Preparar el SDK AimDK aarch64 (una vez)

```bash
ssh agi@10.0.1.41
cd ~/x2_ultra
# Descarga solo el SDK aarch64 (~600 MB comprimido). parallel_download.py abre 24 conexiones
# porque el espejo de AgiBot limita cada una a ~8 KB/s; tarda pero funciona.
X2_SIM_ROOT=~/robots/x2_simulation \
    python3 official_sim/parallel_download.py \
        https://x2-aimdk.agibot.com/downloads/aimdk-sdk-v1_1_0-aimdk-aarch64-faf4c945-artifacts.zip \
        ~/robots/x2_downloads/aimdk-sdk-v1_1_0-aimdk-aarch64-faf4c945-artifacts.zip 24
# Descomprimir:
python3 -c "import zipfile,sys; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])" \
    ~/robots/x2_downloads/aimdk-sdk-v1_1_0-aimdk-aarch64-faf4c945-artifacts.zip \
    ~/robots/x2_simulation
mv ~/robots/x2_simulation/aimdk-aarch64-faf4c945-artifacts \
   ~/robots/x2_simulation/aimdk
# Verifica:
ls ~/robots/x2_simulation/aimdk/install/local_setup.bash
```

> El script `deploy/build_aarch64.sh` hace estos pasos automáticamente si falta el SDK.

> ⚠️ **Verificar en el Orin.** No hemos podido probar el zip aarch64 desde este PC (es x86). Si el
> zip no trae `install/` directo (otra estructura), mira el mensaje de error de
> `build_aarch64.sh` y ajusta: puede que haga falta compilar el AimDK dentro del Orin con
> `colcon build` como hace `official_sim/sim.sh sdk`. Deja el resultado en el mismo path.

## 3. Construir la imagen

```bash
# En el Orin, desde el repo:
cd ~/x2_ultra
deploy/build_aarch64.sh                    # runtime por defecto (ligero)
deploy/build_aarch64.sh --target bench     # opcional: añade mujoco + onnxruntime
```

La compilación es **nativa** en el Orin (8× Cortex-A78AE), no cruza desde x86. Tarda unos minutos
la primera vez (compila aimdk_msgs con los extras del robot: McBodyPose, SetMcMotion…).

El script usa BuildKit con `--build-context aimdk=$X2_SIM_ROOT/aimdk` para pasar el SDK al
Dockerfile sin copiarlo al contexto de build. El stage `overlay-builder` compila `aimdk_msgs`
desde fuente reproduciendo lo que hace `scripts/build_aimdk_overlay.sh`.

## 4. Arrancar el contenedor

```bash
docker compose -f deploy/compose.onboard.yml up -d
docker compose -f deploy/compose.onboard.yml ps       # debe salir "Up" y healthy
```

El contenedor (`x2-onboard`) se queda en `sleep infinity` con la red del host:

- `network_mode: host` → hereda la red del PC2 y ve al robot por develop0.
- Repo montado en `/workspace/x2_ultra:ro`; `logs/real/` sobremontado con escritura.
- `PYTHONPATH` apunta al repo montado → cambios sin rebuild.
- Healthcheck ligero: verifica imports (no toca el robot).

## 5. Probar

**Paso 1 · solo lectura (NO mueve el robot):**

```bash
deploy/onboard.sh check
```

Debe imprimir modo, fuente, batería, reloj del robot, posturas de brazo y terminar con
`"se puede armar"` si los dos programas podrían arrancar. Si `check` falla:

| Síntoma | Causa probable |
|---|---|
| `No such container: x2-onboard` | faltó `compose up -d` |
| `import aimdk_msgs` falla | el overlay no compiló → revisa el build log |
| `no llega el estado del MC` | el PC2 no ve al robot: `ip -4 addr` debe mostrar 10.0.1.x en develop0 |
| `RMW_IMPLEMENTATION` no coincide | debe ser `rmw_fastrtps_cpp` (compose ya lo pone) |
| `ROS_LOCALHOST_ONLY=1` | debe ser `0` para que el contenedor vea al robot |

**Paso 2 · mover el robot (con el mando a mano y espacio libre):**

```bash
deploy/onboard.sh andar        # MUEVE EL ROBOT — ventana Tk
deploy/onboard.sh brazos       # MUEVE EL ROBOT — ventana Tk
deploy/onboard.sh movimientos  # MUEVE EL ROBOT — ventana Tk
```

### Ventanas Tk (headless vs X11)

`andar`, `brazos` y `movimientos` abren ventana **Tk** (no PySide6). En el Orin hay dos escenarios:

1. **Monitor directo / sesión VNC / escritorio JetPack.** `DISPLAY=:0` está definido; la ventana se
   ve y el teclado funciona. Todo OK.
2. **Sesión ssh sin X11 forwarding.** No hay `DISPLAY`; la ventana no abre. `onboard.sh` avisa y
   sale. Opciones:
   - `ssh -X agi@10.0.1.41` (reenvía X11).
   - Instalar `x11vnc` en el Orin y conectarte con un visor.
   - Conectar un monitor al Orin.

`check` **no** abre ventana: funciona en ssh puro.

> ⚠️ **Known issue / limitación.** Si en F2 el bridge player→robot corre headless, hay que refactorizar
> `ui.py` para que la ventana Tk sea opcional (flag `--sin-ventana`). En F1 no se toca `ui.py`.

## 6. Parar / rehacer

```bash
docker compose -f deploy/compose.onboard.yml down     # para y borra el contenedor
docker compose -f deploy/compose.onboard.yml up -d    # vuelve a arrancar
deploy/build_aarch64.sh --no-cache                    # reconstruye desde cero
```

## 7. Tamaño esperado y RAM

Imagen runtime `x2-onboard:latest`:

- Install-space del AimDK aarch64: ~300–600 MB (depende de qué traiga el zip).
- aimdk_msgs compilado: ~50 MB.
- Source de x2_ultra: <5 MB.
- Total esperado: **~500–800 MB en disco**.

RAM en ejecución (config A del plan, docs/14 §1):

- onboard + bridge (F2): ~0,5 GB
- tts-ctc (F4): 0,65 GB
- brain HRIEGO precomputado: 3,67 GB
- **Total: ~4,8 GB = 30%** de los 16 GB unificados del Orin. Cabe holgado.

## 8. Troubleshooting

### Discovery DDS

- `docker exec x2-onboard bash` y dentro:
  ```bash
  source /opt/ros/humble/setup.bash
  ros2 topic list            # debe ver /aima/mc/common/state, /aima/hal/joint/arm/state, …
  ros2 topic hz /aima/mc/common/state   # 10 Hz si hay robot
  ```
- Si no ve topics:
  1. `ip -4 addr` en el Orin → debe haber una IP `10.0.1.x` en develop0.
  2. `echo $ROS_LOCALHOST_ONLY` → `0`.
  3. `echo $RMW_IMPLEMENTATION` → `rmw_fastrtps_cpp`.
  4. ¿Hay firewall? `sudo ufw status` → debe permitir 10.0.1.0/24.
  5. `ros2 doctor --report` dentro del contenedor.

### RAM / térmica

- `tegrastats --interval 2000` (en el Orin, fuera del contenedor) para ver RAM y temperatura.
- El throttle empieza a 99 °C. Si sube, baja el modo de potencia: `sudo nvpmodel -m 0` (MAXN) es
  el recomendado para pruebas; si hay térmica, baja a `sudo nvpmodel -m 2` (20 W).

### Permisos

- El contenedor corre con `cap_drop: ALL`, `no-new-privileges`, `read_only: true`, tmpfs en /tmp.
- Si necesitas tocar algo del sistema (no recomendado), quita `read_only` y `cap_drop` del compose.

### Build falla en overlay-builder

- El `colcon build` del stage overlay requiere `ros-humble-rosidl-default-generators`. Si falla,
  mira el log: puede ser que el `package.xml` del SDK no declare bien las deps. Añade las que
  falten al Dockerfile.
- Si el zip del SDK tiene otra estructura (sin `prebuilt_x86_64/share/aimdk_msgs`), hay que
  adaptar los `COPY --from=aimdk` del Dockerfile. Verifica con `ls $SDK_ROOT/src/aimdk_msgs/`.

### `docker compose` no acepta `--build-context`

- `docker compose` no pasa `--build-context` al builder. Por eso el bloque `build:` de
  compose.onboard.yml es **solo referencia** (no usar `docker compose build`: fallaría sin el
  contexto `aimdk`); la imagen se construye con `deploy/build_aarch64.sh` (`docker buildx`, que sí
  lo acepta) y le pone el tag `x2-onboard:latest`. Luego `compose up -d` solo levanta el contenedor
  desde esa imagen.
- Si prefieres construir desde compose, copia el SDK dentro del repo (p. ej. `deploy/_ctx/aimdk`)
  y añade un `build:` con `context: ..` al compose. No es lo recomendado (duplica ~1 GB).

## 9. Qué queda PENDIENTE (no es tarea de F1)

- **F2 · Puente player → robot** (`src/x2_ultra/real/onboard.py`): que la salida del player HRI
  alimente `/mc/upper_body_command` y `/aima/hc/locomotion` reutilizando `arms.py`/`walk.py`.
- **F3 · Re-timing** al techo real (0,35 rad/s) y gestos SONIC regenerados.
- **F4 · Audio** a bordo por `PlayTts` de AimDK.
- **F5 · Salida limpia** de URS (interlock SIGTERM).
- **F6 · Opcionales:** Face arm64, LLM con `compose.jetson.yml`.
- **F7 · Ensayo general** con cable desconectado.

Esta carpeta `deploy/` solo cierra F1. Lo demás vive en otros tickets del plan.

## 10. Decisiones de diseño (responde a lo pedido)

| Decisión | Valor | Por qué |
|---|---|---|
| Imagen base | `ros:humble-ros-base` (multi-arch, arm64 nativo) | Existe en Docker Hub con manifiesto arm64; más ligera que `ros:humble`; trae rclpy. |
| Alternativa si falla el pull | `arm64v8/ros:humble-ros-base` | Mismo contenido, nombre explícito de la arquitectura. |
| SDK AimDK aarch64 | Montado como build context (`--build-context aimdk=$X2_SIM_ROOT/aimdk`) | Evita descargar ~600 MB dentro del build; el zip ya viene con install-space precompilado para arm64. |
| Overlay aimdk_msgs | Compilado desde src en stage `overlay-builder` | Reproduce `scripts/build_aimdk_overlay.sh`; agrega McBodyPose, SetMcMotion, GetMcMotions, McMotion. |
| x2_ultra en runtime | Copiado al build en `/opt/x2_ultra_src/` y sobremontado por compose desde `/workspace/x2_ultra` | Cambios sin rebuild durante la hackathon. |
| MuJoCo / torch | FUERA del runtime por defecto | El gemelo no va a bordo. Solo en target `bench` (opcional). |
| PySide6 | FUERA | `real/*.py` usan **tkinter** (no PySide6). Se instala `python3-tk`. |
| Headless vs Tk | Tk necesario para andar/brazos/movimientos; check es headless | `ui.py` usa tkinter. Sin X11, esas tres no pueden correr. Documentado como known issue; el refactor a `--sin-ventana` se deja para F2. |
| Comportamiento por defecto | `sleep infinity` + healthcheck de imports | El contenedor se queda "a la espera"; el usuario lanza `onboard.sh check` cuando quiera. |
| Red | `network_mode: host` | El FastDDS del robot solo escucha en develop0; contenedor en el mismo host = descubrimiento local. |

## 11. Comandos copy-paste para el Orin

```bash
# 1) Copiar el repo (desde el PC de desarrollo)
rsync -av --delete --exclude='logs/' --exclude='.venv*/' \
    ./  agi@10.0.1.41:~/x2_ultra/

# 2) SSH al Orin
ssh agi@10.0.1.41
cd ~/x2_ultra

# 3) Build (baja el SDK si falta, luego construye la imagen)
deploy/build_aarch64.sh

# 4) Arrancar contenedor
docker compose -f deploy/compose.onboard.yml up -d
docker compose -f deploy/compose.onboard.yml ps      # hasta que salga "healthy"

# 5) Primera prueba (SOLO LECTURA, no mueve el robot)
deploy/onboard.sh check

# 6) Si check sale verde y quieres mover el robot (con monitor/VNC/ssh -X):
deploy/onboard.sh brazos     # MUEVE EL ROBOT
```

## 12. Verificación desde este PC (lo que se pudo / no se pudo comprobar)

**Hecho aquí (sin robot):**

- `bash -n` sobre `build_aarch64.sh` y `onboard.sh`: sintaxis OK.
- `yaml.safe_load` sobre `compose.onboard.yml`: YAML válido (verifica con el venv-bench si tiene
  pyyaml; si no, vale cualquier Python con pyyaml).
- Coherencia de paths del Dockerfile con los scripts reales:
  - `prebuilt_x86_64/share/aimdk_msgs/` ← `scripts/build_aimdk_overlay.sh` §14.
  - `install/local_setup.bash` ← `scripts/robot_real.sh` línea 42.
  - `.msg`/`.srv` de `scripts/aimdk_overlay/` ← overlay README.
  - `find … -name '*.msg' -exec cp {}` ← `build_aimdk_overlay.sh` línea 37.

**Pendiente (solo en el Orin):**

- Tira real del zip `aimdk-sdk-v1_1_0-aimdk-aarch64-faf4c945-artifacts.zip` y verifica que trae
  `install/local_setup.bash` y `src/aimdk_msgs/prebuilt_x86_64/share/aimdk_msgs/{msg,srv}/`.
- Pull de `ros:humble-ros-base` arm64 en el Orin (depende de la red del PC2).
- Build nativo en el Orin y verificación de `docker run --rm x2-onboard:latest` (debe imprimir
  `x2-onboard: imports OK`).
- `deploy/onboard.sh check` contra el robot real: T0 de la guía de pruebas físicas (docs/15).

## 13. HRIEGO v2 a bordo (Face → saludo/diálogo con voz)

Estado al 2026-10-23: **compilado y verificado sin arrancar**. Imágenes en el Orin:
`x2-onboard:latest` (base ROS + aimdk_msgs), `x2-hri:latest` (HRIEGO v2) y
`x2-ultra/llm:latest` (= `x2-ultra/llm:cpu`, llama.cpp en **CPU**; la de Jetson/CUDA
con `compose.jetson.yml` queda pendiente de tener internet).

Adaptaciones al robot (todo opcional por variable de entorno; en el PC no cambia nada):

| Pieza | En el PC | A bordo |
|---|---|---|
| Cámara | webcam V4L2 | topic ROS del HAL: `HRIEGO_CAMERA_SOURCE=ros:/aima/hal/sensor/rgbd_head_front/rgb_image` (`hriego_v2/robot_io.py`) |
| Audio | `pw-play`/`aplay` | AimDK: foco de audio + `/aima/hal/audio/playback` (`HRIEGO_AUDIO_BACKEND=aimdk`) |
| VRAM | `nvidia-smi` | memoria unificada = MemAvailable (`HRIEGO_JETSON=1`, umbrales en `hriego_jetson_runtime.json`) |
| FSM | GREETING → GUIDANCE → FAREWELL | termina tras el saludo: `HRIEGO_STOP_BEFORE=GUIDANCE` |
| SciPy / scikit-image / soundfile | instalados | respaldos NumPy/wave equivalentes (`uniface/_scipy_fallback.py`, `uniface/_similarity.py`, `supertonic_worker.py`) |

`x2-hri` se construye sin internet (`Dockerfile.hri.offline`, contexto `deploy/.wheels`,
no versionado): paquetes cp310 aarch64 extraídos de imágenes locales + `libGL` del host.

Arranque (cuando se decida probar):

```bash
ssh agi@10.0.1.41
cd ~/x2_ultra
deploy/dockerd_x2.sh                                         # tras cada reinicio del robot
(cd llm && CTX_SIZE=8192 NGL=0 docker compose up -d)          # x2-llm (CPU)
docker exec x2-llm curl -sf http://127.0.0.1:8000/ready       # esperar {"ready": true}
docker compose -f deploy/compose.hri.yml run --rm hri python3 -m hriego_v2.main --check-only
docker compose -f deploy/compose.hri.yml up -d && docker compose -f deploy/compose.hri.yml logs -f
```

Verificado en el Orin (contenedores desechables, sin arrancar el sistema): lectura de la
cámara RGB-D por ROS (1280×720 a ~32 fps), carga de UniFace y de Supertonic 3 (1,7 s),
síntesis "Hola, bienvenido a la universidad." en 4,2 s para 2,7 s de audio (CPU).
**No probado**: reproducción por los altavoces (AimDK), LLM en el Orin, visita completa.

## 14. Bóveda cifrada y registro de accesos

En el robot hay varias cuentas con sudo (`agi`, `run`, `key`) y contraseña compartida:
los permisos de archivo no protegen el proyecto. `deploy/boveda.sh` cifra `~/x2_ultra`
con **gocryptfs** (binario estático arm64 v2.4.0 en `deploy/.tools/gocryptfs`, no
versionado) usando una frase de paso que **no se guarda en el robot**, registra los
accesos en un log encadenado con hashes y hace hablar al robot si detecta un intruso.
No toca SSH, usuarios, grupos, sudoers ni servicios del sistema.

| Ruta en el robot | Qué es |
|---|---|
| `~/.x2_boveda/` | datos cifrados (+ `gocryptfs.conf`; guarda una copia en el PC) |
| `~/x2_ultra/` | punto de montaje: vacío con la bóveda cerrada |
| `~/.x2_boveda_tools/` | FUERA de la bóveda: `boveda.sh`, `gocryptfs`, `boveda_alarma.py`, `alarma.wav`, `confianza.conf`, `accesos.log`, pid del vigilante |

Comandos (tras `instalar`, usar siempre la copia instalada `B=~/.x2_boveda_tools/boveda.sh`):

| Comando | Qué hace |
|---|---|
| `deploy/boveda.sh instalar` | copia script, gocryptfs (verifica sha256) y alarma a `~/.x2_boveda_tools/` |
| `$B crear` | una vez: `gocryptfs -init`, copia `~/x2_ultra` dentro (como root, conserva dueños), verifica lista+tamaños+contenido, deja el original en `~/x2_ultra.SIN_CIFRAR` y abre la bóveda. Se niega si x2-hri/x2-llm corren o si ya existe; pide `SI` |
| `$B borrar-original` | borra `~/x2_ultra.SIN_CIFRAR` cuando hayas comprobado que todo funciona |
| `$B abrir` | pide la frase de paso (la lee gocryptfs, no el script) y monta con `sudo … -allow_other` (los contenedores, que corren como root, la ven). Registra éxito/fallo; un fallo hace sonar la alarma |
| `$B cerrar [--forzar]` | `docker compose down` de x2-hri y x2-llm (+ cualquier contenedor que monte la bóveda), comprueba que nadie la usa (`--forzar` mata esos procesos) y desmonta |
| `$B estado [N]` | abierta/cerrada, vigilante, sesiones actuales (CONFIABLE/DESCONOCIDO), últimas N entradas |
| `$B auditar [--desde 24h]` | logins SSH aceptados y fallidos (journal o auth.log) con IP, puerto, método, huella de clave, MAC (si sigue en `ip neigh`) y CONFIABLE/DESCONOCIDO |
| `$B vigilar` / `vigilar-parar` | vigilante en segundo plano (root vía sudo, nohup): sigue auth.log, y con la bóveda abierta revisa cada 5 s qué procesos tienen cwd/descriptores dentro, de qué sesión SSH vienen (IP, huella) y qué contenedores la montan |
| `$B verificar-log` | comprueba la cadena de hashes de `accesos.log` |
| `$B copiar-log` | imprime el `scp` para bajar el registro al PC |
| `$B generar-alarma ["texto"]` | una vez y con la bóveda abierta: Supertonic (voz M1, es) en x2-hri sin red → `alarma.wav` |
| `$B probar-alarma` | reproduce la alarma por los altavoces (AimDK) |

Identidades de confianza en `~/.x2_boveda_tools/confianza.conf` (líneas `huella …`,
`ip …`, `mac …`, `imagen …`, `proceso …` y ajustes del vigilante). Por defecto: la clave
`SHA256:7EHEBLSXqwkXx/knC8EmKtRo7fikzmEuDIdahvxkRsg` y la IP `10.0.1.50` (PC del dueño,
también cuando entra con contraseña), e imágenes `x2-hri:latest` y `x2-ultra/llm:*`.

Alertas (entrada `nivel=ALERTA` + alarma hablada, como mucho una cada 2 min): login
aceptado DESCONOCIDO, login fallido desde IP no confiable, `abrir` fallido, proceso de
una sesión no confiable o sin sesión SSH usando la bóveda abierta, contenedor de una
imagen no confiable que la monta. Mensaje: *"Alerta. Se ha detectado un intento de
acceso no autorizado al contenido de otro equipo. Acceder a la información de otros
equipos sin permiso es ilegal. Este acceso ha quedado registrado."* Se reproduce con
`boveda_alarma.py` (autónomo, sin importar nada del repo) en un contenedor desechable
de `x2-hri:latest` con red/ipc del host y el perfil DDS de AgiBot, montando solo
`~/.x2_boveda_tools` en solo lectura. Si dockerd está parado se registra
"alarma sin audio: dockerd parado".

Primera vez:

```bash
cd ~/x2_ultra && deploy/boveda.sh instalar
B=~/.x2_boveda_tools/boveda.sh
$B crear                      # anota la CLAVE MAESTRA que imprime gocryptfs FUERA del robot
$B vigilar
deploy/dockerd_x2.sh          # (desde ~/x2_ultra, ya abierta) si dockerd no corre
$B generar-alarma && $B probar-alarma
$B borrar-original            # cuando todo funcione
```

Flujo de cada prueba: **abrir → probar → cerrar**

```bash
B=~/.x2_boveda_tools/boveda.sh
$B vigilar                    # tras cada reinicio (no hay servicio instalado)
$B abrir
cd ~/x2_ultra && deploy/hri_sin_actuacion.sh arrancar   # …la prueba…
cd ~ && $B cerrar             # para contenedores y desmonta
$B estado; $B auditar --desde 24h
```

Desde el PC, de vez en cuando: `scp agi@10.0.1.41:.x2_boveda_tools/accesos.log ./boveda_accesos_$(date +%Y%m%d_%H%M).log`
(cada copia nueva debe empezar exactamente por la anterior).

**Límites (honestos):**

- Con la bóveda **abierta**, cualquiera con sudo (o con acceso a docker, que equivale a
  root) puede leerla. El cifrado protege con la bóveda **cerrada**: ciérrala al terminar.
- root puede matar al vigilante, borrar o reescribir `accesos.log` y recalcular la cadena.
  La cadena solo detecta ediciones/borrados parciales; contra un borrado total sirve la
  copia en el PC. `VIGILANCIA_FIN`/huecos en los `LATIDO` horarios delatan un vigilante caído.
- El vigilante muestrea cada 5 s: una lectura muy corta (un `cat`) entre dos muestras puede
  pasar sin verse. No ve accesos desde contenedores con otras rutas salvo por sus montajes.
- La confianza por IP/MAC se puede suplantar en el cable; la huella de clave no. Si solo
  entras con tu clave, borra la línea `ip` de `confianza.conf`.
- La MAC del historial (`auditar`) solo sale si la IP sigue en la tabla de vecinos; el
  vigilante la anota en el momento del login.
- Tras **reiniciar** el robot la bóveda queda cerrada (bien) y el vigilante parado. No se
  instala ningún servicio porque no se cambia la configuración del sistema. Opcional, a
  decisión del dueño (no se instala): una entrada `@reboot` con `crontab -e`. Como el
  vigilante necesita root y no se toca sudoers, tendría que ir en el crontab de root
  (`sudo crontab -e`):
  `@reboot sleep 60 && BOVEDA_OWNER_HOME=/home/agi /home/agi/.x2_boveda_tools/boveda.sh _vigilar >>/home/agi/.x2_boveda_tools/vigilar.out 2>&1`
- Las imágenes Docker (`/var/lib/docker`) no se cifran, y borrar `~/x2_ultra.SIN_CIFRAR`
  en un SSD no garantiza que sus bloques no sean recuperables forensemente.
- Sin frase de paso **y** sin clave maestra, los datos no se pueden recuperar.
