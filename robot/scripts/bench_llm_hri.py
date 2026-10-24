#!/usr/bin/env python3
"""Banco de pruebas de candidatos LLM para el contrato HRI del Club N.

Mide latencia (primer token, speech listo, total) y calidad (validez del contrato,
tasa tras la reparación local e intención correcta en CONVERSATION) de un modelo
servido por llama-server (API compatible con OpenAI), con los mismos
`contract.build_messages` / `json_schema` / `validate` que usa la FSM. Cada
candidato se evalúa con una ejecución propia (--etiqueta) y los resúmenes se
comparan después con --comparar.

Uso en el robot (aarch64, dentro del contenedor):
  PYTHONPATH=src python3 scripts/bench_llm_hri.py --url http://127.0.0.1:8091 --etiqueta qwen3-4b-q4km
Uso en Windows (desde la raíz del repositorio):
  PYTHONPATH="src;." python scripts/bench_llm_hri.py --etiqueta qwen3-4b-q4km
Comparación de ejecuciones previas (sin llamadas al LLM):
  python3 scripts/bench_llm_hri.py --comparar out_diag/bench_llm/*_resumen.json
"""
import argparse
import importlib.util
import json
import math
import os
import sys
import time
from datetime import datetime
from pathlib import Path

if importlib.util.find_spec("x2_ultra") is None:  # respaldo si PYTHONPATH no trae src/
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from x2_ultra.hri_runtime import contract
from x2_ultra.hri_runtime.llm_client import LlamaStreamClient

# Personas por rango (nombres de los few-shot de personas.py; el WORKER trae su orden simulada).
PERSONAS = {
    "VIP": {"name": "Laura", "rank": "VIP"},
    "REGULAR": {"name": "Sebastian", "rank": "REGULAR"},
    "GUEST": {"name": "Mario", "rank": "GUEST"},
    "STAFF": {"name": "Luis", "rank": "STAFF"},
    "WORKER": {"name": "Josaed", "rank": "WORKER", "worker_subtype": "logistica",
               "work_order": {"area": "almacenes_logistica", "nombre_area": "Almacenes de logística"},
               "work_order_direction": "left"},
}

# Escenarios fijos (áreas y direcciones de club_kb.json); los estados se alternan para
# que la caché de prefijos no llegue tibia. CONVERSATION guarda la intención esperada.
ESCENARIOS = [
    {"id": "saludo_vip", "state": "GREETING", "rank": "VIP", "person": PERSONAS["VIP"]},
    {"id": "conv_horario_biblioteca", "state": "CONVERSATION", "rank": "REGULAR", "person": PERSONAS["REGULAR"],
     "utterance": "Buenas tardes, ¿a qué hora abre la biblioteca del club?", "intent_esperado": "PREGUNTA"},
    {"id": "guia_biblioteca_vip", "state": "POINT_GUIDE", "rank": "VIP", "person": PERSONAS["VIP"],
     "direction": "front", "pending_area": "biblioteca_galeria"},
    {"id": "saludo_staff", "state": "GREETING", "rank": "STAFF", "person": PERSONAS["STAFF"]},
    {"id": "conv_pide_gimnasio", "state": "CONVERSATION", "rank": "GUEST", "person": PERSONAS["GUEST"],
     "utterance": "Quiero ir al gimnasio, por favor.", "intent_esperado": "PIDE_DESTINO"},
    {"id": "denial_bar_invitado", "state": "ACCESS_DENIAL", "rank": "GUEST", "person": PERSONAS["GUEST"],
     "pending_area": "bares_la_cava", "alternatives": ["restaurantes_cafeterias"]},
    {"id": "destino_rol_worker", "state": "DESTINO_DE_ROL", "rank": "WORKER", "person": PERSONAS["WORKER"],
     "direction": "left", "pending_area": "almacenes_logistica"},
    {"id": "conv_despedida_vip", "state": "CONVERSATION", "rank": "VIP", "person": PERSONAS["VIP"],
     "utterance": "Muchas gracias, hasta luego.", "intent_esperado": "DESPEDIDA"},
    {"id": "oferta_guia_piscina", "state": "GUIDANCE_OFFER", "rank": "REGULAR", "person": PERSONAS["REGULAR"],
     "pending_area": "piscina_spa_humedas"},
    {"id": "saludo_invitado", "state": "GREETING", "rank": "GUEST", "person": PERSONAS["GUEST"]},
    {"id": "conv_opciones_staff", "state": "CONVERSATION", "rank": "STAFF", "person": PERSONAS["STAFF"],
     "utterance": "¿A dónde puedo ir? ¿Qué opciones hay en el club?", "intent_esperado": "PREGUNTA"},
    {"id": "despedida_vip", "state": "FAREWELL", "rank": "VIP", "person": PERSONAS["VIP"]},
    {"id": "conv_charla_socio", "state": "CONVERSATION", "rank": "REGULAR", "person": PERSONAS["REGULAR"],
     "utterance": "Hoy hace un día muy bonito en Bogotá.", "intent_esperado": "OTRO"},
    {"id": "conv_si_corto", "state": "CONVERSATION", "rank": "GUEST", "person": PERSONAS["GUEST"],
     "utterance": "Sí.", "intent_esperado": "SI"},
]

CLAVES_METRICAS = ("t_first_token", "t_type_known", "t_first_sentence", "t_speech_done",
                   "t_total", "prompt_tokens", "completion_tokens", "cached_tokens")
CLAVES_LATENCIA = ("t_first_token", "t_speech_done", "t_total")


# ---------------------------------------------------------------- utilidades numéricas
def percentil(vals, p):
    """Percentil por interpolación lineal sobre la lista ordenada (sin numpy)."""
    s = sorted(v for v in vals if v is not None)
    if not s:
        return None
    k = (len(s) - 1) * p / 100.0
    f, c = math.floor(k), math.ceil(k)
    return s[int(k)] if f == c else s[f] * (c - k) + s[c] * (k - f)


def red3(v): return round(v, 3) if v is not None else None
def pct(a, b): return round(100.0 * a / b, 1) if b else None
def fmt(v, nd=1): return "n/d" if v is None else f"{v:.{nd}f}"


def media(vals): return red3(sum(v) / len(v)) if (v := [x for x in vals if x is not None]) else None


# ---------------------------------------------------------------- una llamada al LLM
def llamar(client, kb, esc, args, etiqueta, rep):
    """Un intento único (sin reintento) contra el contrato; nunca lanza excepción."""
    reg = {"etiqueta": etiqueta, "id": esc["id"], "state": esc["state"], "rank": esc["rank"],
           "repeticion": rep, "intent_esperado": esc.get("intent_esperado"), "valid": False,
           "valid_after_repair": False, "errors": [], "intent_ok": None, "speech": None,
           "obj": None, "tokens_s": None, "error": None}
    t0 = time.perf_counter()
    try:
        msgs = contract.build_messages(esc["state"], esc["rank"], esc["person"], kb,
                                       utterance=esc.get("utterance"), alternatives=esc.get("alternatives"),
                                       pending_area=esc.get("pending_area"))
        schema = contract.json_schema(esc["state"], esc["rank"], esc.get("direction"))
        res = client.stream_chat(msgs, schema, temperature=args.temperatura,
                                 max_tokens=args.max_tokens, seed=args.semilla)
        m = res.get("metrics") or {}
        reg.update({k: m.get(k) for k in CLAVES_METRICAS})
        obj, errores = None, []
        try:
            obj = json.loads(res.get("raw") or "")
            errores = contract.validate(obj, esc["state"], esc["rank"], direction=esc.get("direction"))
        except json.JSONDecodeError as exc:
            errores = [f"json_error: {exc}"]
        reg["obj"], reg["errors"], reg["valid"] = obj, errores, not errores
        if isinstance(obj, dict):
            reg["speech"] = obj.get("speech")
            if reg["valid"]:
                reg["valid_after_repair"] = True
            else:  # ¿queda válido tras la reparación local del trigger_text?
                reparado = contract.repair_trigger(obj)
                reg["valid_after_repair"] = not contract.validate(
                    reparado, esc["state"], esc["rank"], direction=esc.get("direction"))
            if esc["state"] == "CONVERSATION":
                reg["intent_ok"] = obj.get("intent") == esc.get("intent_esperado")
            ct, tt, tf = m.get("completion_tokens"), m.get("t_total"), m.get("t_first_token")
            if ct and tt is not None and tf is not None and tt > tf:
                reg["tokens_s"] = ct / (tt - tf)
    except Exception as exc:
        reg["error"] = f"{type(exc).__name__}: {exc}"
        reg["errors"] = [f"excepcion: {reg['error']}"]
    reg["wall_s"] = round(time.perf_counter() - t0, 3)
    return reg


def calentar(client, kb, args):
    """Una llamada GREETING de descarte antes de medir (prepara la caché del prompt)."""
    try:
        msgs = contract.build_messages("GREETING", "VIP", PERSONAS["VIP"], kb)
        client.stream_chat(msgs, contract.json_schema("GREETING", "VIP", None),
                           temperature=args.temperatura, max_tokens=args.max_tokens, seed=args.semilla)
        print("Calentamiento: llamada GREETING de descarte completada.")
    except Exception as exc:
        print(f"Aviso: el calentamiento falló ({exc}); se continúa con la medición.", file=sys.stderr)


# ---------------------------------------------------------------- resumen y salidas
def resumir(regs, etiqueta, url, prefijo, ts):
    n = len(regs)
    conv = [r for r in regs if r.get("intent_ok") is not None]
    lat = {k: {"p50": red3(percentil([r.get(k) for r in regs], 50)),
               "p90": red3(percentil([r.get(k) for r in regs], 90)),
               "max": red3(max([r[k] for r in regs if r.get(k) is not None], default=None))}
           for k in CLAVES_LATENCIA}
    return {"etiqueta": etiqueta, "url": url, "ts": ts, "n": n, "prefijo_estatico": bool(prefijo),
            "fallos": sum(1 for r in regs if r.get("error")),
            "validez_pct": pct(sum(1 for r in regs if r["valid"]), n),
            "validez_tras_reparar_pct": pct(sum(1 for r in regs if r["valid_after_repair"]), n),
            "intent_ok_pct": pct(sum(1 for r in conv if r["intent_ok"]), len(conv)),
            "latencias": lat,
            "tokens_s_medio": media([r.get("tokens_s") for r in regs]),
            "prompt_tokens_medio": media([r.get("prompt_tokens") for r in regs]),
            "cached_tokens_medio": media([r.get("cached_tokens") for r in regs]),
            "muestras": [{"id": r["id"], "speech": r["speech"]} for r in regs if r.get("speech")][:8]}


def escribir_salidas(regs, resumen, salida, etiqueta, ts):
    d = Path(salida)
    d.mkdir(parents=True, exist_ok=True)
    base = f"{etiqueta}_{ts}"
    ruta_jsonl = d / f"{base}.jsonl"
    with open(ruta_jsonl, "w", encoding="utf-8") as fh:
        for r in regs:
            fh.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
    ruta_resumen = d / f"{base}_resumen.json"
    ruta_resumen.write_text(json.dumps(resumen, ensure_ascii=False, indent=2), encoding="utf-8")
    return ruta_jsonl, ruta_resumen


def imprimir_resumen(res, rutas):
    print(f"\n## Banco LLM — {res['etiqueta']}")
    print(f"- URL: {res['url']} | llamadas: {res['n']} | fallos: {res['fallos']} | "
          f"prefijo estático: {'sí' if res['prefijo_estatico'] else 'no'}")
    print("\n| Latencia (s) | p50 | p90 | máx |")
    print("|---|---|---|---|")
    for titulo, k in (("Primer token", "t_first_token"), ("Speech listo", "t_speech_done"), ("Total", "t_total")):
        d = res["latencias"][k]
        print(f"| {titulo} | {fmt(d['p50'], 3)} | {fmt(d['p90'], 3)} | {fmt(d['max'], 3)} |")
    print(f"\n- Validez del contrato: {fmt(res['validez_pct'])} % | tras reparar: "
          f"{fmt(res['validez_tras_reparar_pct'])} % | intent correcto: {fmt(res['intent_ok_pct'])} %")
    print(f"- Generación: {fmt(res['tokens_s_medio'])} tok/s | prompt medio: {fmt(res['prompt_tokens_medio'])} "
          f"tokens | caché media: {fmt(res['cached_tokens_medio'])} tokens")
    print(f"- Archivos: {rutas[0]} y {rutas[1]}")
    print("\n### Muestras de habla")
    for m in res["muestras"]:
        print(f"- [{m['id']}] «{m['speech']}»")


def par_lat(d):
    d = d or {}
    return f"{fmt(d.get('p50'), 3)}/{fmt(d.get('p90'), 3)}"


def comparar(archivos):
    """Lee resúmenes de ejecuciones previas e imprime una tabla Markdown comparativa."""
    filas = []
    for ruta in archivos:
        try:
            with open(ruta, encoding="utf-8") as fh:
                filas.append(json.load(fh))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"Aviso: no se pudo leer «{ruta}»: {exc}", file=sys.stderr)
    if not filas:
        print("Error: no hay resúmenes válidos para comparar.", file=sys.stderr)
        return 1
    print("| etiqueta | n | validez % | tras reparar % | intent % | 1er token p50/p90 "
          "| speech listo p50/p90 | total p50/p90 | tok/s |")
    print("|---|---|---|---|---|---|---|---|---|")
    for r in filas:
        lat = r.get("latencias") or {}
        print(f"| {r.get('etiqueta', '?')} | {r.get('n', '?')} | {fmt(r.get('validez_pct'))} "
              f"| {fmt(r.get('validez_tras_reparar_pct'))} | {fmt(r.get('intent_ok_pct'))} "
              f"| {par_lat(lat.get('t_first_token'))} | {par_lat(lat.get('t_speech_done'))} "
              f"| {par_lat(lat.get('t_total'))} | {fmt(r.get('tokens_s_medio'))} |")
    return 0


def verificar_escenarios(kb, escenarios):
    """Comprobación sin LLM: cada escenario construye mensajes y esquema sin excepción."""
    for esc in escenarios:
        try:
            contract.build_messages(esc["state"], esc["rank"], esc["person"], kb,
                                    utterance=esc.get("utterance"), alternatives=esc.get("alternatives"),
                                    pending_area=esc.get("pending_area"))
            contract.json_schema(esc["state"], esc["rank"], esc.get("direction"))
        except Exception as exc:
            print(f"Error en el escenario «{esc['id']}» ({esc['state']}/{esc['rank']}): "
                  f"{type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
    print(f"ok {len(escenarios)} escenarios")
    return 0


# ---------------------------------------------------------------- CLI
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default=os.environ.get("HRI_LLM_URL", "http://127.0.0.1:8080"),
                    help="URL base de llama-server (por defecto, env HRI_LLM_URL)")
    ap.add_argument("--etiqueta", default=None,
                    help='etiqueta del candidato, p. ej. "qwen3-4b-q4km" (obligatoria salvo con --comparar)')
    ap.add_argument("--repeticiones", type=int, default=2, help="vueltas completas a la lista de escenarios")
    ap.add_argument("--salida", default="out_diag/bench_llm", help="carpeta de salida (.jsonl y resumen)")
    ap.add_argument("--prefijo-estatico", action="store_true",
                    help='activa HRI_PROMPT_PREFIJO_ESTATICO=1 (añade "+prefijo" a la etiqueta)')
    ap.add_argument("--timeout", type=float, default=120.0, help="timeout HTTP por llamada (segundos)")
    ap.add_argument("--max-tokens", type=int, default=320, help="máximo de tokens generados por llamada")
    ap.add_argument("--temperatura", type=float, default=0.2, help="temperatura del muestreo")
    ap.add_argument("--semilla", type=int, default=1234, help="semilla del muestreo (reproducibilidad)")
    ap.add_argument("--calentar", action=argparse.BooleanOptionalAction, default=True,
                    help="llamada GREETING de descarte antes de medir (--no-calentar la omite)")
    ap.add_argument("--comparar", nargs="+", metavar="RESUMEN", default=None,
                    help="compara archivos *_resumen.json de ejecuciones previas (sin llamadas al LLM)")
    ap.add_argument("--limite", type=int, default=None, help="ejecuta solo los primeros N escenarios")
    ap.add_argument("--verificar-escenarios", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)

    etiqueta = args.etiqueta
    if args.prefijo_estatico:
        os.environ["HRI_PROMPT_PREFIJO_ESTATICO"] = "1"
        etiqueta = (etiqueta or "candidato") + "+prefijo"
    if args.comparar:
        return comparar(args.comparar)

    kb = contract.load_kb()
    escenarios = ESCENARIOS if not args.limite else ESCENARIOS[:max(args.limite, 0)]
    if not escenarios:
        print("Error: --limite deja la lista de escenarios vacía.", file=sys.stderr)
        return 1
    if args.verificar_escenarios:
        return verificar_escenarios(kb, escenarios)
    if not etiqueta:
        ap.error("--etiqueta es obligatoria (salvo con --comparar)")

    client = LlamaStreamClient(args.url, timeout=args.timeout)
    if not client.health():
        print(f"Error: llama-server no responde en {args.url} (GET /health). Verifique que el servicio "
              "esté arriba y la URL (--url o env HRI_LLM_URL).", file=sys.stderr)
        return 2
    if args.calentar:
        calentar(client, kb, args)

    print(f"Evaluando «{etiqueta}» en {args.url}: {len(escenarios)} escenarios x {args.repeticiones} "
          f"repeticiones (semilla {args.semilla}, temperatura {args.temperatura}).")
    regs = []
    for rep in range(1, args.repeticiones + 1):
        for esc in escenarios:
            reg = llamar(client, kb, esc, args, etiqueta, rep)
            regs.append(reg)
            estado = ("ERROR" if reg["error"] else "válido" if reg["valid"]
                      else "reparable" if reg["valid_after_repair"] else "INVÁLIDO")
            extra = "" if reg["intent_ok"] is None else f", intent {'ok' if reg['intent_ok'] else 'FALLO'}"
            total = reg.get("t_total")
            print(f"- {rep}/{args.repeticiones} {esc['id']} ({esc['state']}/{esc['rank']}): {estado}{extra}"
                  + (f", total {total:.2f} s" if total is not None else "")
                  + (f" | {reg['error']}" if reg["error"] else ""))

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    resumen = resumir(regs, etiqueta, args.url, args.prefijo_estatico, ts)
    rutas = escribir_salidas(regs, resumen, args.salida, etiqueta, ts)
    imprimir_resumen(resumen, rutas)
    return 0


if __name__ == "__main__":
    sys.exit(main())
