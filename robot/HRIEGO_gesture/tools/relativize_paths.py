#!/usr/bin/env python3
"""Rewrite machine-specific absolute paths in text artifacts (JSON/JSONL/CSV/MD/TXT/YML) so they can be committed.

  python tools/relativize_paths.py [--dry-run] [--root DIR] [FILE ...]

* paths inside this package (<x2_ultra>/HRIEGO_gesture/...)  -> relative to the HRIEGO_gesture root ("visits_v2/vip/wav/a.wav")
* other repos / tools                                        -> placeholders ($X2_ULTRA_ROOT, $LAPLACE_ROOT, $SEMANTIC_CORPOREA_ROOT,
                                                                $X2_TELEOP_ROOT, $ROBOTS_ROOT, $HRIEGO_PY_PHYSICALAI, $HRIEGO_PY_GMR, $CONDA_BASE, $HOME)
* temp/session dirs (/tmp/claude-*/...)                       -> $TMPDIR
Placeholders are expanded by paths.resolve() (os.path.expandvars) when a stored path is read back.
Without FILE arguments it processes the files git would commit under the root (git ls-files -c -o --exclude-standard),
skipping the hand-written docs listed in SKIP. Idempotent. Text-level substitution: formatting and key order are untouched.
"""
import argparse, re, subprocess, sys
from collections import Counter
from pathlib import Path

HOME = "/ho" + "me/luis"          # split so this file itself holds no machine path
SKIP = {"README.md", "README.proposed.md", "COMMIT_PREP.md", "CHANGES_FOR_README.md"}
SKIP_DIRS = {"requirements"}
EXTS = {".json", ".jsonl", ".csv", ".md", ".txt", ".yml", ".yaml"}
# order matters: most specific first. (regex, replacement)
RULES = [
    ("pkg_dir_slash", re.escape(HOME + "/GitHub/x2_ultra/HRIEGO_gesture/"), ""),
    ("pkg_dir", re.escape(HOME + "/GitHub/x2_ultra/HRIEGO_gesture") + r"(?![\w.-])", "."),
    ("x2_ultra_teleop", re.escape(HOME + "/GitHub/x2_ultra-real-robot-teleop"), "$X2_TELEOP_ROOT"),
    ("x2_ultra", re.escape(HOME + "/GitHub/x2_ultra"), "$X2_ULTRA_ROOT"),
    # the c08 table is vendored byte-identically (same sha256) as config/preset_latency_c08.json
    ("latency_c08", r"(?:" + re.escape(HOME + "/GitHub/Laplace") + r"|\$LAPLACE_ROOT)/experiments/RCP-01/runs/20261021_rcp01c08_preset_latency_twin/metrics/preset_latency\.json", "config/preset_latency_c08.json"),
    ("laplace", re.escape(HOME + "/GitHub/Laplace"), "$LAPLACE_ROOT"),
    ("semantic", re.escape(HOME + "/GitHub/SemanticCorporeaHRI"), "$SEMANTIC_CORPOREA_ROOT"),
    ("py_physicalai", re.escape(HOME + "/anaconda3/envs/physicalai/bin/python"), "$HRIEGO_PY_PHYSICALAI"),
    ("py_gmr", re.escape(HOME + "/anaconda3/envs/gmr/bin/python"), "$HRIEGO_PY_GMR"),
    ("conda_base", re.escape(HOME + "/anaconda3"), "$CONDA_BASE"),
    ("robots", re.escape(HOME + "/robots"), "$ROBOTS_ROOT"),
    ("tmp_session", r"/tmp/claude-\d+/[^\s\"'\\,;)]*?/scratchpad", "$TMPDIR"),
    ("tmp_claude_rest", r"/tmp/claude-\d+", "$TMPDIR"),
    ("home_rest", re.escape(HOME), "$HOME"),
]
RULES = [(n, re.compile(p), r) for n, p, r in RULES]


def candidates(root):
    out = subprocess.run(["git", "-C", str(root), "ls-files", "-c", "-o", "--exclude-standard", "."], capture_output=True, text=True, check=True).stdout
    return [root / l for l in out.splitlines() if l]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="*")
    ap.add_argument("--root", default=str(Path(__file__).resolve().parent.parent))
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    root = Path(a.root).resolve()
    files = [Path(f) for f in a.files] or candidates(root)
    stats, changed, scanned = Counter(), 0, 0
    for f in files:
        rel = f.resolve().relative_to(root) if f.is_absolute() or f.exists() else f
        if f.suffix not in EXTS or f.name in SKIP or (rel.parts and rel.parts[0] in SKIP_DIRS) or f.name == "relativize_paths.py":
            continue
        try:
            t = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError):
            continue
        scanned += 1
        n0 = t
        for name, rx, rep in RULES:
            t, k = rx.subn(rep, t)
            stats[name] += k
        if t != n0:
            changed += 1
            if not a.dry_run:
                f.write_text(t, encoding="utf-8")
    print(f"scanned={scanned} changed={changed} {'(dry run)' if a.dry_run else ''}")
    for k, v in stats.items():
        if v:
            print(f"  {k:16s} {v}")


if __name__ == "__main__":
    sys.exit(main())
