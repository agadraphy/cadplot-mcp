from __future__ import annotations

import hashlib
import json
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cadplot_mcp.config import CadPlotConfig
from cadplot_mcp.fingerprint import fingerprint_drawing
from cadplot_mcp.planner import validate_publish_plan
from cadplot_mcp.security import PathPolicy, require_plain_directory_path


def stage_publish_job(
    plan: dict[str, Any],
    config: CadPlotConfig,
    *,
    approved_plan_id: str,
) -> dict[str, Any]:
    """Copy an approved drawing into an isolated job folder and write an audit manifest."""
    validate_publish_plan(plan)
    if approved_plan_id != plan["plan_id"]:
        raise ValueError("approved_plan_id does not match the current publish plan.")
    if config.workspace_root is None:
        raise ValueError("workspace_root must be configured before staging a publish job.")

    source = config.path_policy.require_allowed(plan["drawing"], suffix=".dwg")
    current_fingerprint = fingerprint_drawing(source, config.path_policy)
    if current_fingerprint != plan.get("drawing_fingerprint"):
        raise ValueError("Drawing changed after planning; inspect it again before staging.")

    workspace_root = require_plain_directory_path(config.workspace_root)
    workspace_root.mkdir(parents=True, exist_ok=True)
    resolved_root = workspace_root.resolve(strict=True)
    if resolved_root != workspace_root:
        raise ValueError("workspace_root changed or resolved through a filesystem redirect.")

    job_id = f"job-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:12]}"
    job_root = resolved_root / job_id
    source_dir = job_root / "source"
    output_dir = job_root / "output"
    source_dir.mkdir(parents=True, exist_ok=False)
    output_dir.mkdir()

    staged_drawing = source_dir / source.name
    shutil.copy2(source, staged_drawing)
    staged_digest = fingerprint_drawing(staged_drawing, _single_root_policy(source_dir))
    if staged_digest["sha256"] != current_fingerprint["sha256"]:
        raise RuntimeError("Staged DWG copy failed content verification.")

    outputs = [
        {
            "sheet_index": index,
            "frame_handle": sheet["frame_handle"],
            "pdf": str(output_dir / _pdf_name(source.stem, index, sheet["profile"]["id"])),
            "target_layout": sheet["target_layout"],
            "page_setup": sheet["profile"]["page_setup"],
            "plotter": sheet["profile"]["plotter"],
            "plot_style": sheet["profile"]["plot_style"],
            "canonical_media": sheet["profile"]["canonical_media"],
            "plot_geometry": sheet["plot_geometry"],
            "status": "pending",
        }
        for index, sheet in enumerate(plan["sheets"], start=1)
    ]
    manifest = {
        "schema_version": 1,
        "job_id": job_id,
        "state": "staged",
        "created_utc": datetime.now(UTC).isoformat(),
        "plan_id": plan["plan_id"],
        "source_drawing": str(source),
        "source_fingerprint": current_fingerprint,
        "staged_drawing": str(staged_drawing),
        "output_directory": str(output_dir),
        "outputs": outputs,
    }
    manifest_path = job_root / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    manifest_sha256 = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    return {
        **manifest,
        "manifest": str(manifest_path),
        "manifest_sha256": manifest_sha256,
    }


def _single_root_policy(root: Path) -> PathPolicy:
    return PathPolicy.from_roots([root])


def _pdf_name(stem: str, index: int, profile_id: str) -> str:
    safe_stem = "".join(
        character if character.isalnum() or character in "-_" else "_" for character in stem
    )
    safe_profile = "".join(
        character if character.isalnum() or character in "-_" else "_"
        for character in profile_id
    )
    return f"{index:04d}-{safe_stem}-{safe_profile}.pdf"
