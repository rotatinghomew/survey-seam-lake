"""Command-line inspection of a facts sketch.

Usage::

    python3 -m seam_survey.cli data/facts.json

Prints the registered zones, per-batch coverage diagnostics, any frame-level
problems and the gaps the sketch leaves open.  It never mutates the fixture.
"""

from __future__ import annotations

import argparse
import json
import sys

from .errors import LedgerError
from .facts import load_facts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect a seam-survey facts sketch")
    parser.add_argument("facts", help="path to facts.json")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args(argv)

    try:
        ledger, gaps = load_facts(args.facts)
    except (OSError, ValueError, LedgerError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    batches = sorted(ledger._batches)
    zone_frame = [issue.to_dict() for issue in ledger.zone_frame_issues()]
    diagnostics = {batch_id: ledger.diagnose_batch(batch_id).to_dict() for batch_id in batches}

    if args.json:
        print(
            json.dumps(
                {
                    "project": {
                        "id": ledger.project.id,
                        "common_network": f"{ledger.project.common_network_id}@"
                        f"{ledger.project.common_network_version}",
                    },
                    "zones": [ledger.zone(zone_id).to_dict() for zone_id in sorted(ledger._zones)],
                    "diagnostics": diagnostics,
                    "zone_frame_issues": zone_frame,
                    "gaps": gaps,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    print(f"project {ledger.project.id} common frame "
          f"{ledger.project.common_network_id}@{ledger.project.common_network_version}")
    print("zones:")
    for zone_id in sorted(ledger._zones):
        zone = ledger.zone(zone_id)
        print(f"  {zone.id:10s} owner={zone.owner!s:8s} area={zone.area_m2:.3f} m2")
    print("batches:")
    for batch_id, diag in diagnostics.items():
        state = "ok" if diag["ready"] else "BLOCKED"
        print(
            f"  {batch_id:14s} {state:8s} coverage={diag['covered_area_m2']:.3f} "
            f"gap={diag['gap_area_m2']:.3f} overhang={diag['overhang_area_m2']:.3f}"
        )
        for issue in diag["issues"]:
            print(f"      [{issue['severity']}] {issue['code']}: {issue['message']}")
    if zone_frame:
        print("frame issues:")
        for issue in zone_frame:
            print(f"  [{issue['severity']}] {issue['code']}: {issue['message']}")
    if gaps:
        print("sketch gaps:")
        for gap in gaps:
            print(f"  - {gap}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
