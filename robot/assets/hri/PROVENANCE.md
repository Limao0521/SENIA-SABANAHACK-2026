# Procedencia de los datos HRI del reto 2

Todo lo versionado aquí fue **generado por nuestra propia tubería** (HRIEGO_gesture, rama
`Facial,-LLM-and-Gesture-integration` del repo `x2_ultra`) a partir del contrato de gestos del
proyecto; no contiene datos biométricos ni fotos. Los CSV de animación del MC de AgiBot
(licencia del fabricante) **no** se versionan: se copian con `scripts/fetch_hri_assets.sh`.

## Contenido versionado (espejo parcial de la raíz HRIEGO)

| Ruta local (`assets/hri/HRIEGO_gesture/…`) | Fuente original | Generación |
|---|---|---|
| `contract/catalog.json` | `x2_ultra/HRIEGO_gesture/contract/catalog.json` | Catálogo de emotes validado: emote → (motion_id, area) del MC o señalamiento externo. |
| `config/preset_latency_c08.json` | `x2_ultra/HRIEGO_gesture/config/preset_latency_c08.json` | Latencias MC medidas en el gemelo (corrida c08, Laplace RCP-01 `20261021_rcp01c08_preset_latency_twin`): lead_onset/lead_stroke/duración por binding. Respaldo de `stroke_offset`. |
| `twin/replay/presets_c08/preset_csv_timeline.json` | `x2_ultra/HRIEGO_gesture/twin/replay/presets_c08/preset_csv_timeline.json` | Cronología cinemática de cada CSV (onset, `stroke_s` al 90 % del desplazamiento máx., articulación dominante, `rest_q`). Detector oficial c08/c12. |
| `twin/point_targets.json` | `x2_ultra/HRIEGO_gesture/twin/point_targets.json` | Objetivos articulatorios del overlay de señalamiento (LEFT/RIGHT/FRONT), validados en `twin/stream_point.py`. |
| `visits_v2/<rank>/` (vip, regular, guest, staff, external) | `x2_ultra/HRIEGO_gesture/visits_v2/<rank>/` | Visita completa por rango: LLM (Qwen3-4B Q4, json_schema) → Supertonic TTS (WAV 44,1 kHz) → StreamTalk+GMR+SONIC (brazos 14 + waist/head 50 Hz) → anclas CTC QuartzNet (`anchor_plan/`). |
| `opt/realnames/visits/<persona>/` (laura, luis, mario, sebastian, josaed, desconocido) | `x2_ultra/HRIEGO_gesture/opt/personas/visits/<persona>/` (TASK W3, 2026-10-23; reemplaza los paquetes `opt/realnames` de TASK G2) | Ídem tubería G2 (contrato BF streaming + Supertonic + CTC + SONIC) **regenerada con personas por rango**: tarjetas de persona few-shot en el prompt (VIP mayordomo «usted»+título, REGULAR anfitrión cálido, GUEST concierge explicativo, STAFF colega «tú»+«¡dame esos cinco!», EXTERNAL mostrador breve), validador de address-form/longitud/título con reintento, y prosodia TTS por rango (VIP voz M5 F0≈88 Hz speed 0.92×estado, GUEST 0.97, REGULAR/EXTERNAL 1.0, STAFF 1.05; M1 en el resto). Los `wav_path` internos se reescribieron `opt/personas/`→`opt/realnames/`; los turn_id usan prefijo `ps_`. Código y métricas: `HRIEGO_gesture/opt/personas/REPORT.md`. |

## Recortes respecto a la fuente (para caber en ~36 MB)

De cada paquete de visita se versiona solo lo que el runtime lee:

- `visit_log.json`, `tts_manifest.jsonl` (manifiestos);
- `wav/…/*.wav` **excepto** las copias `*_fsprobe.wav` (sondas de fs, no se usan);
- `anchor_plan/` completo (JSON pequeños de anclas CTC y planes de dispatch);
- `sonic/<turn>/<turn>_arms.npz` (solo el npz de brazos; se omiten smplx/, traces/, x2/, logs/
  intermedios de la tubería, regenerables).

Integridad: `assets/hri/HRIEGO_gesture.sha256` lista el sha256 de los 368 archivos versionados
(verificar con `cd assets/hri/HRIEGO_gesture && sha256sum -c ../HRIEGO_gesture.sha256`).
Cada `visit_log.json` trae además el `wav_sha256` de cada turno.

## Assets NO versionados

- `assets/hri/mc_classic/` — `animation_player.yaml` + 12 CSV de animación del MC de AgiBot
  (`lx2501_3_t2d5/classic`), copiados y verificados por `scripts/fetch_hri_assets.sh` contra
  `assets/hri/mc_classic.sha256`. Fuente: `$X2_SIM_ROOT` (default `~/robots/x2_simulation`).
- Fotos de las personas y galería/DB de identidades (datos personales): nunca se commitean.
  La demo funciona sin ellas gracias a `assets/hri/identities.json` (resultado persona→rango).
- `Face/` en vivo: módulo opcional (ver `docs/reto-2/README_HRI.md`).

## Regeneración

Los paquetes de visita se regeneran con la tubería HRIEGO_gesture (rama
`Facial,-LLM-and-Gesture-integration`): contrato LLM → TTS Supertonic → GMR/SONIC →
`align/anchor_plan.py` (anclas CTC). Ver `docs/reto-2/README_HRI.md` §2.5.
