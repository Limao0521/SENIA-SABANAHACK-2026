# TASK L2 — Reducir reacciones de piernas sobre código W1 (2026-10-23, solo out_diag/legs2/, sin tocar src)

Método: replay determinista (mismo harness que out_diag/legs/replay_attr.py y W1) sobre el engine
ACTUAL (import read-only; monkeypatch de `rig._wf.bounds` y escalado de brazos en memoria).
3 turnos SONIC parados: ps_luis_t02_dialogue, ps_laura_t03_dialogue, ps_sebastian_t04_dialogue
(assets renombrados rn_→ps_; ver PENDIENTES). 7 condiciones × 3 turnos = 21 replays, ~0,5 s c/u.

Condiciones: w1 = código W1 as-is (bounds .18/.08/.05) · b10 = bounds yaw .10, pitch .05, roll .03 ·
b08 = yaw .08, pitch .05, roll .03 · k07/k06 = brazos cmd = REST + k·(cmd−REST), k=0.7/0.6 ·
c_b10k07, c_b08k06 = combinaciones.

## Resultados (legs qvel RMS parado / tilt max ° / drift xy max m / corr waist mín vs SONIC LP1.5 / arm RMS disp vs w1 / peak arm speed rad/s / % ticks arm speed >0.35)
| cond | luis t02 | laura t03 | seb t04 |
|---|---|---|---|
| w1 (W1 actual) | 0.185 / 3.75 / 0.103 / 0.90 / 100% / 2.25 / 76% | 0.270 / 3.49 / 0.158 / 0.81 / 100% / 2.25 / 85% | 0.165 / 3.16 / 0.075 / 0.95 / 100% / 2.44 / 88% |
| b10 | 0.040 / 2.65 / 0.047 / 0.89 / 100% / 2.25 / 75% | 0.153 / 2.83 / 0.064 / 0.86 / 100% / 2.25 / 80% | 0.147 / 2.33 / 0.052 / 0.91 / 100% / 2.44 / 87% |
| b08 | 0.039 / 2.61 / 0.047 / 0.89 / 100% / 2.25 / 75% | 0.153 / 2.83 / 0.064 / 0.86 / 100% / 2.25 / 80% | 0.144 / 2.34 / 0.051 / 0.90 / 100% / 2.44 / 87% |
| k07 | 0.167 / 3.69 / 0.076 / 0.91 / 70% / 2.05 / 55% | 0.208 / 3.76 / 0.117 / 0.89 / 70% / 2.05 / 66% | 0.070 / 3.06 / 0.040 / 0.96 / 70% / 2.05 / 70% |
| k06 | 0.153 / 3.65 / 0.073 / 0.93 / 60% / 1.98 / 39% | 0.145 / 3.69 / 0.080 / 0.91 / 60% / 1.98 / 50% | 0.062 / 3.07 / 0.038 / 0.96 / 60% / 1.98 / 57% |
| **c_b10k07** | **0.030 / 2.54 / 0.037 / 0.89 / 70% / 2.05 / 53%** | **0.043 / 2.51 / 0.035 / 0.91 / 70% / 2.05 / 62%** | **0.052 / 2.46 / 0.036 / 0.91 / 70% / 2.05 / 70%** |
| c_b08k06 | 0.027 / 2.47 / 0.034 / 0.89 / 60% / 1.98 / 37% | 0.036 / 2.48 / 0.033 / 0.91 / 60% / 1.98 / 47% | 0.047 / 2.37 / 0.034 / 0.90 / 60% / 1.98 / 58% |

## Lecturas
- Bounds bajos y escalado de brazos atacan causas DISTINTAS y componen bien: b10 solo aplasta a luis
  (0.185→0.040) pero no a laura/seb; k06 solo aplasta a seb pero no a luis/laura; el combo los cubre todos.
- b08 ≈ b10 (sin ganancia extra): el yaw ya está mayormente recortado en 0.10; no vale la pena bajar más.
- k07/k06 solos NO bajan tilt (3.1–3.8°): el tilt lo domina el waist, consistente con D1.
- waist corr se MANTIENE ≥0.86 en todas (mejora incluso: al recortar, el filtro sigue mejor la forma LP).
- Spill a hombros/cabeza (W1) no ensucia los brazos: arm RMS y peak speed idénticos entre w1 y b10/b08.
- % ticks arm >0.35 rad/s (sim2real): w1 76–88% → k07 55–70% → c_b10k07 53–70%; c_b08k06 37–58%.

## Recomendación: c_b10k07 (bounds yaw ±0.10 / pitch ±0.05 / roll ±0.03 + brazos k=0.7 sobre REST)
- legs qvel RMS 0.030–0.052 → **−82…−90% vs pre-W1 0.297** (objetivo −60% ✓ holgado) y −68…−84% vs W1 actual.
- tilt max ≤2.54° (objetivo ≤3° ✓), drift max ≤0.037 m (−64…−78% vs w1), waist corr ≥0.89 ✓.
- Gestos visibles: arm RMS displacement = 70% del original (objetivo ≥60% ✓); peak arm speed 2.05 rad/s.
- c_b08k06 es ligeramente mejor en piernas pero arm RMS 59.7–59.8% queda apenas BAJO el objetivo 60%
  → se descarta por expresividad; k=0.7 es el punto dulce.
- Implementación sugerida (fuera de este task): WAIST_BOUNDS=[0.10,0.05,0.03] en engine.py y un
  `ARM_AMP_K=0.7` aplicado en el player como cmd = REST + k·(cmd−REST) solo en turnos parados.

## PENDIENTES / riesgos
- Assets renombrados rn_→ps_ (git status en curso por V): w1 de luis t02 da 0.185 aquí vs 0.259 en
  W1_REPORT → los datos/turno pueden haber cambiado; comparaciones válidas DENTRO de este experimento.
- Solo turnos dialogue parados; no se probó venia/presets (usan WAIST_BOUNDS_PRESET, otro camino) ni marcha.
- k=0.7 global: turnos POINT/guidance podrían querer amplitud plena (evaluar por tipo de turno).
- % ticks arm >0.35 rad/s sigue alto (53–70%) incluso con k=0.7; si el límite sim2real es duro,
  hace falta rate-limit de brazos adicional (D1 mostró que suavizar targets no reduce piernas, pero
  sí bajaría la velocidad pico).
