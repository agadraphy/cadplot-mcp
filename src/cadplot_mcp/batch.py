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
StatusBuilder = Callable[[str], dict[str, Any]]
PluginStatusBuilder = Callable[[], dict[str, Any]]
PUBLISH_JOB_STATES = {"Pending", "Running", "Succeeded", "Failed"}
QUEUE_AUTHENTICATION_SCHEME = "windows-dpapi-current-user+hmac-sha256-v1"


def build_drawing_inventory_id(items: Sequence[dict[str, Any]]) -> str:
    """Bind paginated planning to one deterministic drawing inventory snapshot."""
    normalized = sorted(
        (
            {
                "path": str(item["path"]),
                "size_bytes": item.get("size_bytes"),
                "modified_utc": item.get("modified_utc"),
            }
            for item in items
        ),
        key=lambda item: item["path"].casefold(),
    )
    canonical = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_batch_page(
    drawing_paths: Sequence[str],
    plan_builder: PlanBuilder,
    *,
    offset: int = 0,
    limit: int = 20,
    inventory_id: str | None = None,
    expected_inventory_id: str | None = None,
) -> dict[str, Any]:
    """Build one deterministic batch page while isolating per-drawing failures."""
    if offset < 0:
        raise ValueError("offset must be zero or greater.")
    if not 1 <= limit <= 50:
        raise ValueError("limit must be between 1 and 50.")
    current_inventory_id = inventory_id or build_drawing_inventory_id(
        [{"path": path} for path in drawing_paths]
    )
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", current_inventory_id):
        raise ValueError("inventory_id must be a SHA-256 identifier.")
    if offset > 0 and expected_inventory_id is None:
        raise ValueError("expected_inventory_id is required after the first batch page.")
    if expected_inventory_id is not None and expected_inventory_id != current_inventory_id:
        raise ValueError("Drawing inventory changed; restart batch planning at offset 0.")

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
        "inventory_id": current_inventory_id,
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
    """Queue up to 20 approvals, deferring safely after live queue saturation."""
    normalized = _validate_queue_approvals(approvals)
    canonical = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    batch_id = "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    items: list[dict[str, Any]] = []
    queue_saturated = False
    for approval in normalized:
        if queue_saturated:
            items.append(
                {
                    **approval,
                    "queued": False,
                    "deferred": True,
                    "error": "queue_full",
                }
            )
            continue
        try:
            result = queue_builder(approval)
        except Exception as exc:
            result = {"queued": False, "error": str(exc)}
        if not isinstance(result, dict):
            result = {"queued": False, "error": "invalid_queue_response"}
        error = result.get("error")
        plugin = result.get("plugin")
        if error is None and isinstance(plugin, dict):
            error = plugin.get("error")
        if error == "queue_full":
            queue_saturated = True
            result = {**result, "queued": False, "deferred": True, "error": "queue_full"}
        items.append({**approval, **result})
    queued = sum(item.get("queued") is True for item in items)
    deferred = sum(item.get("deferred") is True for item in items)
    return {
        "schema_version": 2,
        "queue_batch_id": batch_id,
        "complete": queued == len(items),
        "summary": {
            "requested": len(items),
            "queued": queued,
            "deferred": deferred,
            "failed": len(items) - queued - deferred,
        },
        "items": items,
    }


def build_publish_batch_status(
    plan_ids: list[str],
    status_builder: StatusBuilder,
    plugin_status_builder: PluginStatusBuilder,
) -> dict[str, Any]:
    """Read up to 20 live job states plus one final atomic queue telemetry sample."""
    normalized = _validate_status_plan_ids(plan_ids)
    items: list[dict[str, Any]] = []
    for plan_id in normalized:
        try:
            response = status_builder(plan_id)
        except Exception as exc:
            response = {
                "found": False,
                "error": f"status_exception:{type(exc).__name__}",
            }
        items.append(_normalize_live_status(plan_id, response))

    queue: dict[str, int | str] | None = None
    queue_error: str | None = None
    try:
        queue = _normalize_queue_telemetry(plugin_status_builder())
    except Exception as exc:
        queue_error = (
            _bounded_status_error(exc)
            if isinstance(exc, ValueError)
            else f"status_exception:{type(exc).__name__}"
        )

    summary = {
        state.casefold(): sum(item.get("job_state") == state for item in items)
        for state in sorted(PUBLISH_JOB_STATES)
    }
    summary["not_found"] = sum(item.get("error") == "job_not_found" for item in items)
    summary["errors"] = sum(
        item.get("job_state") is None and item.get("error") != "job_not_found"
        for item in items
    )
    payload = {
        "schema_version": 1,
        "summary": summary,
        "queue": queue,
        "queue_error": queue_error,
        "items": items,
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        "status_batch_id": "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        **payload,
    }


def _validate_status_plan_ids(plan_ids: list[str]) -> list[str]:
    if not isinstance(plan_ids, list) or not 1 <= len(plan_ids) <= 20:
        raise ValueError("plan_ids must contain between 1 and 20 items.")
    if any(
        not isinstance(plan_id, str)
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", plan_id)
        for plan_id in plan_ids
    ):
        raise ValueError("Each plan_id must be a SHA-256 identifier.")
    if len(plan_ids) != len(set(plan_ids)):
        raise ValueError("plan_ids must be unique within a batch.")
    return list(plan_ids)


def _normalize_live_status(plan_id: str, response: Any) -> dict[str, Any]:
    if not isinstance(response, dict):
        return {
            "plan_id": plan_id,
            "found": False,
            "job_state": None,
            "error": "invalid_status_response",
        }
    plugin = response.get("plugin")
    if response.get("found") is True and isinstance(plugin, dict):
        state = plugin.get("jobState")
        job_error = plugin.get("jobError")
        job_error_valid = (
            isinstance(job_error, str)
            and re.fullmatch(r"[A-Za-z0-9_:-]{1,128}", job_error) is not None
            if state == "Failed"
            else job_error is None
        )
        if (
            plugin.get("ok") is True
            and plugin.get("plan_id") == plan_id
            and state in PUBLISH_JOB_STATES
            and job_error_valid
        ):
            return {
                "plan_id": plan_id,
                "found": True,
                "job_state": state,
                "job_error": job_error,
                "error": None,
            }
    error = response.get("error")
    if error is None and isinstance(plugin, dict):
        error = plugin.get("error")
    return {
        "plan_id": plan_id,
        "found": False,
        "job_state": None,
        "error": _bounded_status_error(error or "invalid_status_response"),
    }


def _normalize_queue_telemetry(response: Any) -> dict[str, int | str]:
    if not isinstance(response, dict) or response.get("ok") is not True:
        error = response.get("error") if isinstance(response, dict) else None
        raise ValueError(_bounded_status_error(error or "invalid_plugin_status"))
    names = {
        "capacity": "queueCapacity",
        "pending": "queuePending",
        "running": "queueRunning",
        "available": "queueAvailable",
    }
    values = {name: response.get(wire_name) for name, wire_name in names.items()}
    values["recovered_on_startup"] = response.get("queueRecoveredOnStartup", 0)
    values["interrupted_on_startup"] = response.get("queueInterruptedOnStartup", 0)
    if any(
        not isinstance(value, int) or isinstance(value, bool) or value < 0
        for value in values.values()
    ):
        raise ValueError("invalid_queue_telemetry")
    if (
        response.get("publishEnabled") is not True
        or response.get("queueAuthentication") != QUEUE_AUTHENTICATION_SCHEME
        or not 1 <= values["capacity"] <= 1_000
        or values["pending"] > values["capacity"]
        or values["available"] > values["capacity"]
        or values["running"] > 1
        or values["recovered_on_startup"] > values["capacity"]
        or values["pending"] + values["available"] != values["capacity"]
    ):
        raise ValueError("invalid_queue_telemetry")
    values["authentication"] = QUEUE_AUTHENTICATION_SCHEME
    return values


def _bounded_status_error(value: Any) -> str:
    text = "".join(character if character.isprintable() else " " for character in str(value))
    return (text.strip() or "status_error")[:256]


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
