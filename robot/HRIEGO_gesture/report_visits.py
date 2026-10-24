"""Merge anchor_plan results into visit_log.json and print/write a per-turn table (visits_v2/report.md)."""
from __future__ import annotations

import json
from pathlib import Path

from .paths import rel

ROOT = Path(__file__).parent / "visits_v2"


def main():
    md = ["| rank | state | speech | emote | trigger | anchor start s | plan status |", "|---|---|---|---|---|---|---|"]
    for d in sorted(ROOT.iterdir()):
        lp, sp = d / "visit_log.json", d / "anchor_plan" / "summary.json"
        if not lp.exists():
            continue
        log = json.loads(lp.read_text())
        plans = {r["turn_id"]: r for r in json.loads(sp.read_text())["rows_detail"]} if sp.exists() else {}
        log["anchor_plan_summary"] = rel(sp) if sp.exists() else None
        for t in log["turns"]:
            p = plans.get(t["turn_id"])
            t["plan"] = {k: p[k] for k in ("emote", "trigger_start_s", "trigger_end_s", "align_status", "status", "reason", "delay_s")} if p else None
            sp_ = (t["llm"]["parsed"] or {}).get("speech")
            dec = t["decision"]
            md.append(f"| {log['rank']} | {t['state']} | {sp_ or 'INVALID ' + str(t['llm']['errors'])[:80]} | {dec['effective']} | "
                      f"{dec['trigger_text'] or '-'} | {p['trigger_start_s'] if p and p['trigger_start_s'] is not None else '-'} | {p['status'] if p else 'no_wav'} |")
        lp.write_text(json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")
    (ROOT / "report.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md))


if __name__ == "__main__":
    main()
