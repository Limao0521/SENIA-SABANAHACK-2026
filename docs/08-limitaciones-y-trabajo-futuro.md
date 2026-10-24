# 08 · Limitaciones y trabajo futuro

Lo que **no** hace esta entrega, dicho sin rodeos, y el siguiente paso para cada cosa.

## Robot

| Limitación | Por qué | Siguiente paso |
|---|---|---|
| **El robot no camina acompañando al VIP** | La oferta existe en la conversación, pero la marcha está deshabilitada por seguridad; la tarea de navegación hasta el ascensor guardada en el robot no está integrada | Disparar la tarea de navegación de AgiBot (`PncTaskRequest` en `/aima/te/pnc_task_request` o la tarea guardada) cuando el VIP acepta, con gestos bloqueados mientras camina |
| **Draco (perro guía D1) no se controla** | No tenemos su API; solo se menciona y se registra | Integrar el SDK del D1 cuando esté disponible |
| **Emotes de fábrica** | Las animaciones de AgiBot no se pueden distribuir ni estaban en el robot | Se usan 12 emotes propios verificados; con los CSV de AgiBot (`scripts/fetch_hri_assets.sh`) el backend usa los originales |
| **Salir del modo de brazos remotos (URS)** | El firmware rechaza el cambio de modo por SDK (`MotionBusy`) | Tras parar, poner Stand desde la app/mando de AgiBot |
| **Co-speech precomputado** | El generador de gestos SONIC en línea no corre en el Orin (dependencias x86, ~4 GB de modelos) | Clips SONIC precomputados (103) mientras tanto; portar SONIC a aarch64 |
| **Latencia ≈ 5–6 s** | Orin NX compartido con la percepción/SLAM de AgiBot (CPU y RAM saturadas) | Qwen3-4B-Instruct-2507 (0 reintentos en el banco del PC), TTS en GPU, VAD más corto; la nube está implementada y apagada |
| **Cabeceo de la cabeza** | `McBodyPose` solo expone giro; el canal HAL de cabeza no se probó a fondo | Probar `HRI_HEAD_CHANNEL=hal` |
| **Una persona a la vez** | Semidúplex sin barge-in; una visita activa | Turnos con interrupción y seguimiento de varias caras |

## App

| Limitación | Siguiente paso |
|---|---|
| Login con cuentas demo fijas, sin backend | Autenticación real (API del club) |
| Datos en memoria | Persistencia local o remota |
| QR estáticos por rol | Credencial firmada por persona y con caducidad (el validador del robot ya soporta QR01/QC1 Ed25519) |
| Validación de formularios mínima | Validación y manejo de errores de red/SMTP |

## Integración

- La app y el robot comparten la credencial y la matriz de permisos, pero **no hay sincronización en
  línea** (reservas, invitados registrados en la app que el robot reconozca, avisos a personal). Un
  backend común del club cerraría ese ciclo.
