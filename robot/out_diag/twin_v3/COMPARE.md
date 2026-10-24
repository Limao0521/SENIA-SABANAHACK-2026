# twin_v2 (before/after) vs twin_v3 (backend HEAD 4918f13)

Method as twin_v2/COMPARE.md: clipped% = limiter counter delta / up_t ticks per phase; fall = tilt>35deg or pelvis<0.45; err>0.12 = % ticks with max_j|cmd(k-1)-meas(k)|>0.12 (reverse-engineered; reproduces twin_v2 metrics.json within +-1 tick). 'before' = twin_v trace recomputed.

| phase | clip% v2-before | v2-after | v3 | err>0.12 v2-before | v2-after | v3 | tilt_max b/a/v3 | pelvis_min b/a/v3 | falls b/a/v3 |
|---|---|---|---|---|---|---|---|---|---|
| S2_sebastian_t02 | 8.03 | 8.03 | 4.01 | 9.36 | 8.7 | 5.69 | 5.54/1.72/1.47 | 0.6928/0.6837/0.6839 | 0/0/0 |
| S2_laura_t02 | 6.13 | 5.85 | 0.0 | 5.33 | 4.79 | 2.13 | 5.34/1.56/1.54 | 0.6928/0.6839/0.6839 | 0/0/0 |
| S2_josaed_t03 | 5.52 | 6.15 | 0.31 | 6.44 | 7.38 | 4.62 | 5.38/1.69/1.54 | 0.6928/0.6838/0.6838 | 0/0/0 |
| S3_point_RIGHT | 10.34 | 10.73 | 4.29 | 25.0 | 21.03 | 20.17 | 5.27/1.69/1.57 | 0.6914/0.6838/0.6839 | 0/0/0 |
| S3_point_LEFT | 13.73 | 13.36 | 6.47 | 28.76 | 31.03 | 26.72 | 5.37/1.59/1.69 | 0.6925/0.6839/0.6838 | 0/0/0 |
| S3_point_FRONT | 15.95 | 16.81 | 6.9 | 41.38 | 43.53 | 37.07 | 5.1/1.39/1.37 | 0.6915/0.6838/0.6839 | 0/0/0 |
| S4_emote_3001_VIP_BOW | 0.0 | 0.0 | 0.0 | 15.85 | 15.14 | 19.92 | 5.71/1.52/1.5 | 0.6926/0.6838/0.6839 | 0/0/0 |
| S4_sonic_after_3001 | 32.8 | 37.6 | 1.6 | 16.8 | 17.6 | 4.8 | 5.41/1.49/1.38 | 0.6929/0.6839/0.684 | 0/0/0 |
| S4_emote_3004_SOCIAL_HEART | 0.0 | 0.0 | 0.0 | 34.74 | 33.91 | 35.64 | 5.47/1.71/1.75 | 0.6893/0.6833/0.6834 | 0/0/0 |
| S4_sonic_after_3004 | 45.6 | 40.8 | 15.2 | 22.4 | 25.6 | 10.4 | 5.3/1.7/1.52 | 0.6927/0.6837/0.6839 | 0/0/0 |
| S4_emote_3017_APPLAUSE | 0.0 | 0.0 | 0.0 | 0.0 | 1.43 | 0.0 | 5.05/1.5/1.23 | 0.6925/0.684/0.6841 | 0/0/0 |
| S4_sonic_after_3017 | 28.0 | 31.2 | 2.4 | 8.8 | 5.6 | 0.0 | 5.12/1.33/1.41 | 0.6929/0.684/0.684 | 0/0/0 |
| S5_settle | 0.0 | 0.0 | 20.9 | 0.0 | 11.94 | 41.79 | 4.99/2.0/5.33 | 0.693/0.6836/0.6826 | 0/0/0 |
| S5_point_after_walk | 0.0 | 0.0 | 0.0 | 28.83 | 27.48 | 32.88 | 4.93/1.6/0.99 | 0.6912/0.6839/0.6836 | 0/0/0 |

limiter_nlim total: before/after/v3 = 373/388/97; unsafe=False estopped=False
final state v3: {'t': '6143.587895789', 'pelvis_z': '0.6837852471476451', 'tilt_deg': '0.869203881234367', 'mode': 'STAND_DEFAULT', 'stable': 'True', 'walking': 'False', 'fall': 'False'}
Note: first two runs aborted at arm_session ('MC en ?'); v3 run used a driver copy patched in-container only (4s wait + observed(graph=True) poll before arm). Repo untouched.
