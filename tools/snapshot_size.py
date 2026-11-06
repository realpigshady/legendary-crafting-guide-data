#!/usr/bin/env python3
"""Snapshot size check (CI): files and bytes per top-level folder of snapshot/.

Prints a table, appends it to $GITHUB_STEP_SUMMARY when set, and warns (GitHub
annotation + a line in warnings.txt when --warnings is given) once the snapshot is
over the budget. Never fails the build: the snapshot is the outage fallback and
must keep being committed; the warning is the cue to squash old snapshot history.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

BUDGET_MB = 200


def measure(root: Path) -> dict[str, tuple[int, int]]:
    """{folder (or '.' for top-level files): (files, bytes)}"""
    out: dict[str, tuple[int, int]] = {}
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(root).parts
        key = rel[0] if len(rel) > 1 else "."
        n, b = out.get(key, (0, 0))
        out[key] = (n + 1, b + p.stat().st_size)
    return out


def report(sizes: dict[str, tuple[int, int]], budget_mb: float) -> tuple[str, bool]:
    total_n = sum(n for n, _ in sizes.values())
    total_b = sum(b for _, b in sizes.values())
    lines = ["| snapshot/ | files | MB |", "|---|---:|---:|"]
    for k, (n, b) in sorted(sizes.items(), key=lambda kv: -kv[1][1]):
        lines.append(f"| {k} | {n:,} | {b / 1e6:.1f} |")
    lines.append(f"| **total** | **{total_n:,}** | **{total_b / 1e6:.1f}** |")
    return "\n".join(lines), total_b > budget_mb * 1e6


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", type=Path, default=Path(__file__).resolve().parent.parent / "snapshot")
    ap.add_argument("--budget-mb", type=float, default=BUDGET_MB)
    ap.add_argument("--warnings", type=Path, help="append the over-budget line to this warnings.txt")
    args = ap.parse_args()
    sizes = measure(args.snapshot)
    table, over = report(sizes, args.budget_mb)
    print(table)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write("### Snapshot size\n\n" + table + "\n")
    if over:
        total = sum(b for _, b in sizes.values()) / 1e6
        msg = f"snapshot/ is {total:.0f} MB, over the {args.budget_mb:.0f} MB budget: squash old snapshot history"
        print(f"::warning::{msg}")
        if args.warnings and args.warnings.exists():
            with open(args.warnings, "a", encoding="utf-8") as f:
                f.write(f"SIZE: {msg}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
