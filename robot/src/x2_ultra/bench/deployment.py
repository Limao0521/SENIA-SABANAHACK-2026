"""Análisis de viabilidad de despliegue A BORDO del X2 Ultra-N.

Responde a: si desplegamos el sistema para testeo en un robot real, ¿pueden todos los
elementos ejecutarse localmente (PC1/PC2/PC3) sin depender de un PC externo?

Cruza tres fuentes, y cada cifra lleva su procedencia:

1. **Medidas del stack HRI** en PC de desarrollo x86 + gemelo digital. NO medidas en el
   Orin NX — `HRIEGO_gesture/README.md` §9: «El Orin NX 16 GB no esta medido».
2. **Benchmark de este paquete** (``runs/bench_*/report.json``): coste del lazo de control
   emulado y su estimación x86→Orin NX (factores documentados en bench.power).
3. **Docs de despliegue**: `docs/reto-2/README_HRI.md` §d–§f, `docs/09-robot-real.md`,
   `docs/03-sdk-aimdk.md`, `llm/README.md`, `HRIEGO_gesture/opt/sim2real/REPORT.md`.

Uso: ``python -m x2_ultra.bench --deployment`` (opcionalmente tras una corrida, para
citar su report.json; si no, busca el runs/bench_*/report.json más reciente).
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Veredictos por elemento
# ---------------------------------------------------------------------------
# A_BORDO         corre/ejecuta en el robot hoy (vendor o ya probado físicamente)
# CABE_SIN_MEDIR  cabe por presupuesto en PC2, medido solo en x86; falta medir en Orin
# FALTA_PUENTE    cabe computacionalmente, pero falta el despliegue/puente a bordo
# NECESITA_PORT   falta build arm64/Jetson (y sus medidas)
# RETIMING        el dato/contenido existe en formato robot pero excede límites reales
# FUERA           se queda en el PC externo / equipo ajeno al robot (por diseño)

ELEMENTS: list[dict] = [
    {
        "key": "locomocion",
        "elemento": "Locomoción y equilibrio (MC, 50 Hz)",
        "ordenador": "PC1 · RK3588s (vendor; prohibido código propio)",
        "estado": "A bordo y probado en robot real: 5 sesiones de andar sin caídas (19-20/10/2026); "
                  "caps de marcha medidos en robot vy 0,5 m/s, wz 0,6 rad/s.",
        "requisito": "— (código del fabricante)",
        "evidencia": "docs/09-robot-real.md · src/x2_ultra/real/control.py · docs/metrics/robot_real_20261020/",
        "veredicto": "A_BORDO",
    },
    {
        "key": "brazos_50hz",
        "elemento": "Stream de brazos 50 Hz (UPPERBODY_REMOTE_SPLIT, /mc/upper_body_command)",
        "ordenador": "PC2 · Orin NX (código propio en contenedor)",
        "estado": "Cliente AimDK existe y está PROBADO en robot real (error medio 0,064 rad, máx 0,10, "
                  "con locomoción activa), pero operado desde PC externo: docker x86 + cable a 10.0.1.41. "
                  "SDK aarch64 descargable y overlay compilable para arm64 existen.",
        "requisito": "<1 núcleo (streaming + shaping; las lecturas del pack comercial son µs por ciclo).",
        "evidencia": "src/x2_ultra/real/arms.py · scripts/robot_real.sh · official_sim/fetch_assets.sh · docs/09",
        "veredicto": "FALTA_PUENTE",
    },
    {
        "key": "player_hri",
        "elemento": "Recepción HRI / player 50 Hz (FSM de turnos, interlocks, limiter, pointing)",
        "ordenador": "PC2 · Orin NX",
        "estado": "Validado 6/6 personas en simulador (stroke−ancla ≤100 ms, 0 caídas, 0 gestos caminando, "
                  "17/17 tests). No envía órdenes al robot: ninguna importación de x2_ultra.real en show/.",
        "requisito": "Ciclo <1 ms sin física. Bench x86 (con física incluida): club 1,9 ms mean; "
                     "Orin estimado p99 ~6,3 ms = 19% del ciclo de 20 ms.",
        "evidencia": "src/x2_ultra/show/hri/{player,tour}.py · out_reto2/REPORT.md · runs/bench_*/report.json",
        "veredicto": "FALTA_PUENTE",
    },
    {
        "key": "llm_qwen3",
        "elemento": "LLM Qwen3-4B Q4_K_M (contenedor x2-llm, llama.cpp)",
        "ordenador": "PC2 · Orin NX",
        "estado": "Medido en x86 de desarrollo: 71,3 tok/s, RAM pico 5,7 GB, VRAM 3,9 GB, GPU 99% durante "
                  "generación. Perfil Jetson existe (compose.jetson.yml, arm64 dustynv/llama_cpp r36.4.0, "
                  "ctx 16k, KV q8_0 → 4,7 GB unificados) pero NUNCA desplegado ni medido en el Orin; "
                  "expectativa documentada 35-40 tok/s (~la mitad que en x86).",
        "requisito": "4,7 GB unificados · GPU saturada en ráfagas de ~1,4 s (p50 por respuesta).",
        "evidencia": "HRIEGO_gesture/visits_v2/resource_profile.json · llm/compose.jetson.yml · llm/README.md",
        "veredicto": "CABE_SIN_MEDIR",
    },
    {
        "key": "tts_ctc",
        "elemento": "TTS Supertonic + anclas CTC QuartzNet",
        "ordenador": "PC2 · Orin NX (CPU)",
        "estado": "Medido en x86: síntesis p50 0,66 s (RTF 0,162), CTC cold 86 ms, RSS 284 MiB; "
                  "0,65 GB en el layout Jetson. Reproducción en altavoces del robot sin medir "
                  "(vía AimDK PlayTts documentada; latencia de altavoz desconocida).",
        "requisito": "0,65 GB · CPU ligera (RTF ≪1 deja margen de sobra).",
        "evidencia": "HRIEGO_gesture/validation/latency_budget.json · docs/reto-2/README_HRI.md §d",
        "veredicto": "CABE_SIN_MEDIR",
    },
    {
        "key": "face_vivo",
        "elemento": "Face en vivo (UniFace: RetinaFace + EdgeFace, cámara)",
        "ordenador": "PC2 · Orin NX",
        "estado": "Solo en x86 de desarrollo: 10,2 FPS (11,65 ms/frame UniFace), 1,81 GB RAM + 543 MB VRAM, "
                  "ruedas torch cu130 x86, webcam V4L2 local; sonda en vivo 2,6-2,7 s/foto. Sin build arm64 "
                  "ni medidas en Orin. LA DEMO FUNCIONA SIN FACE: identidades precomputadas "
                  "(assets/hri/identities.json, persona→rango) dan el mismo resultado validado.",
        "requisito": "~2,3 GB (RAM+VRAM en x86) · ráfagas de GPU que compiten con el LLM.",
        "evidencia": "docs/conclusiones_face.md · docs/reto-2/README_HRI.md §b/§g",
        "veredicto": "NECESITA_PORT",
    },
    {
        "key": "gestos_sonic",
        "elemento": "Generación de gesto co-speech (StreamTalk+GMR+SONIC)",
        "ordenador": "PC2 (motion server online) — hoy: PC externo (offline) / paquetes precomputados",
        "estado": "Offline en dev: 31 s frío / 6,3 s caliente por gesto; 3 workers = 7,9 GB RSS "
                  "(«NO FIT» en 22 GB junto a LLM+gemelo). Online en Jetson (estrategia, NO medido): "
                  "motion server ~4,5 GB, primer comando 0,44-0,55 s. La demo del reto 2 usa paquetes "
                  "PRECOMPUTADOS versionados en assets/hri/ → coste en bordo = reproducción.",
        "requisito": "Precomputado: ~0 (va en los 3,67 GB del proceso brain). Online: +4,5 GB y CPU/GPU sostenida.",
        "evidencia": "HRIEGO_gesture/workers/REPORT.md · docs/reto-2/README_HRI.md §d · assets/hri/PROVENANCE.md",
        "veredicto": "CABE_SIN_MEDIR",
    },
    {
        "key": "presets_vendor",
        "elemento": "Presets de movimiento del vendor (SetMcPresetMotion; 29 confirmados, incl. bow 3001)",
        "ordenador": "PC1 ejecuta · PC2 ordena",
        "estado": "Existen en el robot. Preplay MC de 1,0 s es parte de la dinámica (ponerlo a 0 hizo caer "
                  "el bow). Latencias c08 medidas en gemelo con MC v1.0.0; el robot usa v1.1.4 → "
                  "recalibrar lag audio-gesto en el robot.",
        "requisito": "RTT de servicio MC medido (gemelo): p50 0,86 ms.",
        "evidencia": "docs/reto-2/README_HRI.md §e5/§f · HRIEGO_gesture/config/preset_latency_c08.json",
        "veredicto": "A_BORDO",
    },
    {
        "key": "pack_comercial",
        "elemento": "Pack comercial reto 1 (timeline_ultra.json + brazos SONIC npz) y presets del reto 2",
        "ordenador": "Datos para PC2",
        "estado": "YA está en formato de robot real: cmd (N,14) brazos L7,R7 @50 Hz con nombres AimDK, "
                  "waist_head (N,5), frame 100 = t0 de audio. PERO validado solo en gemelo MC v1.0.0: "
                  "el techo real URS es 0,35 rad/s y los comandos lo exceden en 54-64% de los ticks; "
                  "rate-limitado a 0,35 la amplitud cae hasta −58% y el stroke se retrasa +1,8…+2,9 s.",
        "requisito": "Re-timar con presets del MC (que sí los ejecuta a su velocidad) o regenerar SONIC "
                     "con limiter 0,35 + anclas CTC recalculadas.",
        "evidencia": "HRIEGO_gesture/twin/replay/README.md · HRIEGO_gesture/opt/sim2real/REPORT.md",
        "veredicto": "RETIMING",
    },
    {
        "key": "gemelo_mujoco",
        "elemento": "Gemelo digital MuJoCo (simulación + física 500 Hz + render)",
        "ordenador": "PC externo de desarrollo (por diseño)",
        "estado": "No es necesario a bordo: en el robot la locomoción la ejecuta el MC en PC1. "
                  "Bench x86: escena de 2 robots 6,0-6,7 núcleos de media (picos ~10) + 3,6-4,2 GB RSS; "
                  "en el Orin (8 núcleos ~2× más lentos) se comería prácticamente toda la CPU y dejaría "
                  "sin margen al stack HRI. Dejarlo fuera libera todo el presupuesto de PC2.",
        "requisito": "Si aun así se quisiera a bordo: solo escena de 1 robot (club), RTF>1 estimado, "
                     "y sin LLM/Face concurrentes.",
        "evidencia": "runs/bench_full_v2/report.json · runs/bench_reto2hri/ · docs/12-benchmark-consumo.md §6",
        "veredicto": "FUERA",
    },
    {
        "key": "pantalla_audio_venue",
        "elemento": "Pantalla LED del venue, vídeo de fondo, audio ambiental",
        "ordenador": "Equipo externo del evento / PC3 · RK3588 (voz y pantalla del robot)",
        "estado": "La pantalla LED del escenario es equipo del venue, no del robot. Nada en el repo "
                  "apunta aún al PC3; la voz del robot iría por AimDK PlayTts (documentado, sin medir "
                  "con los WAV de Supertonic).",
        "requisito": "—",
        "evidencia": "docs/03-sdk-aimdk.md §9 · src/x2_ultra/show/screen.py (simulación)",
        "veredicto": "FUERA",
    },
]

# ---------------------------------------------------------------------------
# Presupuesto de memoria de PC2 (Orin NX 16 GB unificados) — README_HRI §d
# ---------------------------------------------------------------------------
PC2_RAM_GB = 16.0
MEMORY_CONFIGS = [
    {
        "config": "A · Demo hackathon (paquetes precomputados, sin LLM en línea)",
        "procesos": {"brain (UniFace + StreamTalk/SONIC ONNX CPU)": 3.67,
                     "TTS Supertonic + CTC QuartzNet": 0.65,
                     "motor 50 Hz (player + interlocks + policy locomoción)": 0.5},
        "nota": "Es lo que corre hoy la demo validada 6/6 (out_reto2/REPORT.md).",
    },
    {
        "config": "B · A + LLM en línea (bienvenidas generadas por Qwen3-4B)",
        "procesos": {"config A": 4.82, "llama-server (ctx 16k, KV q8_0, unificado)": 4.7},
        "nota": "README_HRI §d: «~10 GB de 16 GB (margen para SO/cachés)».",
    },
    {
        "config": "C · Todo en línea (+ motion server SONIC en vivo)",
        "procesos": {"config B": 9.52, "motion server online": 4.5},
        "nota": "INTEGRACION_RETOS §4.5: «~14 GB antes del SO y de MuJoCo: no cabe con holgura. "
                "Medir en el Orin». Inviable con el gemelo a bordo; justo sin él.",
    },
]

# Latencias de interacción medidas en x86 (README_HRI §d) — el Orin NO está medido.
LATENCIAS_X86 = [
    ("LLM: clasificar tipo de turno", 0.20, "README_HRI §d (x86)"),
    ("LLM: primera frase hablada", 0.34, "README_HRI §d (x86)"),
    ("TTS: primera frase lista", 0.62, "README_HRI §d (x86)"),
    ("LLM → primer audio (p50 / p95)", 1.05, "README_HRI §d: p50 1,05 s / p95 1,28 s (x86)"),
    ("Audio + gesto con motion server online", 1.85, "README_HRI §d: ~1,8-1,9 s (x86)"),
    ("SONIC offline por turno (no candidato a bordo)", 4.3, "README_HRI §d"),
    ("Qwen respuesta completa (p50, contenedor x86)", 1.44, "validation/latency_budget.json (n=30, x86)"),
]

GAPS_SIM2REAL = [
    "Brazos URS a 0,35 rad/s reales vs 2,5 rad/s del sim: los comandos validados exceden el techo en "
    "54-64% de los ticks; rate-limitar cuesta amplitud (−0…58%) y stroke +1,8…+2,9 s → re-timar con "
    "presets del MC o regenerar SONIC con limiter 0,35 y anclas recalculadas. (opt/sim2real/REPORT.md)",
    "Cabeza sin canal en URS (HEAD_ONLY es exclusivo): head-pitch del bow y giro del POINT no tienen "
    "canal real simultáneo con brazos → omitir cabeza o McBodyPose (sin probar en robot).",
    "Salida de URS por SDK no conseguida (MotionBusy code=9); STAND_DEFAULT snapping devuelve brazos "
    "al default a ~10 rad/s con ringing 0,5 s → transiciones lentas explícitas, nunca salir con brazos arriba.",
    "Presets en real llevan preplay MC de 1,0 s (el sim usa replay cinématico sin preplay) → recalibrar "
    "TRACK_LAG_S y el lag audio-gesto en el robot; reloj del robot 1,5-6,8 s adelantado (usar stamp=0); "
    "latencia del altavoz sin medir.",
    "Marcha con brazos arriba NO validada en real (gemelo cae 3/3) → mantener caminar→parar→asentar→gestar "
    "(interlock doble motor+player, ya implementado).",
    "Sin manos en el robot (hand_sub_mode=1): masa de punta −0,45 kg/lado y PD real con sag ~0,03 rad → "
    "error de seguimiento ~2× el del sim.",
    "Interlock de caída en real debe leer HAL a 500 Hz (el estado del MC va a solo 10 Hz).",
    "MC v1.1.4 (robot) vs v1.0.0 (gemelo): re-medir latencias de presets y mc_service_rtt.",
]

CHECKLIST_BORDO = [
    "Despliegue PC2: contenedor/script con SDK AimDK aarch64 (official_sim/fetch_assets.sh) + overlay "
    "(compilable aarch64) + paquete x2_ultra — hoy robot_real.sh asume docker x86 y cable a 10.0.1.41.",
    "Puente player/show → robot: que la salida del player HRI (y del show comercial) alimente "
    "/mc/upper_body_command y /aima/hc/locomotion vía x2_ultra.real en vez de MuJoCo ctrl "
    "(el handoff de real/arms.py ya está probado en robot; no está conectado).",
    "Audio a bordo: reproducir los WAV Supertonic por PlayTts/PC3 y medir latencia de altavoz + reloj común.",
    "Re-timing del pack comercial y presets al techo URS 0,35 rad/s + recalibración de lag con MC v1.1.4.",
    "Resolver la salida limpia de URS por SDK (punto abierto en docs/09).",
    "(Opcional demo) Face: build arm64/Jetson + medidas en Orin; si no, identidades precomputadas "
    "(ya validadas: mismo resultado persona→rango).",
    "(Opcional online) x2-llm: desplegar compose.jetson.yml en el Orin y medir tok/s reales "
    "(expectativa 35-40 vs 71,3 en x86).",
    "Correr x2-bench EN el Orin (MUJOCO_GL=egl, --cores 8) para sustituir la banda de estimación "
    "×1,8-2,2 por medidas directas.",
]


def _bench_summary(report: dict | None) -> dict:
    """Extrae de un report.json de bench las cifras útiles para el análisis de despliegue."""
    if not report:
        return {}
    out: dict = {"fuente": report.get("ctx", {}).get("out_dir", "?"),
                 "commit": report.get("environment", {}).get("git", {}).get("commit", "?")}
    for key in ("club_tours", "stage_show", "realtime_50hz"):
        sc = report.get("scenarios", {}).get(key, {})
        m, o = sc.get("metrics", {}), sc.get("orin_nx_estimate", {})
        row: dict = {}
        if key == "club_tours":
            cats = m.get("categories", {})
            worst = max((v["cycle_50hz"] for v in cats.values()
                         if isinstance(v, dict) and "cycle_50hz" in v),
                        key=lambda s: s["mean"], default=None)
            if worst:
                row = {"ciclo_x86_ms": worst}
        else:
            for mk in ("cycle_50hz", "period_50hz"):
                if isinstance(m.get(mk), dict):
                    row = {"ciclo_x86_ms": m[mk]}
        if o.get("estimated"):
            row["orin_est_ms"] = o["estimated"]
            if o.get("budget_20ms_fraction_estimated") is not None:
                row["orin_budget_frac"] = o["budget_20ms_fraction_estimated"]
        if row:
            out[key] = row
    return out


def build_deployment(bench_report: dict | None = None) -> dict:
    """Ensambla el análisis completo (serializable)."""
    configs = []
    for cfg in MEMORY_CONFIGS:
        total = round(sum(cfg["procesos"].values()), 2)
        configs.append({**cfg, "total_gb": total,
                        "pct_of_16gb": round(100 * total / PC2_RAM_GB, 1),
                        "veredicto": "CABE" if total <= 10.5 else ("JUSTO" if total <= 14.5 else "NO_CABE")})
    return {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "pregunta": "¿Pueden todos los elementos del sistema ejecutarse a bordo del X2 Ultra-N "
                    "(PC1/PC2/PC3) sin depender de un PC externo?",
        "robot": {"pc1": "RK3588s · MC/HAL vendor (prohibido código propio)",
                  "pc2": "Jetson Orin NX 16 GB · 8×A78AE · GPU Ampere 1024 · modos 10-25 W · código propio en contenedor",
                  "pc3": "RK3588 · voz, pantalla, interacción"},
        "elements": ELEMENTS,
        "pc2_memory": {"ram_gb": PC2_RAM_GB, "configs": configs},
        "latencias_interaccion_x86": [
            {"paso": p, "s": s, "fuente": f} for p, s, f in LATENCIAS_X86],
        "gaps_sim2real": GAPS_SIM2REAL,
        "checklist_para_bordo": CHECKLIST_BORDO,
        "bench": _bench_summary(bench_report),
        "veredicto": {
            "por_presupuesto": "SÍ — en config A (demo precomputada, ~4,8 GB / 30% de la RAM de PC2) y "
                               "config B (+LLM en línea, ~9,5 GB / 59%) todo cabe en el Orin NX: brazos 50 Hz "
                               "<1 núcleo, locomoción la lleva el MC vendor en PC1, TTS/CTC en CPU con RTF 0,16, "
                               "y el ciclo de control estimado en Orin usa ≤20% del presupuesto de 20 ms (club).",
            "hoy": "NO — el sistema integrado nunca ha corrido a bordo: todo lo validado (show, recepción HRI, "
                   "HRIEGO) se opera desde un PC externo x86 con docker y cable a 10.0.1.41; el puente "
                   "player→robot no existe (show/ no importa x2_ultra.real) y «el Orin NX 16 GB no está medido» "
                   "(HRIEGO_gesture/README.md §9).",
            "gemelo": "El gemelo MuJoCo se queda FUERA a bordo por diseño: no hace falta (la locomoción real la "
                      "ejecuta el MC) y saturaría los 8 núcleos del Orin (6-6,7 núcleos x86 medidos para la "
                      "escena de 2 robots). Su papel es validación previa en el PC de desarrollo.",
            "camino_minimo": "Gaps 1-4 del checklist (contenedor PC2 + puente player→real + audio a bordo + "
                             "re-timing a 0,35 rad/s) dan un despliegue a bordo viable para la demo; Face y LLM "
                             "en línea son opcionales (la demo funciona con identidades y paquetes precomputados).",
        },
    }


_VERDICT_ICON = {"A_BORDO": "✅ a bordo", "CABE_SIN_MEDIR": "🟡 cabe, sin medir en Orin",
                 "FALTA_PUENTE": "🔴 falta puente/despliegue", "NECESITA_PORT": "🔴 necesita port arm64",
                 "RETIMING": "🟠 existe, requiere re-timing", "FUERA": "⬜ fuera del robot (diseño)"}


def render_deployment_md(dep: dict) -> str:
    lines = [
        "# Viabilidad de despliegue a bordo — X2 Ultra-N sin PC externo",
        "",
        f"Generado: {dep['generated']} · bench citado: `{dep['bench'].get('fuente', 'ninguno')}`"
        f" (commit `{dep['bench'].get('commit', '?')}`)" if dep.get("bench") else
        f"Generado: {dep['generated']} · sin corrida de bench citada",
        "",
        f"> **Pregunta:** {dep['pregunta']}",
        "",
        "## Veredicto",
        "",
        f"- **Por presupuesto:** {dep['veredicto']['por_presupuesto']}",
        f"- **Hoy:** {dep['veredicto']['hoy']}",
        f"- **Gemelo digital:** {dep['veredicto']['gemelo']}",
        f"- **Camino mínimo a bordo:** {dep['veredicto']['camino_minimo']}",
        "",
        "## Elemento por elemento",
        "",
        "| Elemento | Ordenador destino | Veredicto | Estado / evidencia |",
        "|---|---|---|---|",
    ]
    for e in dep["elements"]:
        icon = _VERDICT_ICON.get(e["veredicto"], e["veredicto"])
        lines.append(f"| **{e['elemento']}** | {e['ordenador']} | {icon} | {e['estado']} "
                     f"*Requisito:* {e['requisito']} · *Fuente:* {e['evidencia']} |")
    lines += [
        "",
        f"## Presupuesto de memoria PC2 (Orin NX {dep['pc2_memory']['ram_gb']:.0f} GB unificados)",
        "",
        "| Config | Procesos (GB) | Total | % de 16 GB | Veredicto | Nota |",
        "|---|---|---|---|---|---|",
    ]
    for c in dep["pc2_memory"]["configs"]:
        procs = " + ".join(f"{k} {v}" for k, v in c["procesos"].items())
        lines.append(f"| {c['config']} | {procs} | **{c['total_gb']} GB** | {c['pct_of_16gb']}% "
                     f"| {c['veredicto']} | {c['nota']} |")
    lines += [
        "",
        "## Latencias de interacción (medidas en x86 — el Orin NO está medido)",
        "",
        "| Paso | s | Fuente |",
        "|---|---|---|",
    ]
    for lat in dep["latencias_interaccion_x86"]:
        lines.append(f"| {lat['paso']} | {lat['s']} | {lat['fuente']} |")
    if dep.get("bench"):
        lines += ["", "## Lazo de control: bench x86 → estimación Orin NX", ""]
        def _ms(v) -> str:
            return f"{v:.3f}" if isinstance(v, (int, float)) else "?"

        for key in ("club_tours", "stage_show", "realtime_50hz"):
            b = dep["bench"].get(key)
            if not b:
                continue
            x86 = b.get("ciclo_x86_ms", {})
            orin = b.get("orin_est_ms", {})
            frac = b.get("orin_budget_frac")
            piece = f"- `{key}`: x86 mean {_ms(x86.get('mean'))} ms / p99 {_ms(x86.get('p99'))} ms"
            if orin.get("mean") is not None:
                piece += (f" → Orin est. mean {_ms(orin.get('mean'))} ms"
                          f" / p99 {_ms(orin.get('p99'))} ms")
                if frac is not None:
                    piece += f" = **{frac:.0%} del ciclo de 20 ms**"
            else:
                piece += " → sin estimación Orin (el periodo lo fija el scheduler, no la CPU: en el Orin el sueño también apunta a 20 ms)"
            lines.append(piece)
        lines.append("- Nota: estas cifras INCLUYEN la física MuJoCo, que a bordo no corre; el motor 50 Hz "
                     "real (player + stream URS) es una fracción pequeña de este coste.")
    lines += ["", "## Gaps sim2real (docs/reto-2/README_HRI.md §f, opt/sim2real/REPORT.md)", ""]
    lines += [f"{i}. {g}" for i, g in enumerate(dep["gaps_sim2real"], 1)]
    lines += ["", "## Checklist para afirmar «sin PC externo»", ""]
    lines += [f"{i}. {c}" for i, c in enumerate(dep["checklist_para_bordo"], 1)]
    lines += [
        "",
        "---",
        "*Cifras de HRIEGO medidas en PC de desarrollo x86 (22-24 GB RAM, GPU NVIDIA 12 GB) y gemelo con "
        "MC v1.0.0 — `HRIEGO_gesture/README.md` §9: «El Orin NX 16 GB no esta medido». Las estimaciones "
        "Orin de este informe usan los factores ×1,8-2,2 documentados en `bench/power.py` y "
        "`docs/12-benchmark-consumo.md` §4.*",
    ]
    return "\n".join(lines)


def write_deployment(dep: dict, out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "deployment.json"
    md_path = out_dir / "deployment.md"
    json_path.write_text(json.dumps(dep, indent=2, ensure_ascii=False), encoding="utf-8")
    md_path.write_text(render_deployment_md(dep), encoding="utf-8")
    return json_path, md_path


def find_latest_bench_report(runs: Path = Path("runs")) -> Path | None:
    """report.json de bench más reciente bajo runs/ (para citar cifras medidas)."""
    candidates = [p for p in runs.glob("*/report.json") if p.is_file()]
    return max(candidates, key=lambda p: p.stat().st_mtime) if candidates else None
