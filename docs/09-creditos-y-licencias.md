# 09 · Créditos y componentes de terceros

## Equipo

Ver la sección «Equipo» del [README](../README.md). Repositorios de origen con el historial completo:

- Sistema del robot: [juanulloa2050/x2_ultra](https://github.com/juanulloa2050/x2_ultra), rama
  `sabanahack4.0-deploy`, commit `d59a340`.
- App Club N: [Lafrul/ClubNApp](https://github.com/Lafrul/ClubNApp), commit `2752d10`.

## Componentes de terceros

| Componente | Uso | Incluido en el repo | Licencia / fuente |
|---|---|---|---|
| AgiBot X2 Ultra, AimDK SDK (aimdk_msgs, ROS 2) | Interfaz con el robot | No (en el robot) | AgiBot |
| URDF y mallas oficiales del X2 (`agibot_x2_urdf`) | Simulación | Submódulo `robot/third_party/agibot_x2_urdf` (commit `575cc6b`) | Mulan PSL v2 |
| Política de marcha `agibot_mcrl_policy.onnx` | Simulación | Sí (`robot/assets/policies/`) | Apache 2.0 (`LICENSE-agibot-mc-rl.txt`) |
| Animaciones de fábrica de AgiBot (CSV del MC) | Emotes originales | **No** (licencia del fabricante) | Se obtienen con `scripts/fetch_hri_assets.sh` |
| UniFace | Reconocimiento facial | Sí (`robot/Face/thirdparty/uniface/`) | MIT |
| Qwen3-4B (GGUF Q4_K_M) | LLM a bordo | **No** (2,4 GB) | Qwen (Alibaba), ver su ficha en Hugging Face |
| llama.cpp | Servidor del LLM | No (imagen Docker) | MIT |
| faster-whisper / Whisper large-v3-turbo | ASR | No (modelo 1,6 GB) | MIT |
| Supertonic | TTS | No (modelo) | Ver la ficha del modelo |
| zxing-cpp | Lectura de QR | Sí (rueda arm64 en `robot/third_party/wheels/`) | Apache 2.0 |
| OpenCV, NumPy, ONNX Runtime | Visión, cálculo, inferencia | No (dependencias) | Licencias respectivas |
| MuJoCo | Simulación y chequeo de emotes | No (dependencia) | Apache 2.0 |
| HRIEGO (gestos co-verbales precomputados, catálogo de emotes) | Clips SONIC y catálogo | Sí (`robot/assets/hri/HRIEGO_gesture/`) | Proyecto del equipo; procedencia en `robot/assets/hri/PROVENANCE.md` |
| Flutter | App | No (SDK) | BSD-3-Clause |

Los modelos grandes no van en git: `robot/deploy/x2_deploy.sh modelos` los copia al robot desde el PC y
`robot/docs/reto-2/DEPLOY_SABANAHACK4.md` explica de dónde salen.
