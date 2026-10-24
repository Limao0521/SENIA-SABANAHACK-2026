# 07 · Seguridad y privacidad

## Seguridad física del robot

Un humanoide de ~40 kg moviendo los brazos junto a personas exige capas independientes. Ninguna depende
del LLM.

| Capa | Qué hace | Dónde |
|---|---|---|
| **Armado explícito** | Mover el robot exige a la vez `--arm`, `X2_REAL=robot`, `HRI_ACTUATION=1` y escribir `ARMAR`; la etapa `seco` no publica nada | `live_robot_actuado.safety_preflight`, `deploy/hri_club_con_actuacion.sh` |
| **Preflight** | Antes de armar: lectura de brazos, pose (IMU + odometría de piernas), de pie, inclinación < 8°, batería conocida y ≥ 30 %, MC en STAND/URS, ningún otro programa mandando brazos o velocidad | `preflight_robot` |
| **Carga antes de armar** | TTS, LLM, ASR y micrófono se cargan y calientan antes de armar (cargarlos armado dejó sin servicio el bucle de 50 Hz) | `main()` |
| **Limitador** | Velocidad y aceleración por articulación (etapa lenta: 5 rad/s brazos, 10 rad/s emotes; 40 rad/s²) | `backends/aimdk.py` (`Limiter`) |
| **Interlock de caída** | Pelvis < 0,62 m o inclinación > 12° (> 25° siempre) durante 3 ciclos ⇒ abortar gestos, soltar el control y STAND | `FallInterlock` |
| **Watchdog** | 5 Hz: interlock, e-stop, brazos sin lectura > 1 s o pose sin actualizar > 0,5 s ⇒ e-stop y fin de la sesión | `Watchdog` |
| **Sin marcha** | La locomoción está deshabilitada en esta entrega (se rechaza en el runner y en el backend) | `GestureSync.walk_to` |
| **Cintura bloqueada** | Sin movimiento de cintura en las etapas seco y lenta | `make_config` |
| **Mando del fabricante** | Prioridad 80 frente a la nuestra (65): el mando o la app de AgiBot siempre toman el control | AimDK |
| **Parada ordenada** | Ctrl-C, `docker stop`, error o `parar` ⇒ velocidad 0, gestos abortados, soltar la fuente de control y pedir STAND | `finally` de `main()` |
| **Lista blanca ROS** | Los nodos de audio/cámara/cara solo pueden publicar audio y llamar al foco de audio, la cara y el asistente; cualquier otro publicador o servicio se rechaza antes de DDS | `hriego_v2/robot_io.py`, `deploy/hri_audit_ros.py` |
| **Emotes verificados** | Los 12 emotes propios sin autocolisiones en el modelo oficial del robot y < 5 rad/s | `scripts/check_emotes_mujoco.py` |

## Privacidad

- **Todo a bordo**: caras, voces y conversaciones se procesan en el robot; no salen a internet (el robot
  no tiene conexión). La opción de inferencia en la nube (apagada) excluye explícitamente caras y QR.
- **Galería facial del equipo incluida**: `robot/Face/data/institutional_identity.sqlite3` contiene los
  embeddings faciales de los miembros del equipo y `robot/assets/hri/identities.json` sus nombres y
  categorías demo. Están en esta entrega por decisión del equipo; **no publiques este repositorio en
  abierto sin el consentimiento de cada persona** o sin sustituir la galería por una vacía
  (`robot/Face/live/registry_cli.py` permite registrar personas de nuevo).
- **Fotos**: el runner guarda las fotos de cada visita y del escaneo del QR en `out_reto2/live/fotos/`
  (fuera de git) para diagnóstico.
- **Bóveda cifrada**: en el robot, cuya cuenta comparten varios equipos, el repositorio vive en una
  bóveda gocryptfs con registro de accesos y alarma hablada (`robot/deploy/boveda.sh`); solo se abre con
  la frase de paso, que no se guarda en el robot.
- **Credenciales de la app**: cuentas demo fijas y SMTP opcional configurado a mano
  (`app/lib/core/email_config.dart`); no hay credenciales reales en el repositorio.
