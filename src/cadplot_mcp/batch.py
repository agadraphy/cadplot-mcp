from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

PlanBuilder = Callable[[str], dict[str, Any]]
StageBuilder = Callable[[dict[str, Any], str], dict[str, Any]]
QueueBuilder = Callable[[dict[str, str]], dict[str, Any]]


def build_batch_page(
    drawing_paths: Sequence[str],
    plan_builder: PlanBuilder,
    *,
    offset: int = 0,
    limit: int = 20,
) -> dict[str, Any]:
    """Build one deterministic batch page while isolating per-drawing failures."""
    if offset < 0:
        raise ValueError("offset must be zero or greater.")
    if not 1 <= limit <= 50:
        raise ValueError("limit must be between 1 and 50.")

    total = len(drawing_paths)
    selected = drawing_paths[offset : offset + limit]
    items: list[dict[str, Any]] = []
    for path in selected:
        try:
            plan = plan_builder(path)
        except Exception as exc:
            items.append({"drawing": path, "status": "error", "error": str(exc)})
            continue
        items.append(
            {
                "drawing": path,
                "status": "ready" if plan.get("ready") is True else "blocked",
                "plan": plan,
            }
        )

    ready = sum(item["status"] == "ready" for item in items)
    blocked = sum(item["status"] == "blocked" for item in items)
    errors = len(items) - ready - blocked
    next_offset = offset + len(selected)
    payload = {
        "schema_version": 1,
        "offset": offset,
        "limit": limit,
        "total_drawings": total,
        "processed": len(items),
        "next_offset": next_offset if next_offset < total else None,
        "has_more": next_offset < total,
        "summary": {"ready": ready, "blocked": blocked, "errors": errors},
        "items": items,
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        "batch_page_id": "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        **payload,
    }


def stage_approved_batch(
    approvals: list[dict[str, str]],
    plan_builder: PlanBuilder,
    stage_builder: StageBuilder,
) -> dict[str, Any]:
    """Stage a bounded set of explicit path/plan approvals with per-file isolation."""
    normalized = _validate_approvals(approvals)
    canonical = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    approval_batch_id = "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    items: list[dict[str, Any]] = []
    for approval in normalized:
        path = approval["path"]
        approved_plan_id = approval["plan_id"]
        try:
            plan = plan_builder(path)
            if plan.get("ready") is not True:
                items.append(
                    {
                        "drawing": path,
                        "approved_plan_id": approved_plan_id,
                        "status": "blocked",
                        "error": "Current publish plan has blockers.",
                        "current_plan": plan,
                    }
                )
                continue
            if plan.get("plan_id") != approved_plan_id:
                items.append(
                    {
                        "drawing": path,
                        "approved_plan_id": approved_plan_id,
                        "status": "approval_mismatch",
                        "error": "Drawing or plan changed after approval.",
                        "current_plan_id": plan.get("plan_id"),
                    }
                )
                continue
            job = stage_builder(plan, approved_plan_id)
        except Exception as exc:
            items.append(
                {
                    "drawing": path,
                    "approved_plan_id": approved_plan_id,
                    "status": "error",
                    "error": str(exc),
                }
            )
            continue
        items.append(
            {
                "drawing": path,
                "approved_plan_id": approved_plan_id,
                "status": "staged",
                "job": job,
            }
        )

    staged = sum(item["status"] == "staged" for item in items)
    blocked = sum(item["status"] in {"blocked", "approval_mismatch"} for item in items)
    return {
        "schema_version": 1,
        "approval_batch_id": approval_batch_id,
        "complete": staged == len(items),
        "summary": {
            "requested": len(items),
            "staged": staged,
            "blocked": blocked,
            "errors": len(items) - staged - blocked,
        },
        "items": items,
    }


def _validate_approvals(approvals: list[dict[str, str]]) -> list[dict[str, str]]:
    if not isinstance(approvals, list) or not 1 <= len(approvals) <= 20:
        raise ValueError("approvals must contain between 1 and 20 items.")
    normalized: list[dict[str, str]] = []
    for item in approvals:
        if not isinstance(item, dict) or set(item) != {"path", "plan_id"}:
            raise ValueError("Each approval must contain exactly path and plan_id.")
        path = item["path"]
        plan_id = item["plan_id"]
        if not isinstance(path, str) or not path.strip():
            raise ValueError("Approval path must be a non-empty string.")
        if not isinstance(plan_id, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", plan_id):
            raise ValueError("Approval plan_id must be a SHA-256 identifier.")
        normalized.append({"path": path, "plan_id": plan_id})
    paths = [
        str(Path(item["path"]).expanduser().resolve(strict=False)).casefold()
        for item in normalized
    ]
    plan_ids = [item["plan_id"] for item in normalized]
    if len(paths) != len(set(paths)):
        raise ValueError("Approval paths must be unique within a batch.")
    if len(plan_ids) != len(set(plan_ids)):
        raise ValueError("Approval plan_ids must be unique within a batch.")
    return normalized


def queue_approved_batch(
    approvals: list[dict[str, str]],
    queue_builder: QueueBuilder,
) -> dict[str, Any]:
    """Queue up to 20 exact staged-manifest approvals with per-job isolation."""
    normalized = _validate_queue_approvals(approvals)
    canonical = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    batch_id = "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    items: list[dict[str, Any]] = []
    for approval in normalized:
        try:
            result = queue_builder(approval)
        except Exception as exc:
            result = {"queued": False, "error": str(exc)}
        items.append({**approval, **result})
    queued = sum(item.get("queued") is True for item in items)
    return {
        "schema_version": 1,
        "queue_batch_id": batch_id,
        "complete": queued == len(items),
        "summary": {
            "requested": len(items),
            "queued": queued,
            "failed": len(items) - queued,
        },
        "items": items,
    }


def _validate_queue_approvals(approvals: list[dict[str, str]]) -> list[dict[str, str]]:
    required = {"manifest_path", "plan_id", "manifest_sha256"}
    if not isinstance(approvals, list) or not 1 <= len(approvals) <= 20:
        raise ValueError("approvals must contain between 1 and 20 items.")
    normalized: list[dict[str, str]] = []
    for item in approvals:
        if not isinstance(item, dict) or set(item) != required:
            raise ValueError(
                "Each queue approval must contain exactly manifest_path, plan_id, "
                "and manifest_sha256."
            )
        path = item["manifest_path"]
        plan_id = item["plan_id"]
        manifest_sha256 = item["manifest_sha256"]
        if not isinstance(path, str) or not path.strip():
            raise ValueError("Queue approval manifest_path must be a non-empty string.")
        if not isinstance(plan_id, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", plan_id):
            raise ValueError("Queue approval plan_id must be a SHA-256 identifier.")
        if not isinstance(manifest_sha256, str) or not re.fullmatch(
            r"[0-9a-f]{64}", manifest_sha256
        ):
            raise ValueError("Queue approval manifest_sha256 must be a SHA-256 digest.")
        normalized.append(
            {
                "manifest_path": path,
                "plan_id": plan_id,
                "manifest_sha256": manifest_sha256,
            }
        )
    paths = [
        str(Path(item["manifest_path"]).expanduser().resolve(strict=False)).casefold()
        for item in normalized
    ]
    plan_ids = [item["plan_id"] for item in normalized]
    digests = [item["manifest_sha256"] for item in normalized]
    if len(paths) != len(set(paths)):
        raise ValueError("Queue manifest paths must be unique within a batch.")
    if len(plan_ids) != len(set(plan_ids)):
        raise ValueError("Queue plan_ids must be unique within a batch.")
    if len(digests) != len(set(digests)):
        raise ValueError("Queue manifest digests must be unique within a batch.")
    return normalized
