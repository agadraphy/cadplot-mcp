from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from typing import Any

PlanBuilder = Callable[[str], dict[str, Any]]


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
