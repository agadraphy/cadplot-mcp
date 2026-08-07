from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from cadplot_mcp.config import load_config
from cadplot_mcp.pilot import VISUAL_CHECKS, build_pilot_run_evidence
from cadplot_mcp.pipe_client import PluginConnectionError, get_plugin_status


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Collect one licensed AutoCAD pilot run without modifying the job or drawing."
    )
    parser.add_argument("manifest", help="Completed one-sheet staged job manifest.json.")
    parser.add_argument("--release", choices=("2016", "2025"), required=True)
    parser.add_argument("--approved-by", required=True, help="Authorized CAD approver name/role.")
    parser.add_argument(
        "--output", required=True, help="New local JSON file; existing files refuse."
    )
    parser.add_argument("--timeout-ms", type=int, default=2_000)
    parser.add_argument("--licensed", action="store_true")
    parser.add_argument("--authorized-test-asset", action="store_true")
    parser.add_argument("--restart-receipt-verified", action="store_true")
    parser.add_argument("--accept-visual-checks", action="store_true")
    args = parser.parse_args()

    config_path = os.environ.get("CADPLOT_CONFIG")
    if not config_path:
        return _fail("CADPLOT_CONFIG is not set.")
    output = Path(args.output).expanduser().resolve(strict=False)
    if output.exists():
        return _fail("Output already exists; pilot evidence is never overwritten.")
    visual_checks = {name: bool(args.accept_visual_checks) for name in sorted(VISUAL_CHECKS)}
    try:
        config = load_config(config_path)
        status = get_plugin_status(timeout_ms=args.timeout_ms)
        run = build_pilot_run_evidence(
            args.manifest,
            config,
            autocad_release=args.release,
            plugin_status=status,
            approved_by=args.approved_by,
            licensed=args.licensed,
            authorized_test_asset=args.authorized_test_asset,
            restart_receipt_verified=args.restart_receipt_verified,
            visual_checks=visual_checks,
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8") as stream:
            json.dump(run, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
    except (OSError, PluginConnectionError, ValueError) as exc:
        return _fail(str(exc))
    print(
        json.dumps(
            {
                "collected": True,
                "release": run["autocad_release"],
                "plan_id": run["plan_id"],
                "output": str(output),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _fail(message: str) -> int:
    print(json.dumps({"collected": False, "error": message}, ensure_ascii=False, indent=2))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
