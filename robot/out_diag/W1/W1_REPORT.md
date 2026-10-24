# TASK W1 — Canal de waist filtrado + venia estable (2026-10-23, rama claude/reto2-hri, sin commitear)

## Implementación (src/x2_ultra/show/engine.py, src/x2_ultra/show/hri/player.py)
1. `WaistFilter` (clase pura, testeable sin MuJoCo) en el canal `set_waist_delta → control_tick`:
   bounds por eje (yaw ±0,18 / pitch ±0,08 / roll ±0,05 rad) → low-pass críticamente amortiguado
   de 2º orden (fc = 1,5 Hz) → slew por eje (0,4 / 0,2 / 0,2 rad/s) → ganancia g. Constantes
   `WAIST_*` configurables a nivel de módulo. `rig.waist_delta` = delta APLICADO (logger/diag intactos).
2. Ganancia `g(t)` = gate × equilibrio: `set_waist_gate(on/off)` con rampa minimum-jerk 0→1 / 1→0
   en 0,4 s (player: abre en `_enter` de cada turno, cierra al fin de secuencia, en supresión por
   caminata y en `abort_to_rest` — cubre player y tour vía `HriTurns.stop`). Alrededor de presets la
   señal ya trae los fades min-jerk V2 (0,6/0,7 s) del player + el slew gentle.
3. Balance-aware: factor 1→0 lineal con tilt 3→6° y con margen COM→polígono de soporte <30 mm
   (contactos de los geoms de `*_ankle_roll_link`, mismo cálculo que diag_bow.py; solo se evalúa
   parado y con delta activo).
4. Reparto del yaw recortado: exceso → ambos `shoulder_yaw` (±0,10 rad, vía `clamp_arms`+ARM_RATE)
   y `head_yaw` (±0,15, solo Ultra, vía HEAD_RATE), con slew propio 0,5 rad/s y gate.
5. Venia (preset cinemático VIP 3001): `set_waist_gentle(True)` durante la ventana del CSV
   (`csv_start−FADE_OUT_S … csv_end+FADE_IN_S`) → cap pitch 0,10 + slew 0,06 rad/s (= D2 fix a).
   Cabeza y brazos del CSV intactos. Marcha intacta: mismo interlock `|cmd|<0,05`, nunca gestos andando.

## Validación — D1: replay atribución rn_luis_t02_dialogue (out_diag/W1/replay_attr_w1.py)
| métrica (cond a, SONIC as-is) | antes (out_diag/legs) | después | Δ |
|---|---|---|---|
| legs qvel RMS | 0,297 | 0,259 | −13 % |
| tilt mean / max ° | 2,07 / 4,23 | 1,75 / 3,29 | −15 % / −22 % |
| xy drift fin (m) | 0,199 | 0,109 | −45 % |
| pelvis-z std (mm) | 7,4 | 6,4 | −14 % |
| corr(waist aplicado, SONIC LP 1,5 Hz) yaw/pitch/roll | — | 0,91 / 0,95 / 0,90 | ≥0,8 ✓ |
| d_no_waist (sanidad: camino sin waist idéntico) | 0,0256 | 0,0256 | = ✓ |
| f_arms_rest (waist filtrado + brazos reposo) | 0,129 | 0,034 | −74 % |

El residuo de qvel es la parte de brazos (D1: ~57 % momentum, no filtrable). angvel_xy_p95 sube
0,26→0,61 rad/s (correcciones más rápidas de la policy al recortar el gain; tilt y drift bajan igual).

## Validación — D2: venia de Laura (diag_bow.py baseline = código nuevo, out_diag/W1/bow_new*.csv/json)
| métrica | baseline commiteado | fix (a) monkeypatch | código nuevo |
|---|---|---|---|
| drift total x (m) | −0,570 | +0,00002 | **+0,002** ✓ (<2 cm) |
| drift en ventana gesto (m) | −0,539 | −0,046 | −0,050 |
| vx mín (m/s) | −0,266 | −0,033 | −0,034 |
| margen COM→talón mín (m) | 0,008 | 0,053 | **0,053** ✓ (≥0,040) |
| tilt máx ° | 5,2 | 3,1 | 3,0 |
| waist_pitch q máx (rad) | 0,214 | 0,131 | 0,128 (venia visible) |

Los monkeypatches de diag_bow.py (a/b/e) ya no aplican sobre el camino nuevo (asignan rig.waist_delta
directo, que ahora recalcula el filtro); se validó con `baseline` sin parchear = código real.

## Validación — show completo: 6 personas headless sin vídeo (python -m x2_ultra.show.hri.cli --all)
| persona | caídas | gestos andando | stroke máx (ms) |
|---|---|---|---|
| Luis / Josaed / Sebastian / Mario / Laura / Desconocido | 0 / 0 / 0 / 0 / 0 / 0 | 0 ×6 | 40 / 100 / 20 / 40 / 40 / 40 ✓ (≤100) |

## Tests
tests/test_hri_reception.py: **21/21 pasan** (4 nuevos: bounds+slew+excess, gentle bow 0,10/0,06,
gain+reset, gate/gentle del player; FakeRig ampliado). tests/test_engine.py: 6 fallos PREEXISTENTES
(builder.py:313 `mjEQ_JOINT data size 12` — incompatibilidad de versión MuJoCo en manos, ajena a W1).

## Archivos
- Modificados: src/x2_ultra/show/engine.py · src/x2_ultra/show/hri/player.py · tests/test_hri_reception.py
- Nuevos (untracked): out_diag/W1/{replay_attr_w1.py, replay_rn_luis_t02_dialogue.json, bow_new.csv,
  bow_new_summary.json, run_all.out, W1_REPORT.md} · out_reto2/* regenerado (untracked)

## Pendientes / riesgos
- Josaed stroke 100 ms: en el límite del criterio (brazos sin tocar; mismo valor que antes de W1).
- angvel_xy_p95 al alza en el replay (ver arriba); sin efecto en tilt/drift/caídas.
- No se testeó marcha inmediata tras venia encadenada (riesgo ya señalado por D2, mitigado por drift≈0).
- test_engine.py preexistente roto por versión de MuJoCo (no W1).
