# Waist modes on twin

NOTE: trace.npz holds only waist COMMAND (bp), arms, pelvis/tilt; no measured waist/leg joints, so PSD>3Hz of measured waist velocity and leg vel RMS are NOT computable. Command jerk used instead.

| metric | baseline_v4 | slew | lowpass | off |
|---|---|---|---|---|
| fall ticks | 0 | 0 | 0 | 0 |
| unsafe | False | False | False | False |
| tilt max deg | 1.84 | 5.97 | 0.99 | 6.14 |
| tilt rms | 1.09 | 1.21 | 0.47 | 0.83 |
| pelvis min | 0.683 | 0.676 | 0.683 | 0.677 |
| waist cmd jerk rms /axis | 355,486,391,198 | 344,430,329,195 | 322,468,160,189 | 172,455,152,200 |
| waist cmd vel max /axis | 15,15,15,15 | 15,30,30,15 | 15,15,15,15 | 15,30,15,15 |
| waist cmd range /axis | 0.6,0.6,0.6,0.6 | 0.6,0.6,0.6,0.6 | 0.6,0.6,0.6,0.6 | 0.6,0.6,0.6,0.6 |
| arm clip% S2_sebastian_t02 | 3.34 | 4.68 | 4.01 | 0.0 |
| arm clip% S2_laura_t02 | 0.0 | 0.0 | 0.0 | 0.0 |
| arm clip% S2_josaed_t03 | 0.31 | 0.31 | 0.31 | 0.31 |
| arm clip% S3_point_RIGHT | 3.88 | 3.86 | 3.88 | 3.88 |
| arm clip% S3_point_LEFT | 6.03 | 6.47 | 6.03 | 6.47 |
| arm clip% S3_point_FRONT | 6.47 | 6.9 | 6.47 | 6.44 |
| arm clip% S4_emote_3001_VIP_BOW | 0.4 | 0.4 | 0.4 | 0.0 |
| arm clip% S4_sonic_after_3001 | 1.59 | 0.8 | 0.8 | 1.6 |
| arm clip% S4_emote_3004_SOCIAL_HEART | 0.0 | 0.0 | 0.0 | 0.0 |
| arm clip% S4_sonic_after_3004 | 16.0 | 15.2 | 16.0 | 16.8 |
| arm clip% S4_emote_3017_APPLAUSE | 0.0 | 0.0 | 0.0 | 0.0 |
| arm clip% S4_sonic_after_3017 | 1.59 | 1.6 | 3.17 | 6.4 |
| arm clip% S5_walk | 0.0 | 0.0 | 0.0 | 0.0 |
| arm clip% S5_settle | 0.0 | 0.0 | 0.0 | 0.0 |
| arm clip% S5_point_after_walk | 0.0 | 0.0 | 0.0 | 0.0 |
