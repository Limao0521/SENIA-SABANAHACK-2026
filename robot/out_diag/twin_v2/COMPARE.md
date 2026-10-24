# twin_v (before, 78aa645 parent) vs twin_v2 (after)

Metrics recomputed with one uniform method from trace.npz for both runs (clipped = limiter counter delta per phase; ticks = up_t samples; fall = tilt>35deg or pelvis<0.45). 'clip_orig' is the original twin_v/metrics.json value.

| phase | clip% orig | clip% before | clip% after | err>0.12 before | after | vmax before | after | tilt_max b/a | pelvis_min b/a | falls b/a |
|---|---|---|---|---|---|---|---|---|---|---|
| S2_sebastian_t02 | 7.59 | 8.03 | 8.03 | 9.03 | 8.36 | 4.73 | 4.73 | 5.54/1.72 | 0.6928/0.6837 | 0/0 |
| S2_laura_t02 | 5.85 | 6.13 | 5.85 | 5.6 | 5.05 | 4.94 | 4.94 | 5.34/1.56 | 0.6928/0.6839 | 0/0 |
| S2_josaed_t03 | 5.25 | 5.52 | 6.15 | 6.44 | 7.38 | 3.51 | 3.51 | 5.38/1.69 | 0.6928/0.6838 | 0/0 |
| S3_point_RIGHT | 11.2 | 10.34 | 10.73 | 25.0 | 21.03 | 3.51 | 3.51 | 5.27/1.69 | 0.6914/0.6838 | 0/0 |
| S3_point_LEFT | 14.4 | 13.73 | 13.36 | 28.76 | 31.03 | 5.6 | 4.9 | 5.37/1.59 | 0.6925/0.6839 | 0/0 |
| S3_point_FRONT | 16.4 | 15.95 | 16.81 | 41.81 | 43.97 | 6.3 | 5.79 | 5.1/1.39 | 0.6915/0.6838 | 0/0 |
| S4_emote_3001_VIP_BOW | 6.46 | 0.0 | 0.0 | 15.85 | 15.14 | 2.54 | 2.54 | 5.71/1.52 | 0.6926/0.6838 | 0/0 |
| S4_sonic_after_3001 | 38.03 | 32.8 | 37.6 | 16.8 | 17.6 | 4.8 | 4.8 | 5.41/1.49 | 0.6929/0.6839 | 0/0 |
| S4_emote_3004_SOCIAL_HEART | 5.28 | 0.0 | 0.0 | 34.39 | 33.91 | 2.65 | 2.65 | 5.47/1.71 | 0.6893/0.6833 | 0/0 |
| S4_sonic_after_3004 | 48.25 | 45.6 | 40.8 | 22.4 | 25.6 | 4.94 | 4.94 | 5.3/1.7 | 0.6927/0.6837 | 0/0 |
| S4_emote_3017_APPLAUSE | 3.31 | 0.0 | 0.0 | 0.0 | 1.43 | 1.97 | 2.15 | 5.05/1.5 | 0.6925/0.684 | 0/0 |
| S4_sonic_after_3017 | 35.0 | 28.0 | 31.2 | 8.8 | 5.6 | 4.05 | 3.2 | 5.12/1.33 | 0.6929/0.684 | 0/0 |
| S5_settle | 0.0 | 0.0 | 0.0 | 0.0 | 10.45 | 0.0 | 0.0 | 4.99/2.0 | 0.693/0.6836 | 0/0 |
| S5_point_after_walk | 0.0 | 0.0 | 0.0 | 28.83 | 27.48 | 3.67 | 3.67 | 4.93/1.6 | 0.6912/0.6839 | 0/0 |

unsafe after: False, estopped: False, limiter_nlim total before/after: 373/388
final state after: {'t': '5337.318776377', 'pelvis_z': '0.6839826671437549', 'tilt_deg': '0.19633487434125996', 'mode': 'STAND_DEFAULT', 'stable': 'True', 'walking': 'False', 'fall': 'False'}
refusals after: [{'event': 'refused', 't': 5327.309461653, 'what': 'point', 'reason': 'walking'}, {'event': 'refused', 't': 5327.309477143, 'what': 'stream_upper_body', 'reason': 'walking'}]
head events logged: 0 []