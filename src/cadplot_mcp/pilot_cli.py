from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from cadplot_mcp.config import load_config
from cadplot_mcp.pilot import (
    VISUAL_CHECKS,
    assemble_pilot_evidence,
    build_pilot_run_evidence,
    load_and_validate_pilot_evidence,
    validate_bundle_build_evidence,
)
from cadplot_mcp.pipe_client import PluginConnectionError, get_plugin_status

MAX_RUN_BYTES = 256 * 1024


def collect_main() -> int:
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
        return _fail("collected", "CADPLOT_CONFIG is not set.")
    output = Path(args.output).expanduser().resolve(strict=False)
    if output.exists():
        return _fail("collected", "Output already exists; pilot evidence is never overwritten.")
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
        _write_new_json(output, run)
    except (OSError, PluginConnectionError, ValueError) as exc:
        return _fail("collected", str(exc))
    print(
        json.dumps(
            {
                "collected": True,
                "release": run["autocad_release"],
                "build_commit": run["build_commit"],
                "plugin_sha256": run["plugin_sha256"],
                "runtime_series": run["runtime_series"],
                "plan_id": run["plan_id"],
                "output": str(output),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def assemble_main() -> int:
    parser = argparse.ArgumentParser(
        description="Assemble distinct 2016 and 2025 pilot runs into final validated evidence."
    )
    parser.add_argument("--run-2016", required=True)
    parser.add_argument("--run-2025", required=True)
    parser.add_argument("--bundle", required=True, help="Verified CadPlot bundle ZIP.")
    parser.add_argument(
        "--bundle-build-manifest",
        required=True,
        help="Matching bundle-build.json from the verified release root.",
    )
    parser.add_argument(
        "--output", required=True, help="New local JSON file; existing files refuse."
    )
    args = parser.parse_args()

    output = Path(args.output).expanduser().resolve(strict=False)
    if output.exists():
        return _fail(
            "assembled", "Output already exists; final pilot evidence is never overwritten."
        )
    try:
        bundle = Path(args.bundle).expanduser().resolve(strict=True)
        if not bundle.is_file():
            raise ValueError("Bundle path must be a file.")
        bundle_evidence = validate_bundle_build_evidence(bundle, args.bundle_build_manifest)
        evidence = assemble_pilot_evidence(
            _load_run(args.run_2016),
            _load_run(args.run_2025),
            **bundle_evidence,
        )
        _write_new_json(output, evidence)
    except (OSError, ValueError) as exc:
        return _fail("assembled", str(exc))
    print(
        json.dumps(
            {
                "assembled": True,
                "accepted_releases": ["2016", "2025"],
                "bundle_sha256": evidence["bundle_sha256"],
                "repository_commit": evidence["repository_commit"],
                "bundle_build_manifest_sha256": evidence["bundle_build_manifest_sha256"],
                "output": str(output),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def validate_main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate separate licensed AutoCAD 2016 and 2025 pilot evidence."
    )
    parser.add_argument("evidence", help="Path to the completed local pilot-evidence JSON file.")
    args = parser.parse_args()
    try:
        result = load_and_validate_pilot_evidence(args.evidence)
    except (OSError, ValueError) as exc:
        return _fail("valid", str(exc))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _load_run(value: str) -> Any:
    path = Path(value).expanduser().resolve(strict=True)
    if not path.is_file() or path.stat().st_size > MAX_RUN_BYTES:
        raise ValueError("Pilot run must be a file no larger than 256 KiB.")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Pilot run must be valid UTF-8 JSON.") from exc


def _write_new_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def _fail(key: str, message: str) -> int:
    print(json.dumps({key: False, "error": message}, ensure_ascii=False, indent=2))
    return 1
