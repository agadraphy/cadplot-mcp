from __future__ import annotations

import hashlib
import hmac
import json
import re
from typing import Any

from cadplot_mcp.config import CadPlotConfig, PaperProfile
from cadplot_mcp.models import DrawingInspection, FrameCandidate, PageSetupSummary
from cadplot_mcp.paper import parse_paper_size

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

    existing_layouts = {layout.name.casefold() for layout in inspection.layouts}
    for index, frame in enumerate(inspection.frames, start=1):
        target_layout = _target_layout_name(index, frame, config)
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
                    "target_layout": target_layout,
                }
            )
            continue

        profile_payload = _profile_payload(profile)
        page_setup_error = _page_setup_error(profile, inspection.page_setups, config)
        if page_setup_error is not None:
            warnings.append(f"Frame {frame.handle or '<no handle>'}: {page_setup_error}")
            sheets.append(
                {
                    "frame_handle": frame.handle,
                    "label": frame.label,
                    "status": "page_setup_mismatch",
                    "profile": profile_payload,
                    "target_layout": target_layout,
                    "plot_geometry": None,
                }
            )
            continue

        plot_geometry = _derive_plot_geometry(frame, config)
        if plot_geometry is None:
            warnings.append(
                f"Frame {frame.handle or '<no handle>'} does not resolve to an allowed scale."
            )
            sheets.append(
                {
                    "frame_handle": frame.handle,
                    "label": frame.label,
                    "status": "unsupported_scale",
                    "profile": profile_payload,
                    "target_layout": target_layout,
                    "plot_geometry": None,
                }
            )
            continue

        if target_layout.casefold() in existing_layouts:
            warnings.append(
                f"Frame {frame.handle or '<no handle>'} target layout {target_layout!r} "
                "already exists."
            )
            sheets.append(
                {
                    "frame_handle": frame.handle,
                    "label": frame.label,
                    "status": "layout_conflict",
                    "profile": profile_payload,
                    "target_layout": target_layout,
                    "plot_geometry": plot_geometry,
                }
            )
            continue

        sheets.append(
            {
                "frame_handle": frame.handle,
                "label": frame.label,
                "status": "matched",
                "profile": profile_payload,
                "target_layout": target_layout,
                "plot_geometry": plot_geometry,
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


def _derive_plot_geometry(frame: FrameCandidate, config: CadPlotConfig) -> dict[str, Any] | None:
    paper = parse_paper_size(frame.label)
    if paper is None:
        return None
    geometry_width = frame.max_point[0] - frame.min_point[0]
    geometry_height = frame.max_point[1] - frame.min_point[1]
    if min(geometry_width, geometry_height) <= 0:
        return None

    candidates = [
        (0, paper.width_mm, paper.height_mm),
        (90, paper.height_mm, paper.width_mm),
    ]
    best: tuple[float, float, int] | None = None
    for rotation, paper_width, paper_height in candidates:
        width_scale = geometry_width * config.drawing_unit_mm / paper_width
        height_scale = geometry_height * config.drawing_unit_mm / paper_height
        average = (width_scale + height_scale) / 2
        mismatch = abs(width_scale - height_scale) / average
        candidate = (mismatch, average, rotation)
        if best is None or candidate < best:
            best = candidate
    if best is None or best[0] > config.scale_tolerance_ratio:
        return None

    derived = best[1]
    denominator = min(config.scale_denominators, key=lambda item: abs(item - derived))
    if abs(denominator - derived) / denominator > config.scale_tolerance_ratio:
        return None
    return {
        "window": {
            "min_x": frame.min_point[0],
            "min_y": frame.min_point[1],
            "max_x": frame.max_point[0],
            "max_y": frame.max_point[1],
        },
        "rotation_degrees": best[2],
        "scale_denominator": denominator,
        "derived_scale_denominator": round(derived, 6),
        "drawing_unit_mm": config.drawing_unit_mm,
        "paper_width_mm": paper.width_mm,
        "paper_height_mm": paper.height_mm,
    }


def _profile_payload(profile: PaperProfile) -> dict[str, str | None]:
    return {
        "id": profile.id,
        "page_setup": profile.page_setup,
        "plotter": profile.plotter,
        "plot_style": profile.plot_style,
        "canonical_media": profile.canonical_media,
    }


def _page_setup_error(
    profile: PaperProfile,
    page_setups: list[PageSetupSummary],
    config: CadPlotConfig,
) -> str | None:
    if not config.require_page_setup_match:
        return None
    matches = [
        setup for setup in page_setups if setup.name.casefold() == profile.page_setup.casefold()
    ]
    if not matches:
        return f"required page setup {profile.page_setup!r} was not found."
    setup = matches[0]
    if (setup.plotter or "").casefold() != profile.plotter.casefold():
        return (
            f"page setup {profile.page_setup!r} uses plotter {setup.plotter!r}, "
            f"expected {profile.plotter!r}."
        )
    if (setup.plot_style or "").casefold() != profile.plot_style.casefold():
        return (
            f"page setup {profile.page_setup!r} uses plot style {setup.plot_style!r}, "
            f"expected {profile.plot_style!r}."
        )
    if profile.canonical_media is not None and setup.media_name != profile.canonical_media:
        return (
            f"page setup {profile.page_setup!r} uses canonical media {setup.media_name!r}, "
            f"expected exact case-sensitive value {profile.canonical_media!r}."
        )
    return None


def _target_layout_name(index: int, frame: FrameCandidate, config: CadPlotConfig) -> str:
    safe_handle = re.sub(r"[^A-Za-z0-9_-]+", "_", frame.handle or "NOHANDLE").strip("_")
    return f"{config.layout_prefix}_{index:04d}_{safe_handle or 'NOHANDLE'}"[:255]
