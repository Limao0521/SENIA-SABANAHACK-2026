# Waist off vs lowpass (twin)

| metric | off | lowpass | old_off | old_lowpass |
|---|---|---|---|---|
| falls | 0 | 0 | 0 | 0 |
| tilt_max | 1.6444547119257626 | 5.819912656039146 | 6.137850512591792 | 0.9940364516894602 |
| tilt_rms | 1.0320289733569776 | 1.1878926748373946 | 0.8255251986915157 | 0.47042855868263794 |
| pelvis_min | 0.6835018324498823 | 0.6760512920988272 | 0.6767856005293333 | 0.6833046027918557 |
| cmd_nonzero_frac | 0.0 | 0.752454991816694 | 0.5544412607449857 | 0.4443074393752569 |
| cmd_signflips_per_tick | [0,0,0] | [0.0125,0.0908,0.0043] | [0.00614,0.0856,0.00716] | [0.0115,0.0767,0.00555] |
| meas_vel_max | [0.222,0.151,0.136] | [2.68,1.81,0.671] | n/a | n/a |
| meas_vel_psd_gt3hz | [0.078,0.129,0.0947] | [0.174,0.811,0.358] | n/a | n/a |
| meas_legs | 0 | 0 | 0 | 0 |
| armclip% S2_sebastian_t02 | 0.67 | 0.0 | 0.0 | 4.01 |
| armclip% S2_laura_t02 | 0.0 | 0.0 | 0.0 | 0.0 |
| armclip% S2_josaed_t03 | 0.31 | 0.31 | 0.31 | 0.31 |
| armclip% S3_point_RIGHT | 4.31 | 3.88 | 3.88 | 3.88 |
| armclip% S3_point_LEFT | 6.03 | 6.03 | 6.47 | 6.03 |
| armclip% S3_point_FRONT | 6.47 | 6.47 | 6.44 | 6.47 |
| armclip% S4_emote_3001_VIP_BOW | 0.0 | 0.0 | 0.0 | 0.4 |
| armclip% S4_sonic_after_3001 | 2.4 | 2.38 | 1.6 | 0.8 |
| armclip% S4_emote_3004_SOCIAL_HEART | 0.0 | 0.0 | 0.0 | 0.0 |
| armclip% S4_sonic_after_3004 | 16.0 | 15.2 | 16.8 | 16.0 |
| armclip% S4_emote_3017_APPLAUSE | 0.0 | 0.0 | 0.0 | 0.0 |
| armclip% S4_sonic_after_3017 | 6.4 | 2.4 | 6.4 | 3.17 |
| armclip% S5_walk | 0.0 | 0.0 | 0.0 | 0.0 |
| armclip% S5_settle | 0.0 | 0.0 | 0.0 | 0.0 |
| armclip% S5_point_after_walk | 0.0 | 0.0 | 0.0 | 0.0 |

old_* = previous twin_waist runs (no measured data). meas_legs empty (0 rows) -> leg vel RMS not computable.
