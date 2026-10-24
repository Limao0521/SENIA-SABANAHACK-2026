# Registros de las sesiones en el X2 real (20/10/2026)

Copias comprimidas (`gzip`) de `logs/real/`, que no se versiona. Los nombres de los `.jsonl` van en hora UTC (la del
contenedor). Cada `.jsonl` tiene una línea por evento (`inicio`,
`armado`, `desarmado` con motivo, `modo`, `nivel`, `offroad`, `cuclilla`, `salida`) y una `muestra` cada 0,1 s (orden
mandada, modo y fuente del MC, `body_status`, batería; en `brazos`, objetivo y medido de los 14 ejes). Leer con
`zcat archivo.jsonl.gz | python3 -c "import json,sys; [print(json.loads(l)) for l in sys.stdin]"`.
Contexto y conclusiones: [docs/09](../../09-robot-real.md), secciones 9 y 10.

| Archivo | Sesión | Qué pasó |
|---|---|---|
| `andar-20261020-163412` | — | 20 s sin armar (comprobación previa) |
| `andar-20261020-163548` | `andar` 1 | W/S funcionan; lateral 0,25–0,375 m/s y giro 0,3–0,45 rad/s no dan pasos |
| `andar-20261020-164250` | `andar` 2 | lateral 0,5 m/s y giro 0,6 rad/s funcionan; dos desarmes porque el MC arrancó el gesto `1011` (la protección del reproductor de movimientos actuó) |
| `andar-20261020-171453` | `andar` 3 | niveles de velocidad y offroad: el MC anda en `LOCOMOTION_STEP` |
| `body_pose_app-20261020-121350.log` | — | grabación de solo lectura de `/aima/mc/body_pose` y `servo_status` mientras se agachaba con la app; de aquí sale que `stand_height` es una velocidad. Su nombre va en hora local del PC (UTC−5): 12:13 = 17:13 UTC, entre las sesiones 3 y 4 |
| `andar-20261020-172400`, `-172507` | `andar` 4 | cuclillas: desarmes por `body_status` 4 y 0 (antes de la corrección `2630db6`); Esc levanta el robot |
| `andar-20261020-172747` | `andar` 5 | cuclilla completa 0 → −0,50 → 0 sin desarmarse |
| `brazos-20261020-174005` | `brazos` 1 | URS, los dos brazos articulación a articulación hasta 0,4 rad, error medio 0,064 y máximo 0,10 rad |
