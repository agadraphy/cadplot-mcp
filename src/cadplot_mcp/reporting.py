from __future__ import annotations

import hashlib
import json
import re
from base64 import b64decode
from binascii import Error as Base64Error
from datetime import datetime
from pathlib import Path
from typing import Any

from cadplot_mcp.audit import audit_publish_outputs_snapshot
from cadplot_mcp.config import CadPlotConfig
from cadplot_mcp.security import require_plain_directory_path

# Accept the earlier second-precision IDs while new jobs use sortable microsecond precision.
JOB_ID_PATTERN = re.compile(r"job-\d{8}T(?:\d{6}|\d{12})Z-[0-9a-f]{12}")
MAX_REPORTED_ISSUES_PER_JOB = 20
CANCELLED_MARKER = ".cadplot-queue-cancelled.json"
MAX_QUEUE_MARKER_BYTES = 65_536


def build_publish_operations_report(
    config: CadPlotConfig,
    *,
    after_job_id: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """Build one restartable, read-only page over staged workspace jobs."""
    if not 1 <= limit <= 50:
        raise ValueError("limit must be between 1 and 50.")
    if after_job_id is not None and not JOB_ID_PATTERN.fullmatch(after_job_id):
        raise ValueError("after_job_id must be a valid CadPlot job ID.")
    if config.workspace_root is None:
        raise ValueError("workspace_root must be configured before reporting jobs.")

    workspace = require_plain_directory_path(config.workspace_root)
    if not workspace.exists():
        return _report_page([], after_job_id=after_job_id, limit=limit, has_more=False)
    workspace = workspace.resolve(strict=True)
    job_ids = sorted(
        child.name
        for child in workspace.iterdir()
        if JOB_ID_PATTERN.fullmatch(child.name) and child.is_dir()
    )
    if after_job_id is not None:
        job_ids = [job_id for job_id in job_ids if job_id > after_job_id]
    selected = job_ids[:limit]
    items = [_inspect_job(workspace / job_id, config) for job_id in selected]
    return _report_page(
        items,
        after_job_id=after_job_id,
        limit=limit,
        has_more=len(job_ids) > len(selected),
    )


def _inspect_job(job_root: Path, config: CadPlotConfig) -> dict[str, Any]:
    manifest_path = job_root / "manifest.json"
    base = {"job_id": job_root.name, "manifest_path": str(manifest_path)}
    try:
        report, snapshot = audit_publish_outputs_snapshot(manifest_path, config)
        manifest = snapshot.manifest
        manifest_sha256 = snapshot.sha256
        cancelled_hold = _has_structural_cancelled_marker(
            job_root,
            plan_id=manifest["plan_id"],
            manifest_sha256=manifest_sha256,
        )
        snapshot.require_unchanged("operations report")
    except (OSError, ValueError) as exc:
        return {
            **base,
            "status": "invalid_job",
            "next_action": "repair_or_remove_from_workspace_after_review",
            "error": str(exc),
        }

    receipt_wrapper = report["execution_receipt"]
    receipt = receipt_wrapper["receipt"] if receipt_wrapper["found"] else None
    summary = report["summary"]
    if cancelled_hold and (receipt is not None or summary["valid"] or summary["invalid"]):
        return {
            **base,
            "status": "invalid_job",
            "next_action": "inspect_conflicting_cancel_and_execution_evidence",
            "error": "cancelled_queue_marker_conflicts_with_execution_evidence",
        }
    if cancelled_hold:
        status = "cancelled_hold"
        next_action = "confirm_live_cancelled_or_stage_new_job"
    elif not report["source_unchanged"]:
        status = "source_changed"
        next_action = "review_changed_source_then_plan_and_stage_new_job"
    elif report["publish_verified"]:
        status = "complete"
        next_action = "none"
    elif receipt is not None and receipt["state"] == "failed":
        status = "failed"
        next_action = "diagnose_then_stage_new_job"
    elif summary["valid"] or summary["invalid"]:
        status = "manual_review"
        next_action = "review_partial_outputs_then_stage_new_job"
    else:
        status = "awaiting_execution"
        next_action = "check_live_status_then_queue"

    output_issues = [
        {
            "sheet_index": output.get("sheet_index"),
            "frame_handle": output.get("frame_handle"),
            "status": output["status"],
            "pdf": output["pdf"],
        }
        for output in report["outputs"]
        if output["status"] != "valid"
    ]

    item: dict[str, Any] = {
        **base,
        "status": status,
        "next_action": next_action,
        "plan_id": manifest["plan_id"],
        "manifest_sha256": manifest_sha256,
        "created_utc": manifest.get("created_utc"),
        "source_drawing": manifest.get("source_drawing"),
        "source_unchanged": report["source_unchanged"],
        "source": report["source"],
        "outputs_complete": report["outputs_complete"],
        "execution_verified": report["execution_verified"],
        "publish_verified": report["publish_verified"],
        "output_summary": summary,
        "output_issues": output_issues[:MAX_REPORTED_ISSUES_PER_JOB],
        "output_issue_count": len(output_issues),
        "output_issues_truncated": len(output_issues) > MAX_REPORTED_ISSUES_PER_JOB,
        "receipt": receipt,
        "cancellation_requires_live_plugin_verification": cancelled_hold,
    }
    if status == "awaiting_execution":
        item["queue_approval"] = {
            "manifest_path": str(manifest_path),
            "plan_id": manifest["plan_id"],
            "manifest_sha256": manifest_sha256,
        }
    return item


def _report_page(
    items: list[dict[str, Any]],
    *,
    after_job_id: str | None,
    limit: int,
    has_more: bool,
) -> dict[str, Any]:
    statuses = {
        name: sum(item["status"] == name for item in items)
        for name in (
            "complete",
            "source_changed",
            "awaiting_execution",
            "cancelled_hold",
            "failed",
            "manual_review",
            "invalid_job",
        )
    }
    next_after_job_id = items[-1]["job_id"] if items and has_more else None
    payload = {
        "schema_version": 1,
        "after_job_id": after_job_id,
        "limit": limit,
        "processed": len(items),
        "has_more": has_more,
        "next_after_job_id": next_after_job_id,
        "summary": statuses,
        "items": items,
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        "report_page_id": "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        **payload,
    }


def _has_structural_cancelled_marker(
    job_root: Path,
    *,
    plan_id: str,
    manifest_sha256: str,
) -> bool:
    marker = job_root / CANCELLED_MARKER
    if not marker.exists():
        return False
    stat = marker.lstat()
    attributes = getattr(stat, "st_file_attributes", 0)
    if marker.is_symlink() or attributes & 0x400:
        raise ValueError("Cancelled queue marker must be a plain file.")
    if not 2 <= stat.st_size <= MAX_QUEUE_MARKER_BYTES:
        raise ValueError("Cancelled queue marker size is invalid.")
    raw = json.loads(marker.read_text(encoding="utf-8"))
    expected_keys = {
        "schema_version",
        "plan_id",
        "manifest_sha256",
        "cancelled_utc",
        "authentication_version",
        "authentication_tag",
    }
    if not isinstance(raw, dict) or set(raw) != expected_keys:
        raise ValueError("Cancelled queue marker schema is invalid.")
    if (
        raw["schema_version"] != 1
        or raw["authentication_version"] != 1
        or raw["plan_id"] != plan_id
        or raw["manifest_sha256"] != manifest_sha256
        or not isinstance(raw["cancelled_utc"], str)
    ):
        raise ValueError("Cancelled queue marker identity is invalid.")
    try:
        parsed = datetime.fromisoformat(raw["cancelled_utc"].replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Cancelled queue marker timestamp is invalid.") from exc
    if parsed.tzinfo is None:
        raise ValueError("Cancelled queue marker timestamp must include a timezone.")
    try:
        tag = b64decode(raw["authentication_tag"], validate=True)
    except (Base64Error, ValueError, TypeError) as exc:
        raise ValueError("Cancelled queue marker authentication tag is invalid.") from exc
    if len(tag) != 32:
        raise ValueError("Cancelled queue marker authentication tag is invalid.")
    return True
