# 03 · Despliegue y ejecución en el robot

Guía paso a paso para llevar el sistema al AgiBot X2 Ultra y arrancarlo. El robot no tiene internet:
todo se lleva desde el PC por el cable Ethernet.

> Referencias detalladas: `robot/docs/reto-2/DEPLOY_SABANAHACK4.md` (guía completa) y
> `robot/docs/reto-2/DEPLOY_X2_REAL.md` (seguridad y etapas).

## Requisitos

| Dónde | Qué |
|---|---|
| Robot | AgiBot X2 Ultra, firmware v1.1.4, PC2 Jetson Orin NX (JetPack 6 / L4T r36.4), Docker |
| Robot (imágenes) | `x2-onboard:latest`, `x2-hri:latest` (se construyen a bordo: `deploy/build_aarch64.sh`, `deploy/Dockerfile.hri.offline`), `x2-ultra/llm:jetson`, `dustynv/faster-whisper:r36.4.0-cu128-24.04` (se lleva desde el PC) |
| Modelos (no van en git) | `models/faster-whisper-large-v3-turbo` (1,6 GB), `llm/models/Qwen3-4B-Q4_K_M.gguf` (2,4 GB), Supertonic en `experiments/hriego_v2_tts_eval_20261021/model`, `Face/thirdparty/uniface-models` |
| PC | Git Bash o Linux, `ssh`, `docker` (para pasar la imagen del ASR), clave ssh del robot |
| Seguridad | Mando/app de AgiBot a mano, e-stop inalámbrico, ≥ 2 m libres, robot de pie en STAND_DEFAULT, batería ≥ 30 % |

## 1. Conectar y llevar todo al robot (desde el PC)

Cable PC ↔ robot; el PC con IP fija en 10.0.1.x (por ejemplo 10.0.1.50). Desde `robot/`:

```bash
deploy/x2_deploy.sh conectar     # ping + ssh + disco/RAM + dockerd
deploy/x2_deploy.sh todo         # código + modelos que falten + imágenes externas + comprobación
```

## 2. En el robot

```bash
ssh agi@10.0.1.41
cd ~/x2_ultra
deploy/dockerd_x2.sh             # tras cada reinicio del robot (pide sudo)
deploy/x2_listo.sh               # solo lectura: debe terminar en «LISTO»
```

`x2_listo.sh` comprueba imágenes, modelos, que el runner importe bien, los tópicos ROS del robot
(brazos, IMU, odometría de piernas, micrófono), que nadie más publique órdenes, el servicio de la cara y
un preflight real que lee el robot sin armarlo.

Si el repositorio está en la bóveda cifrada (ver [07](07-seguridad-y-privacidad.md)), primero
`~/.x2_boveda_tools/boveda.sh abrir`.

## 3. Arrancar la recepción

```bash
deploy/hri_club_con_actuacion.sh seco --identidad                             # ensayo: no mueve el robot
deploy/hri_club_con_actuacion.sh arrancar --etapa lenta --identidad --repetir # el robot mueve los brazos (escribe ARMAR)
deploy/hri_club_con_actuacion.sh logs                                         # conversación, gestos, latencias
deploy/hri_club_con_actuacion.sh parar                                        # velocidad 0, suelta el control, pide STAND
```

| Etapa | Qué hace |
|---|---|
| `seco` | Toda la cadena real (micrófono, ASR, LLM, voz, cámara, QR) pero el backend no publica nada al robot |
| `prueba --etapa lenta` | Solo brazos: READY, un emote y un señalamiento, sin conversación |
| `arrancar --etapa lenta` | Conversación con actuación a 5 rad/s (brazos) y 10 rad/s (emotes), cintura bloqueada |
| `arrancar --etapa completa` | Límites nominales (10/20 rad/s) — solo después de una sesión lenta limpia |

Tras `parar` el robot queda en el modo de brazos remotos (URS) y el firmware no permite salir por SDK:
**pon Stand en la app de AgiBot** para recuperar el control remoto.

Opciones útiles (variables de entorno del compose `deploy/compose.hri_club_actuado.yml`):

| Variable | Defecto | Efecto |
|---|---|---|
| `HRI_ACUSE` | 1 | acuse inmediato mientras el LLM piensa |
| `HRI_TTS_PREFETCH` | 1 | pre-síntesis de la primera cláusula |
| `HRI_EXPRESIONES` | 1 | emojis en la pantalla de la cara |
| `HRI_PAUSAR_ASISTENTE` | 1 | asistente de voz de fábrica en pausa durante la sesión |
| `HRI_COSPEECH_AMP` | 0.7 | amplitud del gesto co-verbal |
| `HRI_QR_TIMEOUT_S` | 20 | tiempo para mostrar el QR |
| `HRI_MIN_CARA_PX` | 70 | tamaño mínimo de cara para considerar a alguien visitante |

## Probar en el PC (sin robot)

Pruebas automáticas (sin ROS ni GPU):

```bash
cd robot
PYTHONPATH="src:." python -m pytest tests/test_hri_live_robot_actuado.py tests/test_hri_live_robot.py \
    tests/test_hri_confirm.py tests/test_hri_face.py tests/test_hri_asistente.py tests/test_hri_nube.py -q --noconftest
```

Conversación por teclado con el backend en seco (necesita un LLM OpenAI-compatible en
`HRI_LLM_URL`; sin él, la máquina de estados usa frases de respaldo):

```bash
cd robot
PYTHONPATH="src:." python -m x2_ultra.hri_runtime.live_robot_actuado --etapa seco \
    --entrada stdin --sin-voz --permitir-sin-llm --visit VIP
```

El LLM se levanta con `llm/docker-compose.yml` (ver `robot/llm/README.md`).
