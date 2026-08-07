from __future__ import annotations

import hashlib
import hmac
import json
import re
from typing import Any

from cadplot_mcp.config import CadPlotConfig
from cadplot_mcp.models import DrawingInspection

PLAN_ID_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


def create_publish_plan(
    inspection: DrawingInspection,
    config: CadPlotConfig,
    *,
    drawing_fingerprint: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a deterministic, read-only plan from an inspection result."""
    sheets: list[dict[str, Any]] = []
    warnings = list(inspection.warnings)

    for frame in inspection.frames:
        profile = config.match_paper_profile(frame.label)
        if profile is None:
            warnings.append(
                f"Frame {frame.handle or '<no handle>'} has no profile for label {frame.label!r}."
            )
            sheets.append(
                {
                    "frame_handle": frame.handle,
                    "label": frame.label,
                    "status": "unmatched",
                    "profile": None,
                }
            )
            continue

        sheets.append(
            {
                "frame_handle": frame.handle,
                "label": frame.label,
                "status": "matched",
                "profile": {
                    "id": profile.id,
                    "page_setup": profile.page_setup,
                    "plotter": profile.plotter,
                    "plot_style": profile.plot_style,
                },
            }
        )

    payload = {
        "schema_version": 1,
        "mode": "dry-run",
        "drawing": inspection.path,
        "drawing_fingerprint": drawing_fingerprint,
        "sheets": sheets,
        "warnings": warnings,
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    plan_id = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return {
        "plan_id": f"sha256:{plan_id}",
        "ready": bool(sheets) and all(sheet["status"] == "matched" for sheet in sheets),
        **payload,
    }


def validate_publish_plan(plan: dict[str, Any]) -> None:
    """Reject malformed, stale, modified, or non-dry-run publish plans."""
    if plan.get("schema_version") != 1:
        raise ValueError("Unsupported publish-plan schema version.")
    if plan.get("mode") != "dry-run":
        raise ValueError("Only dry-run publish plans can be previewed.")
    if plan.get("ready") is not True:
        raise ValueError("Publish plan is not ready; resolve every blocker first.")

    fingerprint = plan.get("drawing_fingerprint")
    if not isinstance(fingerprint, dict):
        raise ValueError("Publish plan is not bound to a drawing fingerprint.")
    sha256 = fingerprint.get("sha256")
    if not isinstance(sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise ValueError("Publish plan contains an invalid drawing fingerprint.")
    if not isinstance(fingerprint.get("size_bytes"), int) or fingerprint["size_bytes"] < 0:
        raise ValueError("Publish plan contains an invalid drawing size.")
    if not isinstance(fingerprint.get("modified_ns"), int) or fingerprint["modified_ns"] < 0:
        raise ValueError("Publish plan contains an invalid drawing timestamp.")

    sheets = plan.get("sheets")
    if not isinstance(sheets, list) or not sheets:
        raise ValueError("Publish plan must contain at least one sheet.")
    if len(sheets) > 5_000:
        raise ValueError("Publish plan exceeds the 5000-sheet safety limit.")
    if any(not isinstance(sheet, dict) or sheet.get("status") != "matched" for sheet in sheets):
        raise ValueError("Every sheet must have an approved paper-profile match.")

    plan_id = plan.get("plan_id")
    if not isinstance(plan_id, str) or not PLAN_ID_PATTERN.fullmatch(plan_id):
        raise ValueError("Publish plan has an invalid plan_id.")

    payload = {
        "schema_version": plan["schema_version"],
        "mode": plan["mode"],
        "drawing": plan.get("drawing"),
        "drawing_fingerprint": plan.get("drawing_fingerprint"),
        "sheets": sheets,
        "warnings": plan.get("warnings"),
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    expected = "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    if not hmac.compare_digest(plan_id, expected):
        raise ValueError("Publish plan hash mismatch; recreate the plan before continuing.")
