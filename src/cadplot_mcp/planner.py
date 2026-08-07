from __future__ import annotations

import hashlib
import json
from typing import Any

from cadplot_mcp.config import CadPlotConfig
from cadplot_mcp.models import DrawingInspection


def create_publish_plan(
    inspection: DrawingInspection,
    config: CadPlotConfig,
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

