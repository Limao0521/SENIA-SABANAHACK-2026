#!/usr/bin/env python3
"""Descarga el JSON de una obra pública de LinkCraft (sin iniciar sesión)."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

PRESETS = {
    "beat_dance": "01KB2KPKJFVMV50KASN0X4ATHW",
    "tai_chi_1": "01K9CAXMTDA0WBQRJ6V68EE6QS",
    "chen_style_tai_chi": "01KMM3SQWBFSVZE9W9XZ7YPR1H",
    "tai_chi_2": "01KN4108ZC09F7TYBG9ZDVVSAT",
    "kung_fu": "01KPN9P7SJ53271Y0V2ANAYW1C",
}
REQUESTED = ("tai_chi_1", "chen_style_tai_chi", "tai_chi_2", "kung_fu")


def download(work_id: str, output: Path) -> None:
    endpoint = "https://linkcraft.agibot.com/api/v1/public/work?" + urlencode({"id": work_id})
    with urlopen(endpoint, timeout=30) as response:
        work = json.load(response)
    if work.get("code") != 0 or not work.get("data"):
        raise RuntimeError(f"LinkCraft no devolvió la obra pública {work_id}: {work.get('message')}")
    data = work["data"]
    motions = [item for item in data.get("motion", []) if item.get("jsonFileUrl")]
    if len(motions) != 1:
        raise RuntimeError(f"La obra contiene {len(motions)} segmentos JSON; se esperaba uno")
    with urlopen(motions[0]["jsonFileUrl"], timeout=90) as response:
        raw = response.read()
    clip = json.loads(raw)
    if not clip.get("frames") or not clip.get("fps"):
        raise ValueError("El archivo descargado no contiene cuadros de movimiento")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(raw)
    print(f"{data['name']} · {data['productModel']} {data['productVersion']} · "
          f"{len(clip['frames'])} cuadros a {clip['fps']} fps → {output}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--work-id", help="ID de una obra pública con un segmento JSON")
    group.add_argument("--preset", choices=PRESETS, default="beat_dance")
    group.add_argument("--all-requested", action="store_true", help="descarga los cuatro movimientos solicitados")
    parser.add_argument("--output", type=Path, help="ruta de salida (una sola obra)")
    args = parser.parse_args()
    if args.all_requested:
        if args.output:
            parser.error("--output no se puede usar con --all-requested")
        for name in REQUESTED:
            download(PRESETS[name], Path("assets/motions") / f"{name}.json")
    else:
        name = args.preset if not args.work_id else args.work_id
        output = args.output or Path("assets/motions") / f"{name}.json"
        download(args.work_id or PRESETS[args.preset], output)


if __name__ == "__main__":
    main()
