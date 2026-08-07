from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

SHA256 = re.compile(r"[0-9a-f]{64}")
PLAN_ID = re.compile(r"sha256:[0-9a-f]{64}")
COMMIT = re.compile(r"[0-9a-f]{40}")
VISUAL_CHECKS = {
    "orientation",
    "crop",
    "viewport_scale",
    "lineweights",
    "plot_style",
    "fonts",
    "title_block",
}
RUN_FIELDS = {
    "autocad_release",
    "product",
    "adapter",
    "licensed",
    "authorized_test_asset",
    "plan_id",
    "manifest_sha256",
    "receipt_manifest_sha256",
    "receipt_state",
    "source_sha256_before",
    "source_sha256_after",
    "staged_sha256_before",
    "staged_sha256_after",
    "pdf_sha256",
    "publish_verified",
    "restart_receipt_verified",
    "visual_checks",
    "approved_by",
    "completed_utc",
}
EXPECTED = {
    "2016": {"adapter": "autocad-2016-net45", "acadver": "R20.1"},
    "2025": {"adapter": "autocad-2025-net8", "acadver": "R25.0"},
}


def validate_pilot_evidence(raw: Any) -> dict[str, Any]:
    """Require separate, complete licensed-workstation evidence for 2016 and 2025."""
    if not isinstance(raw, dict) or set(raw) != {
        "schema_version",
        "repository_commit",
        "bundle_sha256",
        "runs",
    }:
        raise ValueError("Pilot evidence must contain exactly the documented top-level fields.")
    if raw["schema_version"] != 1:
        raise ValueError("Unsupported pilot evidence schema.")
    if not isinstance(raw["repository_commit"], str) or not COMMIT.fullmatch(
        raw["repository_commit"]
    ):
        raise ValueError("repository_commit must be a full lowercase Git commit SHA.")
    _require_digest(raw["bundle_sha256"], "bundle_sha256")
    runs = raw["runs"]
    if not isinstance(runs, list) or len(runs) != 2:
        raise ValueError("Pilot evidence must contain exactly one 2016 and one 2025 run.")
    validated = [_validate_run(run) for run in runs]
    releases = [run["autocad_release"] for run in validated]
    if sorted(releases) != ["2016", "2025"]:
        raise ValueError("Pilot evidence must contain distinct AutoCAD 2016 and 2025 runs.")
    return {
        "valid": True,
        "schema_version": 1,
        "repository_commit": raw["repository_commit"],
        "bundle_sha256": raw["bundle_sha256"],
        "accepted_releases": sorted(releases),
    }


def load_and_validate_pilot_evidence(path: str | Path) -> dict[str, Any]:
    evidence = Path(path).expanduser().resolve(strict=True)
    if evidence.stat().st_size > 256 * 1024:
        raise ValueError("Pilot evidence exceeds the 256 KiB limit.")
    try:
        raw = json.loads(evidence.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Pilot evidence must be valid UTF-8 JSON.") from exc
    return validate_pilot_evidence(raw)


def _validate_run(run: Any) -> dict[str, Any]:
    if not isinstance(run, dict) or set(run) != RUN_FIELDS:
        raise ValueError("Each pilot run must contain exactly the documented fields.")
    release = run["autocad_release"]
    if release not in EXPECTED:
        raise ValueError("autocad_release must be 2016 or 2025.")
    expected = EXPECTED[release]
    if run["adapter"] != expected["adapter"]:
        raise ValueError(f"AutoCAD {release} adapter identity mismatch.")
    product = run["product"]
    if (
        not isinstance(product, str)
        or f"AutoCAD {release}" not in product
        or f"ACADVER {expected['acadver']}" not in product
    ):
        raise ValueError(f"AutoCAD {release} product/ACADVER evidence mismatch.")
    for flag in (
        "licensed",
        "authorized_test_asset",
        "publish_verified",
        "restart_receipt_verified",
    ):
        if run[flag] is not True:
            raise ValueError(f"AutoCAD {release} requires {flag}=true.")
    if not isinstance(run["plan_id"], str) or not PLAN_ID.fullmatch(run["plan_id"]):
        raise ValueError(f"AutoCAD {release} plan_id is invalid.")
    for field in (
        "manifest_sha256",
        "receipt_manifest_sha256",
        "source_sha256_before",
        "source_sha256_after",
        "staged_sha256_before",
        "staged_sha256_after",
        "pdf_sha256",
    ):
        _require_digest(run[field], field)
    if run["manifest_sha256"] != run["receipt_manifest_sha256"]:
        raise ValueError(f"AutoCAD {release} receipt is not bound to the approved manifest.")
    if run["source_sha256_before"] != run["source_sha256_after"]:
        raise ValueError(f"AutoCAD {release} source DWG changed during the pilot.")
    if run["staged_sha256_before"] != run["staged_sha256_after"]:
        raise ValueError(f"AutoCAD {release} staged DWG changed during the pilot.")
    if run["receipt_state"] != "succeeded":
        raise ValueError(f"AutoCAD {release} receipt_state must be succeeded.")
    checks = run["visual_checks"]
    if not isinstance(checks, dict) or set(checks) != VISUAL_CHECKS:
        raise ValueError(f"AutoCAD {release} visual_checks fields are incomplete.")
    if any(value is not True for value in checks.values()):
        raise ValueError(f"AutoCAD {release} visual acceptance is incomplete.")
    approved_by = run["approved_by"]
    if not isinstance(approved_by, str) or not approved_by.strip() or len(approved_by) > 200:
        raise ValueError(f"AutoCAD {release} approved_by is invalid.")
    completed = run["completed_utc"]
    try:
        parsed = datetime.fromisoformat(completed)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"AutoCAD {release} completed_utc is invalid.") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"AutoCAD {release} completed_utc must include a timezone.")
    return run


def _require_digest(value: Any, field: str) -> None:
    if not isinstance(value, str) or not SHA256.fullmatch(value):
        raise ValueError(f"{field} must be a lowercase SHA-256 digest.")
