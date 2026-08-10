from __future__ import annotations

import hashlib
import json
import math
import re
import zipfile
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Any

from pypdf import PdfReader
from pypdf.errors import LimitReachedError, PdfReadError

from cadplot_mcp.audit import (
    audit_publish_outputs_snapshot,
    build_receipt_output_digest,
    pdf_page_marking_evidence,
)
from cadplot_mcp.config import CadPlotConfig
from cadplot_mcp.fingerprint import fingerprint_file
from cadplot_mcp.reporting import JOB_ID_PATTERN, build_publish_operations_report
from cadplot_mcp.security import FILE_ATTRIBUTE_REPARSE_POINT

SHA256 = re.compile(r"[0-9a-f]{64}")
PLAN_ID = re.compile(r"sha256:[0-9a-f]{64}")
COMMIT = re.compile(r"[0-9a-f]{40}")
MAX_REFERENCE_PDF_BYTES = 128 * 1024 * 1024
VISUAL_CHECKS = {
    "orientation",
    "crop",
    "viewport_scale",
    "lineweights",
    "plot_style",
    "fonts",
    "title_block",
}
QUEUE_AUTHENTICATION_SCHEME = "windows-dpapi-current-user+hmac-sha256-v1"
RUN_FIELDS = {
    "autocad_release",
    "product",
    "adapter",
    "build_commit",
    "plugin_sha256",
    "runtime_series",
    "queue_authentication",
    "licensed",
    "authorized_test_asset",
    "plan_id",
    "manifest_sha256",
    "receipt_manifest_sha256",
    "receipt_state",
    "receipt_output_count",
    "receipt_outputs_sha256",
    "receipt_output_binding_verified",
    "source_sha256_before",
    "source_sha256_after",
    "staged_sha256_before",
    "staged_sha256_after",
    "template_assets",
    "published_pdf",
    "visual_reference",
    "publish_verified",
    "restart_receipt_verified",
    "visual_checks",
    "approved_by",
    "completed_utc",
}
RECOVERY_FIELDS = {
    "autocad_release",
    "product",
    "adapter",
    "build_commit",
    "plugin_sha256",
    "runtime_series",
    "queue_authentication",
    "licensed",
    "authorized_test_assets",
    "restart_verified",
    "report_page_id",
    "workspace_report_complete",
    "workspace_job_count",
    "job_count",
    "jobs",
    "approved_by",
    "completed_utc",
}
RECOVERY_JOB_FIELDS = {
    "job_id",
    "plan_id",
    "manifest_sha256",
    "receipt_manifest_sha256",
    "receipt_state",
    "receipt_output_count",
    "receipt_outputs_sha256",
    "receipt_output_binding_verified",
    "source_sha256_before",
    "source_sha256_after",
    "staged_sha256_before",
    "staged_sha256_after",
    "outputs_complete",
    "publish_verified",
}
VISUAL_REFERENCE_FIELDS = {
    "sha256",
    "size_bytes",
    "page_count",
    "page_width_mm",
    "page_height_mm",
    "comparison_tolerance_mm",
}
PDF_EVIDENCE_FIELDS = {
    "sheet_index",
    "file",
    "sha256",
    "size_bytes",
    "page_count",
    "page_width_mm",
    "page_height_mm",
}
TEMPLATE_ASSET_FIELDS = {
    "id",
    "layout",
    "page_setup",
    "size_bytes",
    "approved_sha256",
    "source_sha256_after",
    "staged_sha256_after",
}
EXPECTED = {
    "2016": {"adapter": "autocad-2016-net45", "acadver": "R20.1"},
    "2025": {"adapter": "autocad-2025-net8", "acadver": "R25.0"},
}
BUNDLE_BUILD_FIELDS = {
    "schema_version",
    "exact_commit",
    "package_version",
    "created_utc",
    "api_identity",
    "bundle",
    "source_tree_audit_passed",
    "bundle_verification_passed",
    "archive_audit_passed",
    "matching_sdk_bundle_built",
    "company_assets_copied",
    "autodesk_binaries_included",
    "autocad_launched",
    "live_publish_proven",
}
BUNDLE_FILES = {
    "LICENSE",
    "PackageContents.xml",
    "Contents/Windows/2016/CadPlotMcp.AutoCAD2016.dll",
    "Contents/Windows/2016/CadPlotMcp.Core.dll",
    "Contents/Windows/2025/CadPlotMcp.AutoCAD2025.dll",
    "Contents/Windows/2025/CadPlotMcp.Core.dll",
}
ADAPTER_PATHS = {
    "2016": "Contents/Windows/2016/CadPlotMcp.AutoCAD2016.dll",
    "2025": "Contents/Windows/2025/CadPlotMcp.AutoCAD2025.dll",
}


def build_pilot_run_evidence(
    manifest_value: str | Path,
    config: CadPlotConfig,
    *,
    autocad_release: str,
    plugin_status: dict[str, Any],
    approved_by: str,
    licensed: bool,
    authorized_test_asset: bool,
    restart_receipt_verified: bool,
    visual_checks: dict[str, bool],
    reference_pdf: str | Path,
    completed_utc: str | None = None,
) -> dict[str, Any]:
    """Build one read-only live-pilot record from cross-checked job evidence."""
    if autocad_release not in EXPECTED:
        raise ValueError("autocad_release must be 2016 or 2025.")
    for field in (
        "ok",
        "readOnly",
        "workspaceConfigured",
        "publishEnabled",
        "runtimeSupported",
    ):
        if plugin_status.get(field) is not True:
            raise ValueError(f"Live AutoCAD status requires {field}=true.")
    if plugin_status.get("queueAuthentication") != QUEUE_AUTHENTICATION_SCHEME:
        raise ValueError("Live AutoCAD status requires authenticated durable queue intent.")
    report, snapshot = audit_publish_outputs_snapshot(manifest_value, config)
    if report.get("source_unchanged") is not True:
        raise ValueError("Pilot source DWG changed after approval.")
    if report.get("publish_verified") is not True:
        raise ValueError("Pilot run requires publish_verified=true output evidence.")
    if len(report.get("outputs", [])) != 1:
        raise ValueError("Licensed pilot evidence must come from exactly one output sheet.")

    manifest = snapshot.manifest
    source = config.path_policy.require_allowed(manifest["source_drawing"], suffix=".dwg")
    staged = Path(manifest["staged_drawing"]).resolve(strict=True)
    source_before = manifest["source_fingerprint"]["sha256"]
    source_after = fingerprint_file(source, label="Pilot source DWG")
    staged_after = fingerprint_file(staged, label="Pilot staged DWG")
    receipt = report["execution_receipt"]["receipt"]
    completed = completed_utc or datetime.now(UTC).isoformat()
    output = report["outputs"][0]
    run = {
        "autocad_release": autocad_release,
        "product": plugin_status.get("product"),
        "adapter": plugin_status.get("adapter"),
        "build_commit": plugin_status.get("buildCommit"),
        "plugin_sha256": plugin_status.get("pluginSha256"),
        "runtime_series": plugin_status.get("runtimeSeries"),
        "queue_authentication": plugin_status.get("queueAuthentication"),
        "licensed": licensed,
        "authorized_test_asset": authorized_test_asset,
        "plan_id": manifest["plan_id"],
        "manifest_sha256": snapshot.sha256,
        "receipt_manifest_sha256": receipt["manifest_sha256"],
        "receipt_state": receipt["state"],
        "receipt_output_count": receipt["output_count"],
        "receipt_outputs_sha256": receipt["outputs_sha256"],
        "receipt_output_binding_verified": report["receipt_output_binding_verified"],
        "source_sha256_before": source_before,
        "source_sha256_after": source_after["sha256"],
        "staged_sha256_before": source_before,
        "staged_sha256_after": staged_after["sha256"],
        "template_assets": _collect_template_asset_evidence(manifest, config),
        "published_pdf": _collect_published_pdf(output),
        "visual_reference": _collect_visual_reference(
            reference_pdf,
            config,
            output,
        ),
        "publish_verified": report["publish_verified"],
        "restart_receipt_verified": restart_receipt_verified,
        "visual_checks": visual_checks,
        "approved_by": approved_by,
        "completed_utc": completed,
    }
    result = validate_pilot_run_evidence(run)
    snapshot.require_unchanged("pilot evidence collection")
    return result


def validate_pilot_run_evidence(raw: Any) -> dict[str, Any]:
    """Validate one licensed-workstation run before it is assembled with the other release."""
    return _validate_run(raw)


def build_batch_recovery_evidence(
    manifest_values: list[str | Path],
    config: CadPlotConfig,
    *,
    autocad_release: str,
    plugin_status: dict[str, Any],
    approved_by: str,
    licensed: bool,
    authorized_test_assets: bool,
    restart_verified: bool,
    completed_utc: str | None = None,
) -> dict[str, Any]:
    """Collect a path-redacted small-batch recovery record after an AutoCAD restart."""
    if not 2 <= len(manifest_values) <= 20:
        raise ValueError("Batch recovery evidence requires 2 to 20 completed manifests.")
    if autocad_release not in EXPECTED:
        raise ValueError("autocad_release must be 2016 or 2025.")
    for field in (
        "ok",
        "readOnly",
        "workspaceConfigured",
        "publishEnabled",
        "runtimeSupported",
    ):
        if plugin_status.get(field) is not True:
            raise ValueError(f"Live AutoCAD status requires {field}=true.")
    if plugin_status.get("queueAuthentication") != QUEUE_AUTHENTICATION_SCHEME:
        raise ValueError("Live AutoCAD status requires authenticated durable queue intent.")

    manifest_paths = [
        Path(value).expanduser().resolve(strict=True) for value in manifest_values
    ]
    if len(set(manifest_paths)) != len(manifest_paths):
        raise ValueError("Batch recovery manifests must be distinct.")

    operations = build_publish_operations_report(config, limit=50)
    if operations["has_more"] is not False:
        raise ValueError(
            "Batch recovery requires an isolated workspace with at most 50 staged jobs."
        )
    if operations["processed"] != len(manifest_paths):
        raise ValueError(
            "Batch recovery requires an isolated workspace containing exactly the approved batch."
        )
    indexed = {item["job_id"]: item for item in operations["items"]}
    jobs: list[dict[str, Any]] = []
    for manifest_path in manifest_paths:
        report, snapshot = audit_publish_outputs_snapshot(manifest_path, config)
        manifest = snapshot.manifest
        job_id = manifest["job_id"]
        item = indexed.get(job_id)
        if (
            item is None
            or item.get("status") != "complete"
            or item.get("publish_verified") is not True
            or Path(str(item.get("manifest_path", ""))).resolve(strict=True)
            != manifest_path
            or item.get("manifest_sha256") != snapshot.sha256
        ):
            raise ValueError(
                f"Batch recovery job {job_id!r} is not complete in the current operations report."
            )
        if (
            report.get("outputs_complete") is not True
            or report.get("receipt_output_binding_verified") is not True
            or report.get("publish_verified") is not True
        ):
            raise ValueError(
                f"Batch recovery job {job_id!r} lacks verified receipt-bound outputs."
            )
        receipt = report["execution_receipt"]["receipt"]
        source_before = manifest["source_fingerprint"]["sha256"]
        source = config.path_policy.require_allowed(
            manifest["source_drawing"], suffix=".dwg"
        )
        staged = Path(manifest["staged_drawing"]).resolve(strict=True)
        source_after = fingerprint_file(source, label="Recovery source DWG")
        staged_after = fingerprint_file(staged, label="Recovery staged DWG")
        jobs.append(
            {
                "job_id": job_id,
                "plan_id": manifest["plan_id"],
                "manifest_sha256": snapshot.sha256,
                "receipt_manifest_sha256": receipt["manifest_sha256"],
                "receipt_state": receipt["state"],
                "receipt_output_count": receipt["output_count"],
                "receipt_outputs_sha256": receipt["outputs_sha256"],
                "receipt_output_binding_verified": report[
                    "receipt_output_binding_verified"
                ],
                "source_sha256_before": source_before,
                "source_sha256_after": source_after["sha256"],
                "staged_sha256_before": source_before,
                "staged_sha256_after": staged_after["sha256"],
                "outputs_complete": report["outputs_complete"],
                "publish_verified": report["publish_verified"],
            }
        )
        snapshot.require_unchanged("batch recovery evidence collection")

    jobs.sort(key=lambda item: item["job_id"])
    recovery = {
        "autocad_release": autocad_release,
        "product": plugin_status.get("product"),
        "adapter": plugin_status.get("adapter"),
        "build_commit": plugin_status.get("buildCommit"),
        "plugin_sha256": plugin_status.get("pluginSha256"),
        "runtime_series": plugin_status.get("runtimeSeries"),
        "queue_authentication": plugin_status.get("queueAuthentication"),
        "licensed": licensed,
        "authorized_test_assets": authorized_test_assets,
        "restart_verified": restart_verified,
        "report_page_id": operations["report_page_id"],
        "workspace_report_complete": True,
        "workspace_job_count": operations["processed"],
        "job_count": len(jobs),
        "jobs": jobs,
        "approved_by": approved_by,
        "completed_utc": completed_utc or datetime.now(UTC).isoformat(),
    }
    return validate_batch_recovery_evidence(recovery)


def validate_batch_recovery_evidence(raw: Any) -> dict[str, Any]:
    """Validate one release's bounded post-restart batch recovery record."""
    if not isinstance(raw, dict) or set(raw) != RECOVERY_FIELDS:
        raise ValueError("Batch recovery evidence fields are incomplete.")
    release = raw["autocad_release"]
    if release not in EXPECTED:
        raise ValueError("autocad_release must be 2016 or 2025.")
    expected = EXPECTED[release]
    if raw["adapter"] != expected["adapter"]:
        raise ValueError(f"AutoCAD {release} recovery adapter identity mismatch.")
    if raw["runtime_series"] != expected["acadver"]:
        raise ValueError(f"AutoCAD {release} recovery runtime series mismatch.")
    if raw["queue_authentication"] != QUEUE_AUTHENTICATION_SCHEME:
        raise ValueError(f"AutoCAD {release} recovery queue authentication mismatch.")
    product = raw["product"]
    if (
        not isinstance(product, str)
        or f"AutoCAD {release}" not in product
        or f"ACADVER {expected['acadver']}" not in product
    ):
        raise ValueError(f"AutoCAD {release} recovery product/ACADVER mismatch.")
    if not isinstance(raw["build_commit"], str) or not COMMIT.fullmatch(
        raw["build_commit"]
    ):
        raise ValueError(f"AutoCAD {release} recovery build_commit is invalid.")
    _require_digest(raw["plugin_sha256"], "recovery.plugin_sha256")
    for flag in (
        "licensed",
        "authorized_test_assets",
        "restart_verified",
        "workspace_report_complete",
    ):
        if raw[flag] is not True:
            raise ValueError(f"AutoCAD {release} recovery requires {flag}=true.")
    report_page_id = raw["report_page_id"]
    if not isinstance(report_page_id, str) or not PLAN_ID.fullmatch(report_page_id):
        raise ValueError(f"AutoCAD {release} recovery report_page_id is invalid.")
    workspace_job_count = raw["workspace_job_count"]
    job_count = raw["job_count"]
    if (
        not isinstance(workspace_job_count, int)
        or isinstance(workspace_job_count, bool)
        or not isinstance(job_count, int)
        or isinstance(job_count, bool)
        or not 2 <= job_count <= 20
        or workspace_job_count != job_count
    ):
        raise ValueError(f"AutoCAD {release} recovery job counts are invalid.")
    jobs = raw["jobs"]
    if not isinstance(jobs, list) or len(jobs) != job_count:
        raise ValueError(f"AutoCAD {release} recovery jobs are incomplete.")
    job_ids: set[str] = set()
    plan_ids: set[str] = set()
    for job in jobs:
        if not isinstance(job, dict) or set(job) != RECOVERY_JOB_FIELDS:
            raise ValueError(f"AutoCAD {release} recovery job fields are incomplete.")
        job_id = job["job_id"]
        if not isinstance(job_id, str) or not JOB_ID_PATTERN.fullmatch(job_id):
            raise ValueError(f"AutoCAD {release} recovery job_id is invalid.")
        if job_id in job_ids:
            raise ValueError(f"AutoCAD {release} recovery jobs must be distinct.")
        job_ids.add(job_id)
        plan_id = job["plan_id"]
        if not isinstance(plan_id, str) or not PLAN_ID.fullmatch(plan_id):
            raise ValueError(f"AutoCAD {release} recovery plan_id is invalid.")
        if plan_id in plan_ids:
            raise ValueError(f"AutoCAD {release} recovery plans must be distinct.")
        plan_ids.add(plan_id)
        for field in (
            "manifest_sha256",
            "receipt_manifest_sha256",
            "receipt_outputs_sha256",
            "source_sha256_before",
            "source_sha256_after",
            "staged_sha256_before",
            "staged_sha256_after",
        ):
            _require_digest(job[field], f"recovery.jobs.{field}")
        if job["manifest_sha256"] != job["receipt_manifest_sha256"]:
            raise ValueError(
                f"AutoCAD {release} recovery receipt is not bound to its manifest."
            )
        if job["source_sha256_before"] != job["source_sha256_after"]:
            raise ValueError(f"AutoCAD {release} recovery source DWG changed.")
        if job["staged_sha256_before"] != job["staged_sha256_after"]:
            raise ValueError(f"AutoCAD {release} recovery staged DWG changed.")
        if job["receipt_state"] != "succeeded":
            raise ValueError(f"AutoCAD {release} recovery receipt did not succeed.")
        if (
            not isinstance(job["receipt_output_count"], int)
            or isinstance(job["receipt_output_count"], bool)
            or job["receipt_output_count"] < 1
        ):
            raise ValueError(f"AutoCAD {release} recovery output count is invalid.")
        for flag in (
            "receipt_output_binding_verified",
            "outputs_complete",
            "publish_verified",
        ):
            if job[flag] is not True:
                raise ValueError(
                    f"AutoCAD {release} recovery job requires {flag}=true."
                )
    if [job["job_id"] for job in jobs] != sorted(job_ids):
        raise ValueError(f"AutoCAD {release} recovery jobs must be ordered by job_id.")
    approved_by = raw["approved_by"]
    if not isinstance(approved_by, str) or not approved_by.strip() or len(approved_by) > 200:
        raise ValueError(f"AutoCAD {release} recovery approved_by is invalid.")
    try:
        completed = datetime.fromisoformat(raw["completed_utc"])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"AutoCAD {release} recovery completed_utc is invalid.") from exc
    if completed.tzinfo is None:
        raise ValueError(f"AutoCAD {release} recovery completed_utc needs a timezone.")
    return raw


def assemble_pilot_evidence(
    run_2016: Any,
    run_2025: Any,
    recovery_2016: Any,
    recovery_2025: Any,
    *,
    repository_commit: str,
    bundle_sha256: str,
    bundle_build_manifest_sha256: str,
    package_version: str,
    adapter_sha256: dict[str, str],
) -> dict[str, Any]:
    """Assemble and validate the immutable two-release pilot evidence document."""
    validated_2016 = validate_pilot_run_evidence(run_2016)
    validated_2025 = validate_pilot_run_evidence(run_2025)
    if validated_2016["autocad_release"] != "2016":
        raise ValueError("run_2016 must contain AutoCAD 2016 evidence.")
    if validated_2025["autocad_release"] != "2025":
        raise ValueError("run_2025 must contain AutoCAD 2025 evidence.")
    validated_recovery_2016 = validate_batch_recovery_evidence(recovery_2016)
    validated_recovery_2025 = validate_batch_recovery_evidence(recovery_2025)
    if validated_recovery_2016["autocad_release"] != "2016":
        raise ValueError("recovery_2016 must contain AutoCAD 2016 evidence.")
    if validated_recovery_2025["autocad_release"] != "2025":
        raise ValueError("recovery_2025 must contain AutoCAD 2025 evidence.")
    raw = {
        "schema_version": 7,
        "repository_commit": repository_commit,
        "package_version": package_version,
        "bundle_sha256": bundle_sha256,
        "bundle_build_manifest_sha256": bundle_build_manifest_sha256,
        "adapter_sha256": adapter_sha256,
        "runs": [validated_2016, validated_2025],
        "batch_recovery": [validated_recovery_2016, validated_recovery_2025],
    }
    validate_pilot_evidence(raw)
    return raw


def validate_pilot_evidence(raw: Any) -> dict[str, Any]:
    """Require separate, complete licensed-workstation evidence for 2016 and 2025."""
    if not isinstance(raw, dict) or set(raw) != {
        "schema_version",
        "repository_commit",
        "package_version",
        "bundle_sha256",
        "bundle_build_manifest_sha256",
        "adapter_sha256",
        "runs",
        "batch_recovery",
    }:
        raise ValueError("Pilot evidence must contain exactly the documented top-level fields.")
    if raw["schema_version"] != 7:
        raise ValueError("Unsupported pilot evidence schema.")
    if not isinstance(raw["repository_commit"], str) or not COMMIT.fullmatch(
        raw["repository_commit"]
    ):
        raise ValueError("repository_commit must be a full lowercase Git commit SHA.")
    _require_digest(raw["bundle_sha256"], "bundle_sha256")
    _require_digest(
        raw["bundle_build_manifest_sha256"], "bundle_build_manifest_sha256"
    )
    if not isinstance(raw["package_version"], str) or not re.fullmatch(
        r"[0-9]+(?:\.[0-9]+){2}(?:[A-Za-z0-9.+-]*)?", raw["package_version"]
    ):
        raise ValueError("package_version is invalid.")
    adapter_sha256 = raw["adapter_sha256"]
    if not isinstance(adapter_sha256, dict) or set(adapter_sha256) != {"2016", "2025"}:
        raise ValueError("adapter_sha256 must contain exactly 2016 and 2025.")
    for release, digest in adapter_sha256.items():
        _require_digest(digest, f"adapter_sha256.{release}")
    runs = raw["runs"]
    if not isinstance(runs, list) or len(runs) != 2:
        raise ValueError("Pilot evidence must contain exactly one 2016 and one 2025 run.")
    validated = [_validate_run(run) for run in runs]
    releases = [run["autocad_release"] for run in validated]
    if sorted(releases) != ["2016", "2025"]:
        raise ValueError("Pilot evidence must contain distinct AutoCAD 2016 and 2025 runs.")
    for run in validated:
        release = run["autocad_release"]
        if run["build_commit"] != raw["repository_commit"]:
            raise ValueError(f"AutoCAD {release} running plug-in commit mismatch.")
        if run["plugin_sha256"] != adapter_sha256[release]:
            raise ValueError(f"AutoCAD {release} running plug-in binary mismatch.")
    recovery = raw["batch_recovery"]
    if not isinstance(recovery, list) or len(recovery) != 2:
        raise ValueError(
            "Pilot evidence must contain one batch recovery record for each AutoCAD release."
        )
    validated_recovery = [validate_batch_recovery_evidence(item) for item in recovery]
    recovery_releases = [item["autocad_release"] for item in validated_recovery]
    if sorted(recovery_releases) != ["2016", "2025"]:
        raise ValueError(
            "Pilot evidence must contain distinct AutoCAD 2016 and 2025 batch recovery records."
        )
    for item in validated_recovery:
        release = item["autocad_release"]
        matching_run = next(run for run in validated if run["autocad_release"] == release)
        if item["build_commit"] != raw["repository_commit"]:
            raise ValueError(f"AutoCAD {release} recovery plug-in commit mismatch.")
        if item["plugin_sha256"] != adapter_sha256[release]:
            raise ValueError(f"AutoCAD {release} recovery plug-in binary mismatch.")
        for field in (
            "product",
            "adapter",
            "build_commit",
            "plugin_sha256",
            "runtime_series",
            "queue_authentication",
        ):
            if item[field] != matching_run[field]:
                raise ValueError(
                    f"AutoCAD {release} recovery and one-sheet runtime identity mismatch."
                )
        if matching_run["plan_id"] in {job["plan_id"] for job in item["jobs"]}:
            raise ValueError(
                f"AutoCAD {release} recovery batch must be distinct from the one-sheet pilot."
            )
        if datetime.fromisoformat(item["completed_utc"]) <= datetime.fromisoformat(
            matching_run["completed_utc"]
        ):
            raise ValueError(
                f"AutoCAD {release} recovery must be completed after the one-sheet pilot."
            )
    return {
        "valid": True,
        "schema_version": 7,
        "repository_commit": raw["repository_commit"],
        "package_version": raw["package_version"],
        "bundle_sha256": raw["bundle_sha256"],
        "bundle_build_manifest_sha256": raw["bundle_build_manifest_sha256"],
        "accepted_releases": sorted(releases),
        "batch_recovery_releases": sorted(recovery_releases),
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
    if run["runtime_series"] != expected["acadver"]:
        raise ValueError(f"AutoCAD {release} normalized runtime series mismatch.")
    if run["queue_authentication"] != QUEUE_AUTHENTICATION_SCHEME:
        raise ValueError(f"AutoCAD {release} queue authentication mismatch.")
    product = run["product"]
    if (
        not isinstance(product, str)
        or f"AutoCAD {release}" not in product
        or f"ACADVER {expected['acadver']}" not in product
    ):
        raise ValueError(f"AutoCAD {release} product/ACADVER evidence mismatch.")
    if not isinstance(run["build_commit"], str) or not COMMIT.fullmatch(
        run["build_commit"]
    ):
        raise ValueError(f"AutoCAD {release} build_commit is invalid.")
    _require_digest(run["plugin_sha256"], "plugin_sha256")
    for flag in (
        "licensed",
        "authorized_test_asset",
        "publish_verified",
        "restart_receipt_verified",
        "receipt_output_binding_verified",
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
    ):
        _require_digest(run[field], field)
    if run["manifest_sha256"] != run["receipt_manifest_sha256"]:
        raise ValueError(f"AutoCAD {release} receipt is not bound to the approved manifest.")
    if run["source_sha256_before"] != run["source_sha256_after"]:
        raise ValueError(f"AutoCAD {release} source DWG changed during the pilot.")
    if run["staged_sha256_before"] != run["staged_sha256_after"]:
        raise ValueError(f"AutoCAD {release} staged DWG changed during the pilot.")
    _validate_template_assets(run["template_assets"], release)
    if run["receipt_state"] != "succeeded":
        raise ValueError(f"AutoCAD {release} receipt_state must be succeeded.")
    _validate_pdf_evidence(run["published_pdf"], release, "published_pdf")
    if (
        not isinstance(run["receipt_output_count"], int)
        or isinstance(run["receipt_output_count"], bool)
        or run["receipt_output_count"] != 1
    ):
        raise ValueError(f"AutoCAD {release} receipt_output_count must be one.")
    _require_digest(run["receipt_outputs_sha256"], "receipt_outputs_sha256")
    if run["receipt_outputs_sha256"] != build_receipt_output_digest(
        [run["published_pdf"]]
    ):
        raise ValueError(f"AutoCAD {release} receipt output binding mismatch.")
    _validate_visual_reference(run["visual_reference"], release)
    tolerance = float(run["visual_reference"]["comparison_tolerance_mm"])
    for field in ("page_width_mm", "page_height_mm"):
        if abs(
            float(run["published_pdf"][field])
            - float(run["visual_reference"][field])
        ) > tolerance:
            raise ValueError(
                f"AutoCAD {release} visual reference geometry does not match published PDF."
            )
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


def _collect_visual_reference(
    reference_pdf_value: str | Path,
    config: CadPlotConfig,
    output: dict[str, Any],
) -> dict[str, Any]:
    supplied = Path(reference_pdf_value).expanduser().absolute()
    if _is_reparse(supplied):
        raise ValueError("Visual reference PDF must not be a symlink or reparse point.")
    reference = config.path_policy.require_allowed(supplied, suffix=".pdf")
    if not reference.is_file():
        raise ValueError("Visual reference PDF must be a regular file.")
    before = reference.stat()
    size = before.st_size
    if size <= 0 or size > MAX_REFERENCE_PDF_BYTES:
        raise ValueError("Visual reference PDF must be between 1 byte and 128 MiB.")
    try:
        reference_bytes = reference.read_bytes()
        after_read = reference.stat()
    except OSError as exc:
        raise ValueError("Visual reference PDF could not be read.") from exc
    if len(reference_bytes) != size or _file_snapshot_changed(before, after_read):
        raise ValueError("Visual reference PDF changed while being read.")
    if reference_bytes[:5] != b"%PDF-":
        raise ValueError("Visual reference does not have a PDF header.")
    try:
        reader = PdfReader(BytesIO(reference_bytes), strict=False)
        if reader.is_encrypted:
            raise ValueError("Visual reference PDF must not be encrypted.")
        if len(reader.pages) != 1:
            raise ValueError("Visual reference PDF must contain exactly one page.")
        page = reader.pages[0]
        media_box = page.mediabox
        width_mm = float(media_box.width) * 25.4 / 72
        height_mm = float(media_box.height) * 25.4 / 72
        rotation = int(page.get("/Rotate", 0) or 0)
        if rotation % 90 != 0:
            raise ValueError("Visual reference PDF rotation must be a multiple of 90 degrees.")
        if rotation % 180 != 0:
            width_mm, height_mm = height_mm, width_mm
        if pdf_page_marking_evidence(page)["marking_operator_count"] < 1:
            raise ValueError("Visual reference PDF has no marking content.")
    except LimitReachedError as exc:
        raise ValueError(
            "Visual reference PDF decoded content exceeds the audit safety limit."
        ) from exc
    except (OSError, PdfReadError, TypeError, ValueError) as exc:
        if isinstance(exc, ValueError) and str(exc).startswith("Visual reference"):
            raise
        raise ValueError("Visual reference PDF structure is invalid.") from exc
    try:
        after_parse = reference.stat()
        current_path = supplied.resolve(strict=True)
        path_redirected = _is_reparse(supplied) or current_path != reference
    except OSError:
        after_parse = None
        path_redirected = True
    if (
        after_parse is None
        or path_redirected
        or _file_snapshot_changed(before, after_parse)
    ):
        raise ValueError("Visual reference PDF changed while being audited.")
    if not all(math.isfinite(value) and value > 0 for value in (width_mm, height_mm)):
        raise ValueError("Visual reference PDF page size is invalid.")
    output_width = output.get("page_width_mm")
    output_height = output.get("page_height_mm")
    if not all(
        isinstance(value, (int, float)) and math.isfinite(value) and value > 0
        for value in (output_width, output_height)
    ):
        raise ValueError("Published PDF is missing validated page geometry.")
    if (
        abs(width_mm - float(output_width)) > config.pdf_page_tolerance_mm
        or abs(height_mm - float(output_height)) > config.pdf_page_tolerance_mm
    ):
        raise ValueError(
            "Visual reference PDF orientation or page size does not match the published PDF."
        )
    return {
        "sha256": hashlib.sha256(reference_bytes).hexdigest(),
        "size_bytes": size,
        "page_count": 1,
        "page_width_mm": round(width_mm, 3),
        "page_height_mm": round(height_mm, 3),
        "comparison_tolerance_mm": config.pdf_page_tolerance_mm,
    }


def _file_snapshot_changed(before: Any, after: Any) -> bool:
    return any(
        getattr(before, field, None) != getattr(after, field, None)
        for field in ("st_dev", "st_ino", "st_size", "st_mtime_ns")
    )


def _collect_published_pdf(output: dict[str, Any]) -> dict[str, Any]:
    return {
        "sheet_index": output["sheet_index"],
        "file": Path(output["pdf"]).name,
        "sha256": output["sha256"],
        "size_bytes": output["size_bytes"],
        "page_count": output["page_count"],
        "page_width_mm": output["page_width_mm"],
        "page_height_mm": output["page_height_mm"],
    }


def _validate_pdf_evidence(raw: Any, release: str, label: str) -> None:
    if not isinstance(raw, dict) or set(raw) != PDF_EVIDENCE_FIELDS:
        raise ValueError(f"AutoCAD {release} {label} fields are incomplete.")
    _require_digest(raw["sha256"], f"{label}.sha256")
    if raw["sheet_index"] != 1:
        raise ValueError(f"AutoCAD {release} {label} sheet index is invalid.")
    file_name = raw["file"]
    if (
        not isinstance(file_name, str)
        or not file_name
        or Path(file_name).name != file_name
        or Path(file_name).suffix.casefold() != ".pdf"
        or any(ord(character) < 32 or ord(character) == 127 for character in file_name)
    ):
        raise ValueError(f"AutoCAD {release} {label} file identity is invalid.")
    if (
        not isinstance(raw["size_bytes"], int)
        or isinstance(raw["size_bytes"], bool)
        or raw["size_bytes"] < 1
    ):
        raise ValueError(f"AutoCAD {release} {label} size is invalid.")
    if raw["page_count"] != 1:
        raise ValueError(f"AutoCAD {release} {label} must contain one page.")
    for field in ("page_width_mm", "page_height_mm"):
        value = raw[field]
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(value)
            or value <= 0
        ):
            raise ValueError(f"AutoCAD {release} {label} page size is invalid.")


def _validate_visual_reference(raw: Any, release: str) -> None:
    if not isinstance(raw, dict) or set(raw) != VISUAL_REFERENCE_FIELDS:
        raise ValueError(f"AutoCAD {release} visual_reference fields are incomplete.")
    _require_digest(raw["sha256"], "visual_reference.sha256")
    if (
        not isinstance(raw["size_bytes"], int)
        or isinstance(raw["size_bytes"], bool)
        or not 1 <= raw["size_bytes"] <= MAX_REFERENCE_PDF_BYTES
    ):
        raise ValueError(f"AutoCAD {release} visual_reference size is invalid.")
    if raw["page_count"] != 1:
        raise ValueError(f"AutoCAD {release} visual_reference must contain one page.")
    for field in ("page_width_mm", "page_height_mm"):
        value = raw[field]
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(value)
            or value <= 0
        ):
            raise ValueError(f"AutoCAD {release} visual_reference page size is invalid.")
    tolerance = raw["comparison_tolerance_mm"]
    if (
        not isinstance(tolerance, (int, float))
        or isinstance(tolerance, bool)
        or not math.isfinite(tolerance)
        or not 0 <= tolerance <= 10
    ):
        raise ValueError(f"AutoCAD {release} visual_reference tolerance is invalid.")


def _is_reparse(path: Path) -> bool:
    try:
        stat = path.lstat()
    except OSError:
        return False
    return path.is_symlink() or bool(
        getattr(stat, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT
    )


def _collect_template_asset_evidence(
    manifest: dict[str, Any], config: CadPlotConfig
) -> list[dict[str, Any]]:
    assets = manifest.get("template_assets", [])
    if not assets:
        return []
    if config.template_path_policy is None:
        raise ValueError("Pilot template assets require configured template_roots.")
    profiles = {profile.id: profile for profile in config.paper_profiles}
    evidence: list[dict[str, Any]] = []
    for asset in sorted(assets, key=lambda item: item["id"]):
        profile = profiles.get(asset["id"])
        source_value = asset.get("source_template")
        if not isinstance(source_value, str) or not source_value.strip():
            raise ValueError(f"Pilot template asset {asset['id']!r} has no source identity.")
        source = config.template_path_policy.require_allowed(source_value)
        if (
            profile is None
            or profile.template_drawing != source
            or profile.template_sha256 != asset["sha256"]
            or profile.template_layout != asset["layout"]
            or profile.page_setup != asset["page_setup"]
        ):
            raise ValueError(
                f"Pilot template asset {asset['id']!r} does not match the active office profile."
            )
        staged = Path(asset["staged_template"]).resolve(strict=True)
        source_fingerprint = fingerprint_file(
            source, label=f"Pilot template {asset['id']!r} source"
        )
        staged_fingerprint = fingerprint_file(
            staged, label=f"Pilot template {asset['id']!r} staged copy"
        )
        source_hash = source_fingerprint["sha256"]
        staged_hash = staged_fingerprint["sha256"]
        if (
            source_fingerprint["size_bytes"] != asset["size_bytes"]
            or source_hash != asset["sha256"]
        ):
            raise ValueError(
                f"Pilot template asset {asset['id']!r} source changed after approval."
            )
        if (
            staged_fingerprint["size_bytes"] != asset["size_bytes"]
            or staged_hash != asset["sha256"]
        ):
            raise ValueError(
                f"Pilot template asset {asset['id']!r} staged copy changed after approval."
            )
        evidence.append(
            {
                "id": asset["id"],
                "layout": asset["layout"],
                "page_setup": asset["page_setup"],
                "size_bytes": asset["size_bytes"],
                "approved_sha256": asset["sha256"],
                "source_sha256_after": source_hash,
                "staged_sha256_after": staged_hash,
            }
        )
    return evidence


def _validate_template_assets(value: Any, release: str) -> None:
    if not isinstance(value, list) or len(value) > 100:
        raise ValueError(f"AutoCAD {release} template_assets collection is invalid.")
    ids: set[str] = set()
    layouts: set[str] = set()
    for asset in value:
        if not isinstance(asset, dict) or set(asset) != TEMPLATE_ASSET_FIELDS:
            raise ValueError(f"AutoCAD {release} template asset fields are invalid.")
        asset_id = asset["id"]
        if (
            not isinstance(asset_id, str)
            or re.fullmatch(r"[A-Za-z0-9_-]{1,64}", asset_id) is None
            or asset_id in ids
        ):
            raise ValueError(f"AutoCAD {release} template asset id is invalid or duplicate.")
        ids.add(asset_id)
        for field in ("layout", "page_setup"):
            name = asset[field]
            if (
                not isinstance(name, str)
                or not 1 <= len(name) <= 255
                or name.isspace()
                or any(ord(character) < 32 or ord(character) == 127 for character in name)
            ):
                raise ValueError(f"AutoCAD {release} template asset metadata is invalid.")
        layout_key = asset["layout"].casefold()
        if layout_key in layouts:
            raise ValueError(f"AutoCAD {release} template asset layout is duplicate.")
        layouts.add(layout_key)
        size = asset["size_bytes"]
        if not isinstance(size, int) or isinstance(size, bool) or size < 1:
            raise ValueError(f"AutoCAD {release} template asset size is invalid.")
        for field in (
            "approved_sha256",
            "source_sha256_after",
            "staged_sha256_after",
        ):
            _require_digest(asset[field], f"template_assets.{field}")
        if (
            asset["approved_sha256"] != asset["source_sha256_after"]
            or asset["approved_sha256"] != asset["staged_sha256_after"]
        ):
            raise ValueError(f"AutoCAD {release} template asset changed during the pilot.")


def validate_bundle_build_evidence(
    bundle_archive_value: str | Path,
    build_manifest_value: str | Path,
) -> dict[str, Any]:
    """Cross-check a production bundle archive, build manifest, API IDs, and inner hashes."""
    bundle_archive = Path(bundle_archive_value).expanduser().resolve(strict=True)
    build_manifest = Path(build_manifest_value).expanduser().resolve(strict=True)
    if not bundle_archive.is_file() or bundle_archive.is_symlink():
        raise ValueError("Bundle archive must be a plain file.")
    if not build_manifest.is_file() or build_manifest.is_symlink():
        raise ValueError("Bundle build manifest must be a plain file.")
    if build_manifest.stat().st_size > 1024 * 1024:
        raise ValueError("Bundle build manifest exceeds the 1 MiB safety limit.")
    try:
        raw = json.loads(build_manifest.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Bundle build manifest must be valid UTF-8 JSON.") from exc
    if not isinstance(raw, dict) or set(raw) != BUNDLE_BUILD_FIELDS:
        raise ValueError("Bundle build manifest fields are not exact.")
    if raw["schema_version"] != 1:
        raise ValueError("Unsupported bundle build manifest schema.")
    if not isinstance(raw["exact_commit"], str) or not COMMIT.fullmatch(raw["exact_commit"]):
        raise ValueError("Bundle build exact_commit is invalid.")
    if not isinstance(raw["package_version"], str) or not re.fullmatch(
        r"[0-9]+(?:\.[0-9]+){2}(?:[A-Za-z0-9.+-]*)?", raw["package_version"]
    ):
        raise ValueError("Bundle build package_version is invalid.")
    for field in (
        "source_tree_audit_passed",
        "bundle_verification_passed",
        "archive_audit_passed",
        "matching_sdk_bundle_built",
    ):
        if raw[field] is not True:
            raise ValueError(f"Bundle build requires {field}=true.")
    for field in (
        "company_assets_copied",
        "autodesk_binaries_included",
        "autocad_launched",
        "live_publish_proven",
    ):
        if raw[field] is not False:
            raise ValueError(f"Bundle build requires {field}=false.")
    _validate_api_identity(raw["api_identity"])

    bundle = raw["bundle"]
    if not isinstance(bundle, dict) or set(bundle) != {
        "directory",
        "archive",
        "archive_sha256",
        "files",
    }:
        raise ValueError("Bundle build archive fields are not exact.")
    if bundle["directory"] != "CadPlotMcp.bundle" or bundle["archive"] != bundle_archive.name:
        raise ValueError("Bundle build archive names are invalid.")
    archive_sha256 = _sha256(bundle_archive)
    if bundle["archive_sha256"] != archive_sha256:
        raise ValueError("Bundle archive hash does not match its build manifest.")
    files = bundle["files"]
    if not isinstance(files, list) or len(files) != len(BUNDLE_FILES):
        raise ValueError("Bundle build file evidence count is invalid.")
    hashes: dict[str, str] = {}
    for item in files:
        if not isinstance(item, dict) or set(item) != {"path", "sha256"}:
            raise ValueError("Bundle build file evidence fields are invalid.")
        path = item["path"]
        if path in hashes:
            raise ValueError("Bundle build file evidence contains duplicate paths.")
        _require_digest(item["sha256"], f"bundle file {path}")
        hashes[path] = item["sha256"]
    if set(hashes) != BUNDLE_FILES:
        raise ValueError("Bundle build file evidence set is invalid.")

    expected_entries = {f"CadPlotMcp.bundle/{path}": digest for path, digest in hashes.items()}
    try:
        with zipfile.ZipFile(bundle_archive) as archive:
            entries = [entry for entry in archive.infolist() if not entry.is_dir()]
            names = [entry.filename.replace("\\", "/") for entry in entries]
            if len(names) != len(expected_entries) or set(names) != set(expected_entries):
                raise ValueError("Bundle archive entry set is invalid.")
            if len(names) != len(set(names)):
                raise ValueError("Bundle archive contains duplicate entries.")
            for entry, name in zip(entries, names, strict=True):
                if entry.file_size > 100 * 1024 * 1024:
                    raise ValueError("Bundle archive entry exceeds the 100 MiB safety limit.")
                digest = hashlib.sha256()
                with archive.open(entry) as stream:
                    while chunk := stream.read(1024 * 1024):
                        digest.update(chunk)
                if digest.hexdigest() != expected_entries[name]:
                    raise ValueError(f"Bundle archive entry hash mismatch: {name}")
    except zipfile.BadZipFile as exc:
        raise ValueError("Bundle archive is not a valid ZIP file.") from exc

    return {
        "repository_commit": raw["exact_commit"],
        "package_version": raw["package_version"],
        "bundle_sha256": archive_sha256,
        "bundle_build_manifest_sha256": _sha256(build_manifest),
        "adapter_sha256": {
            release: hashes[path] for release, path in ADAPTER_PATHS.items()
        },
    }


def _validate_api_identity(value: Any) -> None:
    if not isinstance(value, dict) or set(value) != {"autocad_2016", "autocad_2025"}:
        raise ValueError("Bundle build API identity fields are invalid.")
    expected = {
        "autocad_2016": ("R20.1", "20.1."),
        "autocad_2025": ("R25.0", "25.0."),
    }
    expected_names = {"AcMgd.dll", "AcDbMgd.dll", "AcCoreMgd.dll"}
    for key, (series, version_prefix) in expected.items():
        identity = value[key]
        if not isinstance(identity, dict) or set(identity) != {
            "detected_series",
            "assemblies",
        }:
            raise ValueError(f"{key} API identity fields are invalid.")
        if identity["detected_series"] != series:
            raise ValueError(f"{key} API identity series mismatch.")
        assemblies = identity["assemblies"]
        if not isinstance(assemblies, list) or len(assemblies) != 3:
            raise ValueError(f"{key} API assembly evidence count is invalid.")
        names = set()
        for assembly in assemblies:
            if not isinstance(assembly, dict) or set(assembly) != {
                "name",
                "assembly_version",
                "sha256",
            }:
                raise ValueError(f"{key} API assembly evidence fields are invalid.")
            names.add(assembly["name"])
            if not str(assembly["assembly_version"]).startswith(version_prefix):
                raise ValueError(f"{key} API assembly version mismatch.")
            _require_digest(assembly["sha256"], f"{key} API assembly")
        if names != expected_names:
            raise ValueError(f"{key} API assembly names are invalid.")


def _require_digest(value: Any, field: str) -> None:
    if not isinstance(value, str) or not SHA256.fullmatch(value):
        raise ValueError(f"{field} must be a lowercase SHA-256 digest.")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()
