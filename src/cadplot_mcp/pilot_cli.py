from __future__ import annotations

import argparse
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cadplot_mcp.config import load_config
from cadplot_mcp.fingerprint import read_stable_bytes
from cadplot_mcp.pilot import (
    VISUAL_CHECKS,
    assemble_pilot_evidence,
    build_batch_recovery_evidence,
    build_pilot_run_evidence,
    load_and_validate_pilot_evidence,
    validate_bundle_build_evidence,
)
from cadplot_mcp.pipe_client import PluginConnectionError, get_plugin_status

MAX_RUN_BYTES = 256 * 1024


@dataclass(frozen=True)
class _RunInputSnapshot:
    path: Path
    label: str
    content: bytes
    fingerprint: dict[str, Any]
    value: Any

    def require_unchanged(self) -> None:
        try:
            content, fingerprint = read_stable_bytes(
                self.path,
                min_bytes=2,
                max_bytes=MAX_RUN_BYTES,
                label=self.label,
            )
        except ValueError as exc:
            raise ValueError(f"{self.label} changed during pilot assembly.") from exc
        if content != self.content or fingerprint != self.fingerprint:
            raise ValueError(f"{self.label} changed during pilot assembly.")


def collect_main() -> int:
    parser = argparse.ArgumentParser(
        description="Collect one licensed AutoCAD pilot run without modifying the job or drawing."
    )
    parser.add_argument("manifest", help="Completed one-sheet staged job manifest.json.")
    parser.add_argument("--release", choices=("2016", "2025"), required=True)
    parser.add_argument("--approved-by", required=True, help="Authorized CAD approver name/role.")
    parser.add_argument(
        "--reference-pdf",
        required=True,
        help="Authorized one-page office reference PDF under an allowed root.",
    )
    parser.add_argument(
        "--workstation-preflight",
        required=True,
        help="Exact no-overwrite read-only workstation preflight JSON for this release.",
    )
    parser.add_argument(
        "--publish-session",
        required=True,
        help="Exact no-overwrite publish-session JSON bound to the read-only preflight.",
    )
    parser.add_argument(
        "--output", required=True, help="New local JSON file; existing files refuse."
    )
    parser.add_argument("--timeout-ms", type=int, default=2_000)
    parser.add_argument("--licensed", action="store_true")
    parser.add_argument("--authorized-test-asset", action="store_true")
    parser.add_argument("--restart-receipt-verified", action="store_true")
    for check in sorted(VISUAL_CHECKS):
        parser.add_argument(
            f"--accept-{check.replace('_', '-')}",
            action="store_true",
            help=f"Explicitly attest the {check.replace('_', ' ')} comparison.",
        )
    args = parser.parse_args()

    config_path = os.environ.get("CADPLOT_CONFIG")
    if not config_path:
        return _fail("collected", "CADPLOT_CONFIG is not set.")
    output = Path(args.output).expanduser().resolve(strict=False)
    if output.exists():
        return _fail("collected", "Output already exists; pilot evidence is never overwritten.")
    visual_checks = {
        name: bool(getattr(args, f"accept_{name}")) for name in sorted(VISUAL_CHECKS)
    }
    try:
        config = load_config(config_path)
        workstation_preflight = _load_run(
            args.workstation_preflight,
            label=f"AutoCAD {args.release} workstation preflight",
        )
        publish_session = _load_run(
            args.publish_session,
            label=f"AutoCAD {args.release} publish session",
        )
        workstation_gates = {
            "read_only": {
                "evidence_sha256": hashlib.sha256(
                    workstation_preflight.content
                ).hexdigest(),
                "record": workstation_preflight.value,
            },
            "publish": {
                "evidence_sha256": hashlib.sha256(publish_session.content).hexdigest(),
                "record": publish_session.value,
            },
        }
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
            reference_pdf=args.reference_pdf,
            workstation_gates=workstation_gates,
        )
        workstation_preflight.require_unchanged()
        publish_session.require_unchanged()
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
                "workstation_preflight_sha256": run["workstation_gates"]["read_only"][
                    "evidence_sha256"
                ],
                "publish_session_sha256": run["workstation_gates"]["publish"][
                    "evidence_sha256"
                ],
                "plan_id": run["plan_id"],
                "published_pdf_sha256": run["published_pdf"]["sha256"],
                "reference_pdf_sha256": run["visual_reference"]["sha256"],
                "template_asset_count": len(run["template_assets"]),
                "output": str(output),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def collect_recovery_main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Collect a bounded licensed AutoCAD batch-recovery record after restart "
            "without modifying jobs or drawings."
        )
    )
    parser.add_argument(
        "manifests",
        nargs="+",
        help="Two to twenty completed staged-job manifest.json files.",
    )
    parser.add_argument("--release", choices=("2016", "2025"), required=True)
    parser.add_argument("--approved-by", required=True, help="Authorized CAD approver name/role.")
    parser.add_argument(
        "--output", required=True, help="New local JSON file; existing files refuse."
    )
    parser.add_argument("--timeout-ms", type=int, default=2_000)
    parser.add_argument("--licensed", action="store_true")
    parser.add_argument("--authorized-test-assets", action="store_true")
    parser.add_argument("--restart-verified", action="store_true")
    args = parser.parse_args()

    config_path = os.environ.get("CADPLOT_CONFIG")
    if not config_path:
        return _fail("collected", "CADPLOT_CONFIG is not set.")
    output = Path(args.output).expanduser().resolve(strict=False)
    if output.exists():
        return _fail(
            "collected", "Output already exists; recovery evidence is never overwritten."
        )
    try:
        config = load_config(config_path)
        status = get_plugin_status(timeout_ms=args.timeout_ms)
        recovery = build_batch_recovery_evidence(
            args.manifests,
            config,
            autocad_release=args.release,
            plugin_status=status,
            approved_by=args.approved_by,
            licensed=args.licensed,
            authorized_test_assets=args.authorized_test_assets,
            restart_verified=args.restart_verified,
        )
        _write_new_json(output, recovery)
    except (OSError, PluginConnectionError, ValueError) as exc:
        return _fail("collected", str(exc))
    print(
        json.dumps(
            {
                "collected": True,
                "release": recovery["autocad_release"],
                "build_commit": recovery["build_commit"],
                "plugin_sha256": recovery["plugin_sha256"],
                "report_page_id": recovery["report_page_id"],
                "job_count": recovery["job_count"],
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
    parser.add_argument("--recovery-2016", required=True)
    parser.add_argument("--recovery-2025", required=True)
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
        run_2016 = _load_run(args.run_2016, label="AutoCAD 2016 pilot run")
        run_2025 = _load_run(args.run_2025, label="AutoCAD 2025 pilot run")
        recovery_2016 = _load_run(
            args.recovery_2016, label="AutoCAD 2016 recovery record"
        )
        recovery_2025 = _load_run(
            args.recovery_2025, label="AutoCAD 2025 recovery record"
        )
        inputs = (run_2016, run_2025, recovery_2016, recovery_2025)
        bundle = Path(args.bundle).expanduser().absolute()
        bundle_evidence = validate_bundle_build_evidence(bundle, args.bundle_build_manifest)
        evidence = assemble_pilot_evidence(
            run_2016.value,
            run_2025.value,
            recovery_2016.value,
            recovery_2025.value,
            **bundle_evidence,
        )
        for item in inputs:
            item.require_unchanged()
        _write_new_json(output, evidence)
    except (OSError, ValueError) as exc:
        return _fail("assembled", str(exc))
    print(
        json.dumps(
            {
                "assembled": True,
                "accepted_releases": ["2016", "2025"],
                "batch_recovery_releases": ["2016", "2025"],
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


def _load_run(value: str, *, label: str) -> _RunInputSnapshot:
    path = Path(value).expanduser().absolute()
    content, fingerprint = read_stable_bytes(
        path,
        min_bytes=2,
        max_bytes=MAX_RUN_BYTES,
        label=label,
    )
    try:
        raw = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} must be valid UTF-8 JSON.") from exc
    return _RunInputSnapshot(path, label, content, fingerprint, raw)


def _write_new_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def _fail(key: str, message: str) -> int:
    print(json.dumps({key: False, "error": message}, ensure_ascii=False, indent=2))
    return 1
