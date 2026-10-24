# TASK D2 — Por qué la venia (NM_bow.csv, GREETING_VIP 3001/11) desplaza al X2 HACIA ATRÁS

Fecha: 2026-10-23 · Rama `claude/reto2-hri` · Sin commits ni ediciones a archivos trackeados.
Repro: `out_diag/bow/diag_bow.py <fix> <csv>` — replay headless del turno GREETING_VIP de Laura
(`use_person_visits("Laura")` + `plan_turn` + `HriPlayer` sobre `ShowEngine(CLUB)`), logging 50 Hz:
COM del robot (`data.subtree_com[root]`), borde posterior del polígono de soporte (contactos de los
geoms de `*_ankle_roll_link`), x/vx de la base, waist_pitch ctrl vs q vs delta, caderas/rodillas/
tobillos, acciones de la policy, tilt. Ventana del gesto (preset CSV): t = 2,86 → 6,26 s.

## 1. Cuantificación del drift (baseline, tal cual está commiteado)

| Métrica | Valor |
|---|---|
| Drift total x de la base (26 s de sim) | **−0,570 m** (x0 4,391 → 3,822; el robot mira a +x, luego es HACIA ATRÁS) |
| Drift durante la ventana del gesto | −0,539 m (95 % del total) |
| Fase *down* (rampa waist_delta 0→0,15, t 2,86–3,20) | −0,010 m (casi nada) |
| Fase *hold* (waist_delta ≈ 0,15 sostenido + decaimiento, t 3,20–6,3) | **−0,529 m** (vx mín −0,27 m/s) |
| waist_pitch: pedido CSV / aplicado / medido | +0,60 rad (fuera de límite URDF ±0,3142) / 0,15 (clip `WAIST_LIMIT`) / máx 0,214 (delta + acción policy) |
| tilt máx | 5,2° · sin caída · COM al borde posterior del soporte: ver tabla §3 |

El CSV: waist_pitch sube a 0,57 en 0,8 s, mantiene 0,60 durante ~1,0 s y baja en ~0,6 s; los brazos
van HACIA ATRÁS (shoulder_pitch 0,4 → −0,32, elbow −1,2 → −1,9) y la cabeza se inclina (head_bow).

## 2. Mecanismo (con evidencia de los logs)

1. `player.tick` → `rig.set_waist_delta(clip(w_csv, ±0,15))` y `engine.control_tick` hace
   `d.ctrl[act_waist] += waist_delta` **solo con el robot parado** (|cmd|<0,05): es un *offset
   exógeno y rápido* (0→0,15 rad en ~0,36 s; sin límite de tasa en reto 2, a diferencia del
   `kinematic_clip` de reto 1 que lo limitaba a 0,25 rad/s) sobre el objetivo de cintura de la policy.
2. El torso se inclina adelante → la proyección del COM se desplaza hacia los dedos. La policy
   "brazos_libres" (observa todos los joints; controla piernas+cintura; cmd de velocidad = 0) lo
   interpreta como una perturbación sostenida que empuja el COM adelante.
3. Su estrategia de equilibrio con cmd=0 es recentrar el COM **llevando la base/pelvis hacia atrás**
   (extensión de cadera + pasos de retirada): mientras el offset se mantiene (~3 s de plateau+fade),
   el robot "camina" hacia atrás hasta −0,27 m/s. El drift es de HOLD, no del transitorio inicial.
4. El tobillo no basta: el margen COM→borde posterior se consume (0,01–0,08 m medidos) y la
   estrategia de tobillo satura; la policy pasa a estrategia de paso hacia atrás.
5. Los brazos del CSV que "van atrás" en realidad **ayudan**: compensan parcialmente el COM del
   torso adelantado. Al forzar brazos al frente (fix b) el drift EMPEORA (−0,68 m). No es el swing
   de brazos la causa; es el offset de cintura sostenido.

## 3. Fixes probados (cada uno = replay completo; mismos seeds/escena)

| Fix | Implementación (monkeypatch en diag_bow.py) | Drift total (m) | Drift en gesto (m) | vx mín (m/s) | Margen COM→talón mín (m) | tilt máx (°) | waist_pitch q máx (rad) = profundidad visual |
|---|---|---|---|---|---|---|---|
| baseline | — | **−0,570** | −0,539 | −0,266 | **0,008** (¡8 mm al talón!) | 5,2 | 0,214 |
| (a) rampa lenta + menor | cap 0,10 rad + slew 0,06 rad/s (min-jerk-like) | **+0,00002** | −0,046 | −0,033 | 0,053 | 3,1 | 0,131 |
| (b) brazos al frente | brazos D (sin swing atrás), waist igual | −0,681 (PEOR) | −0,642 | −0,315 | 0,011 | 4,6 | 0,221 |
| (c)≈(a+b) combo | cap 0,10 + slew lento + brazos D | −0,176 | −0,154 | −0,145 | 0,038 | 3,0 | 0,140 |
| (e) sin waist | solo cabeza+brazos del CSV, waist_delta ≡ 0 | +0,002 | +0,013 | −0,019 | 0,083 | 0,7 | −0,019 (sin venia de torso) |
| (d) hold base | NO testeado: cmd ya es 0 (interlock); el drift es reacción de equilibrio de la policy, no velocidad comandada — un "stance hold" requeriría congelar los pies (no expuesto por la policy) | — | — | — | — | — | — |

## 4. Recomendación

**Fix (a): limitar el offset de waist_pitch a 0,10 rad con rampa lenta (~0,06 rad/s, perfil
min-jerk) — drift ≈ 0 (24 µm), margen COM 0,053 m, tilt 3,1°, y la venia sigue viéndose**
(torso 0,13 rad ≈ 7,5° + head_bow del CSV + brazos atrás del CSV; idéntico al gesto validado en
reto 1 con waist_limit 0,15 + rate 0,25 rad/s). Implementación sugerida (2 líneas de espíritu):
en `RobotRig.set_waist_delta` guardar el *target* y hacer slew-limiting en `control_tick`
(como el `HEAD_RATE`/`ARM_RATE` existentes), con `waist_delta_lim[1] = 0,10`. Alternativamente,
suavizar en `player._stream` la señal de waist del preset con un min-jerk de ~1,5–2 s.
- NO usar (b) ni (c): quitar el swing de brazos del CSV empeora el drift (los brazos atrás
  compensan el torso adelante).
- (e) es la opción más estable (drift 2 mm) pero pierde la venia de torso; queda como
  fallback si se exige margen máximo.
- Riesgo: no se testeó marcha inmediatamente después de la venia con cap 0,10+slew (reto 1
  documentó inestabilidad al andar atrás tras venia con límites mayores; con drift≈0 debería
  mejorar). Pendiente: validar (a) en el show completo (`--person Laura`) y con venias encadenadas.

## 5. Archivos generados (todo en out_diag/bow/, nada trackeado)
diag_bow.py · baseline.csv(+_summary.json, corrida 2 con soporte) · a_slow_waist.csv · b_arms_fwd.csv ·
ab_combo.csv · e_no_waist.csv · *.out (stdout con resumen JSON)
