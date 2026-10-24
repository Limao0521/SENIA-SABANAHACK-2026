# Prueba 2026-10-24 09:16 — identidad real (cara + QR) con actuación, etapa lenta

Rama `sabanahack4.0-deploy` @ `12f8524`. Comando: `deploy/hri_club_con_actuacion.sh arrancar --etapa lenta --identidad --repetir`.

## Resultado

| Qué | Resultado |
|---|---|
| Armado / READY | OK (error medio 0,014 rad) |
| Cara | Josaed, Luis (×2), Mario, Sebastian (×2) reconocidos (cos 0,47–0,60); 1 no miembro |
| QR | **1 de 6 leído** (Mario, 8,1 s); 5 × `QR_TIMEOUT` |
| Conversación (Mario, GUEST) | 3 turnos; ASR 1,5–1,7 s; fin de habla → primer audio 11,8–16,3 s |
| Gesto co-verbal (clips SONIC) | visible, OK |
| Emotes de saludo/despedida | **no** (sin CSV de AgiBot: `emote_unavailable`) |
| Backend | 0 caídas, 0 e-stop; `limiter_hit` 1150 (co-verbal a 5 rad/s); `svc_timeout` 1 |
| Fallo | el executor ROS compartido de hriego murió (`InvalidHandle: destruction was requested`) al destruir el nodo de la cámara de una foto ⇒ sin foco de audio (robot mudo) y sin cámara ⇒ la sesión abortó (parada ordenada) |

## Pendientes que salen de la prueba

1. Cámara persistente (no crear/destruir un nodo por foto).
2. Lectura de QR más robusta (multiescala / recorte central; guardar fotogramas para diagnóstico).
3. WORKER (EXTERNAL): no pedir QR; derivar directo a personal.
4. Mencionar que Draco (D1) también puede acompañar/redirigir.
5. Emotes de saludo/despedida sin los CSV de AgiBot.

## Log

```text
[seguridad] etapa=lenta dry_run=False joint_vmax=5.0 emote_vmax=10.0 amax=40.0 cintura=off cabeza=body_pose marcha=deshabilitada SONIC=clips-precomputados prioridad=65
[tts] motor: supertonic (calentado en 2.0 s)
[llm] x2-llm listo en http://127.0.0.1:8080
[llm] calentado en 7.0 s
[asr] conectado a /tmp/x2_hri_sock/hri_asr.sock
[gesto] co-verbal: 103 clips SONIC precomputados, amplitud 70%
[mic] suscrito a /aima/hal/audio/capture (QoS best_effort)
[mic] formato detectado: {"channels": 6, "sample_rate": 16000, "sample_format": "S16LE", "coding_format": "pcm", "mic_channels": 4, "ref_channels": 2} · canal usado: 0
[preflight] modo=UPPERBODY_REMOTE_SPLIT batería=70 pelvis=0.7199962547795582 tilt=3.5650256497085007 pose=imu_torso+leg_odom fuente_MC=pnc brazos=sí CSV de emotes=0 objetivos de señalamiento=['RIGHT', 'LEFT', 'FRONT'] McBodyPose=sí
[preflight] AVISO: sin CSV de emotes (X2_MC_CLASSIC / scripts/fetch_hri_assets.sh): los EMOTE se registrarán como emote_unavailable; solo habrá señalamiento y READY
[armado] altura de referencia (leg_odometry): ok
[armado] fuente URS registrada; READY a 50 Hz desde la postura medida
[prueba] READY 5 s: {'segundos': 5.0, 'limiter_hits': 2, 'err_max_rad': 0.059, 'err_medio_rad': 0.014}
[identidad] esperando a un visitante delante de la cámara…
[identidad] cara UNKNOWN (0.38262951374053955, 134 px); confirmo con otra foto
[identidad] cara KNOWN: Josaed (0.6023629307746887) · foto /workspace/x2_ultra/out_reto2/live/fotos/visita_20261024_091718.jpg
  robot   : Hola, Josaed. Para confirmar su membresía, por favor muéstreme su credencial QR frente a mi cámara.
  robot   : Acerque un poco más el código QR a mi cámara, por favor.
[identidad] REJECTED · - · rango - · fuente - · motivo QR_TIMEOUT
[sesión] visita 1 terminada (estado END)
[identidad] esperando a un visitante delante de la cámara…
[identidad] cara UNKNOWN (0.3934261202812195, 171 px); confirmo con otra foto
[identidad] cara UNKNOWN (0.3618830442428589, 109 px); confirmo con otra foto
[identidad] cara KNOWN: Luis (0.5865541696548462) · foto /workspace/x2_ultra/out_reto2/live/fotos/visita_20261024_091829.jpg
  robot   : Hola, Luis. Para confirmar su membresía, por favor muéstreme su credencial QR frente a mi cámara.
  robot   : Acerque un poco más el código QR a mi cámara, por favor.
[identidad] REJECTED · - · rango - · fuente - · motivo QR_TIMEOUT
[sesión] visita 2 terminada (estado END)
[identidad] esperando a un visitante delante de la cámara…
[identidad] cara UNKNOWN (0.05902353301644325, 114 px); confirmo con otra foto
[identidad] cara KNOWN: Luis (0.5873152017593384) · foto /workspace/x2_ultra/out_reto2/live/fotos/visita_20261024_091929.jpg
  robot   : Hola, Luis. Para confirmar su membresía, por favor muéstreme su credencial QR frente a mi cámara.
  robot   : Acerque un poco más el código QR a mi cámara, por favor.
[identidad] REJECTED · - · rango - · fuente - · motivo QR_TIMEOUT
[sesión] visita 3 terminada (estado END)
[identidad] esperando a un visitante delante de la cámara…
[identidad] cara UNKNOWN (0.30680349469184875, 98 px); confirmo con otra foto
[identidad] cara UNKNOWN: - (0.3950841426849365) · foto /workspace/x2_ultra/out_reto2/live/fotos/visita_20261024_092029.jpg
[identidad] REJECTED · - · rango - · fuente - · motivo NO_MIEMBRO
[sesión] visita 4 terminada (estado END)
[identidad] esperando a un visitante delante de la cámara…
[identidad] cara UNKNOWN (0.030551468953490257, 316 px); confirmo con otra foto
[identidad] cara KNOWN: Mario (0.47625377774238586) · foto /workspace/x2_ultra/out_reto2/live/fotos/visita_20261024_092056.jpg
  robot   : Hola, Mario. Para confirmar su membresía, por favor muéstreme su credencial QR frente a mi cámara.
[identidad] QR leído en 8.1 s
[motor] EMOTE 1011/2 en +1.60 s (ancla 0.70 s)
──── turno admision · estado FSM: CONVERSATION
  robot   : Bienvenido a Club N, Mario; hoy también es parte de nuestra comunidad y le oriento paso a paso.
  motor   : {"act": "EMOTE", "type": "GREETING_GUEST"} → NO EJECUTADO (sin CSV de emotes)
  latencias (s): {"admision_s": 16.182, "primer_audio_s": 11.433, "audio_real_s": 8.05, "tts_first_audio_s": 10.529, "tts_estimated": false}
[identidad] CONFIRMED · Mario · rango GUEST · fuente qr
──── turno usuario #1 · estado FSM: CONVERSATION
  usuario : '¿A dónde puedo ir?'
  robot   : Mario, con gusto: puede visitar recepción y vestíbulos, restaurantes y cafeterías, centro de convenciones o biblioteca y galería, todos dentro del club.
  intent  : PREGUNTA · área: None
  latencias (s): {"asr_s": 1.654, "fsm_llm_tts_s": 19.012, "fin_habla_a_primer_audio_s": 14.701, "audio_real_s": 12.47, "turno_total_s": 28.198, "endpoint_silencio_s": 0.7, "app_llm_type_s": 4.12, "app_llm_first_sentence_s": 5.177, "app_tts_first_audio_s": 12.805, "app_tts_estimated": false}
──── turno usuario #2 · estado FSM: CONVERSATION
  usuario : '¿Puedes indicarme dónde está la galería?'
  robot   : Claro, la galería está al frente, piso 2, horario Lun a Dom, 08:00 a 20:00.
  intent  : PREGUNTA · área: biblioteca_galeria
  latencias (s): {"asr_s": 1.745, "fsm_llm_tts_s": 14.698, "fin_habla_a_primer_audio_s": 11.75, "audio_real_s": 8.17, "turno_total_s": 21.503, "endpoint_silencio_s": 0.7, "app_llm_type_s": 0.802, "app_llm_first_sentence_s": 3.809, "app_tts_first_audio_s": 9.784, "app_tts_estimated": false}
[motor] EMOTE 3031/11 en +1.71 s (ancla 0.81 s)
──── turno usuario #3 · estado FSM: END
  usuario : 'Muchas gracias.'
  robot   : Gracias por visitarnos, Mario; ya conoce nuestra casa, vuelva cuando quiera.
  intent  : DESPEDIDA · área: None
  motor   : {"act": "EMOTE", "type": "FAREWELL_WAVE"} → NO EJECUTADO (sin CSV de emotes)
  latencias (s): {"asr_s": 1.486, "fsm_llm_tts_s": 17.933, "fin_habla_a_primer_audio_s": 16.31, "audio_real_s": 7.03, "turno_total_s": 23.801, "endpoint_silencio_s": 0.7, "app_llm_type_s": 0.73, "app_llm_first_sentence_s": 1.752, "app_tts_first_audio_s": 13.918, "app_tts_estimated": false}
[sesión] visita 5 terminada (estado END)
[identidad] esperando a un visitante delante de la cámara…
[identidad] cara UNKNOWN (0.13542497158050537, 143 px); confirmo con otra foto
[identidad] cara KNOWN: Sebastian (0.49727150797843933) · foto /workspace/x2_ultra/out_reto2/live/fotos/visita_20261024_092323.jpg
  robot   : Hola, Sebastian. Para confirmar su membresía, por favor muéstreme su credencial QR frente a mi cámara.
  robot   : Acerque un poco más el código QR a mi cámara, por favor.
[identidad] REJECTED · - · rango - · fuente - · motivo QR_TIMEOUT
[sesión] visita 6 terminada (estado END)
[identidad] esperando a un visitante delante de la cámara…
[identidad] cara KNOWN: Sebastian (0.46981745958328247) · foto /workspace/x2_ultra/out_reto2/live/fotos/visita_20261024_092420.jpg
  robot   : Hola, Sebastian. Para confirmar su membresía, por favor muéstreme su credencial QR frente a mi cámara.
  robot   : Acerque un poco más el código QR a mi cámara, por favor.
Exception in thread hriego-ros:
Traceback (most recent call last):
  File "/usr/lib/python3.10/threading.py", line 1016, in _bootstrap_inner
    self.run()
  File "/usr/lib/python3.10/threading.py", line 953, in run
    self._target(*self._args, **self._kwargs)
  File "/opt/ros/humble/local/lib/python3.10/dist-packages/rclpy/executors.py", line 323, in spin
    self.spin_once()
  File "/opt/ros/humble/local/lib/python3.10/dist-packages/rclpy/executors.py", line 867, in spin_once
    self._spin_once_impl(timeout_sec)
  File "/opt/ros/humble/local/lib/python3.10/dist-packages/rclpy/executors.py", line 864, in _spin_once_impl
    future.result()
  File "/opt/ros/humble/local/lib/python3.10/dist-packages/rclpy/task.py", line 109, in result
    raise self.exception()
  File "/opt/ros/humble/local/lib/python3.10/dist-packages/rclpy/task.py", line 309, in _execute_coroutine_step
    result = coro.send(None)
  File "/opt/ros/humble/local/lib/python3.10/dist-packages/rclpy/executors.py", line 478, in handler
    arg = take_from_wait_list(entity)
  File "/opt/ros/humble/local/lib/python3.10/dist-packages/rclpy/executors.py", line 399, in _take_subscription
    with sub.handle:
rclpy._rclpy_pybind11.InvalidHandle: cannot use Destroyable because destruction was requested
[voz] error de reproducción: TimeoutError: Sin respuesta de /aimdk_5Fmsgs/srv/RequestAudioFocus
[voz] error de reproducción: TimeoutError: Sin respuesta de /aimdk_5Fmsgs/srv/RequestAudioFocus
[identidad] REJECTED · - · rango - · fuente - · motivo QR_TIMEOUT
[sesión] visita 7 terminada (estado END)
[identidad] esperando a un visitante delante de la cámara…
[identidad] sin imagen de /aima/hal/sensor/rgb_head_front_center/rgb_image/compressed (1)
[identidad] sin imagen de /aima/hal/sensor/rgb_head_front_center/rgb_image/compressed (2)
[identidad] sin imagen de /aima/hal/sensor/rgb_head_front_center/rgb_image/compressed (3)
[identidad] sin imagen de /aima/hal/sensor/rgb_head_front_center/rgb_image/compressed (4)
[identidad] sin imagen de /aima/hal/sensor/rgb_head_front_center/rgb_image/compressed (5)
[sesión] backend cerrado; eventos: /workspace/x2_ultra/out_reto2/live/actuado_lenta_20261024_092613_eventos.jsonl
Traceback (most recent call last):
  File "/usr/lib/python3.10/runpy.py", line 196, in _run_module_as_main
    return _run_code(code, main_globals, None,
  File "/usr/lib/python3.10/runpy.py", line 86, in _run_code
    exec(code, run_globals)
  File "/workspace/x2_ultra/src/x2_ultra/hri_runtime/live_robot_actuado.py", line 839, in <module>
    sys.exit(main())
  File "/workspace/x2_ultra/src/x2_ultra/hri_runtime/live_robot_actuado.py", line 805, in main
    code = session.run(photo, frame)
  File "/workspace/x2_ultra/src/x2_ultra/hri_runtime/live_robot.py", line 805, in run
    self.admit(photo, frame)
  File "/workspace/x2_ultra/src/x2_ultra/hri_runtime/live_robot_actuado.py", line 478, in admit
    return self.admit_identity()
  File "/workspace/x2_ultra/src/x2_ultra/hri_runtime/live_robot_actuado.py", line 487, in admit_identity
    got = self.wait_for_visitor()
  File "/workspace/x2_ultra/src/x2_ultra/hri_runtime/live_robot_actuado.py", line 584, in wait_for_visitor
    raise RuntimeError(f"la cámara {self.args.camara} no entrega imágenes")
RuntimeError: la cámara /aima/hal/sensor/rgb_head_front_center/rgb_image/compressed no entrega imágenes
===EVENTOS
/home/agi/x2_ultra/out_reto2/live/actuado_lenta_20261024_092613_eventos.jsonl
{'armed': 1, 'limiter_hit': 1150, 'emote_unavailable': 2, 'stop': 1, 'svc_timeout': 1}
x2-hri-actuado Exited (1) 2 minutes ago
x2-asr Up 49 minutes
x2-llm Up 49 minutes (healthy)
```
