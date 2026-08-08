from __future__ import annotations

import argparse
import json
import os

from cadplot_mcp.environment import diagnose_environment


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Diagnose CadPlot config, AutoCAD inspection, and local plug-in connectivity."
    )
    parser.add_argument(
        "--config",
        default=os.environ.get("CADPLOT_CONFIG"),
        help="Configuration path; defaults to CADPLOT_CONFIG.",
    )
    parser.add_argument(
        "--mode",
        choices=("config", "inspection", "full"),
        default="full",
        help="config=filesystem only, inspection=plus COM, full=plus local plug-in.",
    )
    parser.add_argument("--timeout-ms", type=int, default=2_000)
    args = parser.parse_args()
    try:
        report = diagnose_environment(args.config, mode=args.mode, timeout_ms=args.timeout_ms)
    except ValueError as exc:
        report = {
            "schema_version": 1,
            "mode": args.mode,
            "ready": False,
            "errors": [str(exc)],
        }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
