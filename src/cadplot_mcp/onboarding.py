from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from cadplot_mcp.models import DrawingInspection


def build_office_inventory_report(inspection: DrawingInspection) -> dict[str, Any]:
    """Summarize exact observed CAD resource names without approving any mapping."""
    frame_observations = [
        {
            "handle": frame.handle,
            "label": frame.label,
            "layer": frame.layer,
            "width_mm": frame.width_mm,
            "height_mm": frame.height_mm,
            "confidence": frame.confidence,
        }
        for frame in inspection.frames
    ]
    paper_setups = [setup for setup in inspection.page_setups if not setup.model_type]
    paper_layouts = [layout for layout in inspection.layouts if not layout.model_type]
    warnings = list(inspection.warnings)
    if not frame_observations:
        warnings.append("No frame labels are available for an authorized paper-profile mapping.")
    if not paper_setups:
        warnings.append("No paper-space named page setups were observed.")
    for setup in paper_setups:
        if not setup.has_verified_one_to_one_scale():
            warnings.append(
                f"Named page setup {setup.name!r} is not verified at 1:1 layout plot scale "
                "and cannot be approved for publishing."
            )
    if not paper_layouts:
        warnings.append("No paper-space layouts were observed as title-block candidates.")

    return {
        "schema_version": 1,
        "drawing": inspection.path,
        "read_only": True,
        "requires_authorized_mapping": True,
        "frame_observations": frame_observations,
        "page_setups": [setup.to_dict() for setup in paper_setups],
        "resource_names": {
            "frame_labels": _unique(frame.label for frame in inspection.frames),
            "frame_layers": _unique(frame.layer for frame in inspection.frames),
            "page_setups": _unique(setup.name for setup in paper_setups),
            "plotters": _unique(setup.plotter for setup in paper_setups),
            "plot_styles": _unique(setup.plot_style for setup in paper_setups),
            "canonical_media": _unique(setup.media_name for setup in paper_setups),
            "paper_space_layouts": _unique(layout.name for layout in paper_layouts),
        },
        "template_layout_candidates": [
            {
                "name": layout.name,
                "plotter": layout.plotter,
                "plot_style": layout.plot_style,
                "canonical_media": layout.media_name,
                "candidate_only": True,
                "requires_exactly_one_floating_viewport_validation": True,
            }
            for layout in paper_layouts
        ],
        "warnings": _unique(warnings),
        "next_action": (
            "Have the authorized CAD reviewer map each frame label to an exact named page setup "
            "and optionally approve one in-drawing template layout. Keep publishing disabled."
        ),
    }


def _unique(values: Iterable[str | None]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value is None:
            continue
        normalized = value.strip()
        key = normalized.casefold()
        if not normalized or key in seen:
            continue
        seen.add(key)
        result.append(normalized)
    return sorted(result, key=str.casefold)
