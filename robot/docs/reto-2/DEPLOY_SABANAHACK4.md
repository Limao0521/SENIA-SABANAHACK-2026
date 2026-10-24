# Deploy sabanahack 4.0 — HRI conversacional con actuación en el X2 Ultra real

Rama: `sabanahack4.0-deploy` = `claude/reto2-hri` (backend validado en el gemelo: rampas SONIC,
cintura bloqueada, cabeza) + `sabanahack-3.0` (Alfred/Draco, QR) + runner a bordo con actuación.

Qué hace el robot en esta entrega: escucha (micrófono del robot → ASR en la GPU), conversa
(FSM del Club N + Qwen3-4B), habla (Supertonic por los altavoces) y **gesticula**: emotes de
AgiBot reproducidos por URS y señalamiento hacia el destino, anclados a la palabra de la frase.
Qué **no** hace: caminar, co-speech SONIC (no corre en el Orin), D1 (solo registro), mover la
cintura. Detalle técnico y seguridad: [DEPLOY_X2_REAL.md](DEPLOY_X2_REAL.md) §2 y §6.

## 0. Antes de ir al robot (en el PC)

| Qué | Cómo | Estado |
|---|---|---|
| Rama | `git switch sabanahack4.0-deploy` | lista |
| Tests | `PYTHONPATH=src python -m pytest tests/test_hri_live_robot_actuado.py tests/test_hri_live_robot.py -q --noconftest` | 60/60 |
| Imagen del ASR arm64 | `docker pull --platform linux/arm64 dustynv/faster-whisper:r36.4.0-cu128-24.04` | ya en el PC (2026-10-24); **~17 GB**: el robot necesita ese espacio libre (`conectar` lo muestra) |
| CSV de emotes | `scripts/fetch_hri_assets.sh` (necesita el `mc_param` del simulador oficial) | **opcional**: si no, se usan los del robot (`/agibot`) |

## 1. Conectar

Cable PC ↔ robot. El PC necesita IP fija en 10.0.1.x:

- Linux: `nmcli con up "X2 robot"`
- Windows (PowerShell como administrador):
  `New-NetIPAddress -InterfaceAlias Ethernet -IPAddress 10.0.1.100 -PrefixLength 24`

```bash
deploy/x2_deploy.sh conectar      # ping + ssh + L4T + disco/RAM + dockerd
```

La clave `~/.ssh/x2_ultra_ed25519` se usa sola si existe. Usuario/host: `X2_HOST=agi@10.0.1.41`.

## 2. Llevar todo al robot (desde el PC)

```bash
deploy/x2_deploy.sh todo
# equivale a:
deploy/x2_deploy.sh codigo        # git archive del commit actual → ~/x2_ultra (+ archivo DEPLOYED)
deploy/x2_deploy.sh modelos       # solo lo que falte: ASR 1,6 GB, LLM 2,4 GB, Supertonic, UniFace, CSV del MC
deploy/x2_deploy.sh imagenes      # dustynv/faster-whisper si falta (pull en el PC → docker load)
deploy/x2_deploy.sh listo         # comprobación a bordo, solo lectura
```

`codigo` despliega el **último commit**, no los cambios sin commitear (avisa si los hay).
Las imágenes propias (`x2-onboard`, `x2-hri`, `x2-ultra/llm:*`) se construyen **en** el Orin
(no se pueden traer desde x86); según `deploy/README.md` §13 ya estaban construidas el 2026-10-23.

## 3. Comprobación a bordo (`deploy/x2_listo.sh`, solo lectura)

En el robot (`deploy/x2_deploy.sh ssh` o `ssh agi@10.0.1.41`), primero `deploy/dockerd_x2.sh`
(tras cada reinicio del robot) y luego:

```bash
deploy/x2_listo.sh
```

Revisa: commit desplegado, dockerd, imágenes, modelos, CSV de emotes, imports del runner dentro
de `x2-hri` (sin mujoco), tópicos ROS del robot (`/aima/mc/common/state`, brazos, odometría,
micrófono) y que nadie más publique brazos/velocidad, y el **preflight real** del runner (lee el
robot: batería, pelvis, modo del MC; no arma ni publica órdenes). Debe terminar en `LISTO`.

## 4. Sesión en el robot (no saltar pasos)

Robot de pie en STAND_DEFAULT con el mando/app, ≥ 2 m libres, e-stop inalámbrico en la mano de
una persona dedicada. Las etapas armadas piden escribir `ARMAR`.

```bash
deploy/hri_club_sin_actuacion.sh parar                         # por si quedó la fase 1
# a) dry-run completo: conversación real, el backend registra lo que publicaría
deploy/hri_club_con_actuacion.sh seco --visit STAFF
deploy/hri_club_con_actuacion.sh logs                           # [motor] EMOTE/POINT + latencias
deploy/hri_club_con_actuacion.sh parar
# b) prueba armada lenta, sin conversación: READY 5 s → emote 3001/11 → señal LEFT → STAND
deploy/hri_club_con_actuacion.sh prueba --etapa lenta
# c) conversación con gestos, lenta (5/10 rad/s)
deploy/hri_club_con_actuacion.sh arrancar --etapa lenta --visit STAFF
deploy/hri_club_con_actuacion.sh estado                         # limiter_hit / fall_interlock
deploy/hri_club_con_actuacion.sh parar                          # vel 0 + DELETE fuente URS + STAND
# d) solo si c) fue limpio: límites del spec
deploy/hri_club_con_actuacion.sh arrancar --etapa completa --visit VIP
```

Criterios para pasar de etapa: en b) `[prueba] READY` con error medio ≲ 0,03 rad y
`limiter_hits` 0, el emote y la señal se ven suaves; en c) ningún `fall_interlock`, pocos
`limiter_hit`, el gesto cae con la palabra y `parar` deja el robot en STAND.

Parada: `parar` (ordenada) o, ante cualquier duda, **e-stop inalámbrico / mando** (prioridad 80
gana a la nuestra, 65). El runner además hace e-stop solo si salta el interlock de caída o se
pierde el feedback de brazos > 1 s.

## 5. Si algo falla

| Síntoma | Qué hacer |
|---|---|
| `x2_deploy.sh conectar`: no responde | IP fija del PC en 10.0.1.x; cable; robot encendido |
| `listo`: falta `dustynv/faster-whisper` | `deploy/x2_deploy.sh imagenes` desde el PC |
| `listo`: falta `x2-hri`/`x2-onboard` | en el robot: `deploy/build_aarch64.sh`, luego `Dockerfile.hri.offline` (ver `deploy/README.md` §13) |
| `listo`: sin CSV de emotes | `MC_CLASSIC_DIR=/ruta/al/classic deploy/x2_listo.sh`; si no hay, solo habrá señalamiento |
| `listo`: publicador ajeno en `/mc/upper_body_command` | parar `x2-onboard`/teleop/app que esté mandando brazos |
| preflight: `sin pose de odometría` | sin ella el interlock no ve caídas: **no armar**; revisar `/aima/hal/odom/state` |
| preflight: `MC en ...` | poner STAND_DEFAULT con el mando/app |
| `aimdk_msgs.msg.McBodyPose` en AVISO | la cabeza no girará; el resto funciona |
| LLM lento (CPU) | normal sin `x2-ultra/llm:jetson`; la FSM tiene frases de respaldo |

## Expresiones faciales (pantalla de la cara)

La cara del robot cambia de expresión siguiendo la conversación mediante el servicio
`/aimdk_5Fmsgs/srv/PlayEmoji`, tipo `aimdk_msgs/srv/PlayEmoji`: petición con `emotion_id`
(uint8), `mode` (1 = una vez, 2 = bucle) y `priority` (int32); respuesta `success` + `message`.
Una petición con prioridad **>=** la actual sustituye a la que se muestra; los módulos del
sistema usan 8 (sobretemperatura) y 10 (fallo grave), pero en el X2 real la cara ya se reproduce a
prioridad **60** (verificado a bordo: con 11 responde «prioridad menor que la actual, se ignora»),
así que aquí se usa **60** por defecto (igual sustituye; no se impone por encima).
El servicio lo sirve el PC de interacción (PC3): la llamada cruza de máquina y se hacen
reintentos cortos. Fuentes: docs X2 AimDK v0.8 «5.2.2 Screen Control»
(<https://x2-aimdk.agibot.com/en/latest/Interface/interactor/screen.html>) y ejemplo Python
6.1.26 `play_emoji` (<https://x2-aimdk.agibot.com/en/latest/example/Python.html>).

**Componente**: `src/x2_ultra/hri_runtime/face.py`, enganchado en `live_robot_actuado.py`
(sesión, identidad y gestos) y en `SpeechPlayer.on_play/on_idle` (`live_robot.py`). Variables
de entorno (el compose con actuación las pone): `HRI_EXPRESIONES` (1 = activa; 0/ausente =
objeto inerte), `HRI_EXPRESIONES_EN_SECO` y `HRI_EXPRESIONES_PRIORIDAD` (defecto 60: en el X2 real la cara ya se reproduce a 60 y con menos se rechaza, verificado el 2026-10-24). En la
etapa `seco` va solo en modo registro (líneas `[cara] …`, sin ROS), salvo
`HRI_EXPRESIONES_EN_SECO=1` (la pantalla no es actuación: permite probar la cara sin armar);
en `lenta`/`completa` llama de verdad. Si el tipo `aimdk_msgs.srv.PlayEmoji` o el servicio no
existen, la función **se desactiva sola** con una línea de log (`DESACTIVADAS`). Un nodo y un
cliente por sesión (executor compartido), hilo de trabajo propio con deduplicación y límite de
cadencia: los ganchos nunca bloquean la conversación.

**Lista blanca**: `hriego_v2/robot_io.py` (`ALLOWED_SERVICES`) y `deploy/hri_audit_ros.py`
admiten ahora `PlayEmoji` (solo pantalla) además del foco de audio.

| Momento | Expresión | Emoji (id, modo) |
|---|---|---|
| esperando visitante | espera | 10 calma, bucle |
| cara detectada | curioso | 50 abrir ojos, una vez |
| pidiendo el QR (habla) | hablando | 90 feliz, bucle |
| esperando el QR | atento | 11 calma (ojos 2), bucle |
| micrófono abierto | escuchando | 1 parpadeo, bucle |
| ASR/LLM/TTS en curso | pensando | 170 pensando, bucle |
| hablando (conversación, guía) | hablando | 90 feliz, bucle |
| saludo y despedida (estados GREETING/FAREWELL, gestos GREETING_*, FAREWELL_WAVE, THUMBS_UP) | alegre | 100 muy feliz, bucle |
| SOCIAL_HEART | encantado | 200 adoración, bucle |
| APPLAUSE | euforico | 101 eufórico, bucle |
| NEGATION_NORMAL y derivación a personal | disculpa | 120 compasión, bucle |
| NEGATION_STRONG | serio | 160 serio, bucle |
| QR rechazado | triste | 110 triste, bucle |
| fin de visita (cola de voz vacía en END) | espera | 10 calma, bucle |
| fin de sesión (`stop()`) | reposo | 10 calma, **una vez** (no deja un bucle a prioridad 60 tapando la cara del sistema) |

**Verificación** (solo pantalla: no mueve nada ni arma nada). En el robot, desde `~/x2_ultra`:

```bash
docker run --rm --network host --ipc host -e RMW_IMPLEMENTATION=rmw_fastrtps_cpp -e ROS_LOCALHOST_ONLY=0 -e ROS_LOG_DIR=/tmp/ros_logs -e FASTRTPS_DEFAULT_PROFILES_FILE=/agibot/software/entry/cfg/ros_dds_configuration.xml -v /agibot/software/entry/cfg/ros_dds_configuration.xml:/agibot/software/entry/cfg/ros_dds_configuration.xml:ro -v ~/x2_ultra:/workspace/x2_ultra -e PYTHONPATH=/workspace/x2_ultra/src:/workspace/x2_ultra x2-hri:latest python3 /workspace/x2_ultra/deploy/hri_probar_cara.py [--ids 90,170,1] [--segundos 3] [--lista]
```

Además, `deploy/x2_listo.sh` comprueba el import de `aimdk_msgs.srv.PlayEmoji` dentro de
`x2-hri` (§6) y la presencia del servicio en la red (§7, `x2_ros_check.py`); si faltan es un
AVISO, nunca un FALLO. Durante una sesión, el log del runner debe mostrar líneas como
`[cara] pensando → emoji 170 (pensando, bucle) · turno`.

> **Pendiente: verificado solo en el PC (robot sin conexión el 2026-10-24); IDs y servicio
> según la documentación pública de AimDK X2, sin confirmar en el firmware del robot.**
