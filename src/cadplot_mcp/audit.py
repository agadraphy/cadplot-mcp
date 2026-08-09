from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from cadplot_mcp.config import CadPlotConfig
from cadplot_mcp.security import FILE_ATTRIBUTE_REPARSE_POINT, PathPolicyError

MAX_MANIFEST_BYTES = 1024 * 1024
MAX_RECEIPT_BYTES = 64 * 1024
HASH_CHUNK_BYTES = 1024 * 1024


def audit_publish_outputs(manifest_value: str | Path, config: CadPlotConfig) -> dict[str, Any]:
    """Validate one staged job and inspect expected PDFs without writing any files."""
    manifest, job_root = load_staged_manifest(manifest_value, config)
    results = [
        _audit_pdf(item, job_root, config.pdf_page_tolerance_mm)
        for item in manifest["outputs"]
    ]
    valid = sum(item["status"] == "valid" for item in results)
    missing = sum(item["status"] == "missing" for item in results)
    invalid = len(results) - valid - missing
    receipt = read_publish_receipt(manifest_value, config)
    outputs_complete = valid == len(results)
    execution_verified = bool(
        receipt["found"] and receipt["receipt"]["state"] == "succeeded"
    )
    return {
        "schema_version": 1,
        "job_id": manifest["job_id"],
        "plan_id": manifest["plan_id"],
        "complete": outputs_complete,
        "outputs_complete": outputs_complete,
        "execution_verified": execution_verified,
        "publish_verified": outputs_complete and execution_verified,
        "summary": {
            "expected": len(results),
            "valid": valid,
            "missing": missing,
            "invalid": invalid,
        },
        "outputs": results,
        "execution_receipt": receipt,
    }


def read_publish_receipt(
    manifest_value: str | Path,
    config: CadPlotConfig,
) -> dict[str, Any]:
    """Read and cross-check immutable plug-in execution evidence for a staged job."""
    manifest, job_root = load_staged_manifest(manifest_value, config)
    receipt_path = job_root / "receipt.json"
    if not receipt_path.exists():
        return {"found": False, "receipt": None}
    resolved = receipt_path.resolve(strict=True)
    if resolved.parent != job_root.resolve(strict=True) or not resolved.is_file():
        raise PathPolicyError("Publish receipt is outside its job boundary.")
    if resolved.stat().st_size > MAX_RECEIPT_BYTES:
        raise ValueError("Publish receipt exceeds the 64 KiB safety limit.")
    try:
        raw = json.loads(resolved.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Publish receipt is not valid UTF-8 JSON.") from exc
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        raise ValueError("Unsupported publish receipt schema.")
    if raw.get("plan_id") != manifest["plan_id"]:
        raise ValueError("Publish receipt plan identity mismatch.")
    manifest_digest = _sha256(job_root / "manifest.json")
    if raw.get("manifest_sha256") != manifest_digest:
        raise ValueError("Publish receipt manifest digest mismatch.")
    state = raw.get("state")
    error = raw.get("error")
    if state not in {"succeeded", "failed"}:
        raise ValueError("Publish receipt has an invalid terminal state.")
    if state == "succeeded" and error is not None:
        raise ValueError("Successful publish receipt cannot contain an error.")
    if state == "failed" and (
        not isinstance(error, str)
        or not re.fullmatch(r"[A-Za-z0-9_:-]{1,128}", error)
    ):
        raise ValueError("Failed publish receipt has an invalid error code.")
    completed = raw.get("completed_utc")
    try:
        parsed = datetime.fromisoformat(completed)
    except (TypeError, ValueError) as exc:
        raise ValueError("Publish receipt has an invalid completion timestamp.") from exc
    if parsed.tzinfo is None:
        raise ValueError("Publish receipt completion timestamp must include a timezone.")
    return {"found": True, "receipt": raw}


def load_staged_manifest(
    manifest_value: str | Path,
    config: CadPlotConfig,
) -> tuple[dict[str, Any], Path]:
    if config.workspace_root is None:
        raise ValueError("workspace_root must be configured before auditing outputs.")
    workspace_root = config.workspace_root.resolve(strict=True)
    manifest_path = Path(manifest_value).expanduser().resolve(strict=True)
    if manifest_path.name != "manifest.json":
        raise PathPolicyError("Expected a job manifest named manifest.json.")
    if workspace_root not in manifest_path.parents:
        raise PathPolicyError("Job manifest is outside the configured workspace_root.")
    if manifest_path.stat().st_size > MAX_MANIFEST_BYTES:
        raise ValueError("Job manifest exceeds the 1 MiB safety limit.")

    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Job manifest is not valid UTF-8 JSON.") from exc
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        raise ValueError("Unsupported job manifest schema.")
    if raw.get("state") != "staged":
        raise ValueError("Only staged job manifests can be audited.")

    job_root = manifest_path.parent
    if job_root.parent != workspace_root or raw.get("job_id") != job_root.name:
        raise PathPolicyError("Job manifest identity does not match its workspace directory.")
    output_root = (job_root / "output").resolve(strict=True)
    if Path(str(raw.get("output_directory", ""))).resolve(strict=True) != output_root:
        raise PathPolicyError("Manifest output_directory is outside its job boundary.")

    staged_drawing = Path(str(raw.get("staged_drawing", ""))).resolve(strict=True)
    if (
        job_root / "source" not in staged_drawing.parents
        or staged_drawing.suffix.casefold() != ".dwg"
    ):
        raise PathPolicyError("Manifest staged_drawing is outside its job boundary.")
    source_fingerprint = raw.get("source_fingerprint")
    if not isinstance(source_fingerprint, dict) or not isinstance(
        source_fingerprint.get("sha256"), str
    ):
        raise ValueError("Job manifest contains an invalid source fingerprint.")
    if _sha256(staged_drawing) != source_fingerprint["sha256"]:
        raise ValueError("Staged drawing no longer matches the approved source fingerprint.")

    template_assets = raw.get("template_assets", [])
    if not isinstance(template_assets, list) or len(template_assets) > 100:
        raise ValueError("Job manifest contains an invalid template asset collection.")
    template_ids: set[str] = set()
    template_layouts: dict[str, dict[str, Any]] = {}
    template_by_id: dict[str, dict[str, Any]] = {}
    if template_assets:
        template_root_value = job_root / "source" / "templates"
        if _is_reparse(template_root_value):
            raise PathPolicyError("Manifest template asset directory cannot be a reparse point.")
        template_root = template_root_value.resolve(strict=True)
        if not template_root.is_dir():
            raise PathPolicyError("Manifest template asset directory is invalid.")
        for asset in template_assets:
            if not isinstance(asset, dict):
                raise ValueError("Job manifest contains an invalid template asset.")
            asset_id = asset.get("id")
            if not isinstance(asset_id, str) or not re.fullmatch(
                r"[A-Za-z0-9_-]{1,64}", asset_id
            ):
                raise ValueError("Job manifest contains an invalid template asset id.")
            if asset_id in template_ids:
                raise ValueError("Job manifest contains duplicate template asset ids.")
            template_ids.add(asset_id)
            layout = asset.get("layout")
            page_setup = asset.get("page_setup")
            if not _valid_resource_name(layout) or not _valid_resource_name(page_setup):
                raise ValueError("Job manifest template asset has invalid metadata.")
            layout_key = layout.casefold()
            if layout_key in template_layouts:
                raise ValueError("Job manifest contains duplicate template asset layouts.")
            template_layouts[layout_key] = asset
            template_by_id[asset_id] = asset
            staged_template_value = Path(
                str(asset.get("staged_template", ""))
            ).expanduser().absolute()
            if _is_reparse(staged_template_value):
                raise PathPolicyError("Manifest template asset cannot be a reparse point.")
            staged_template = staged_template_value.resolve(strict=True)
            if (
                staged_template.parent != template_root
                or staged_template.suffix.casefold() not in {".dwg", ".dwt"}
                or not staged_template.is_file()
            ):
                raise PathPolicyError("Manifest template asset is outside its job boundary.")
            if (
                not isinstance(asset.get("size_bytes"), int)
                or isinstance(asset["size_bytes"], bool)
                or asset["size_bytes"] < 1
                or not isinstance(asset.get("sha256"), str)
                or re.fullmatch(r"[0-9a-f]{64}", asset["sha256"]) is None
            ):
                raise ValueError("Job manifest template asset has an invalid fingerprint.")
            if staged_template.stat().st_size != asset["size_bytes"] or _sha256(
                staged_template
            ) != asset.get("sha256"):
                raise ValueError("Staged template no longer matches its approved fingerprint.")

    outputs = raw.get("outputs")
    if not isinstance(outputs, list) or not 1 <= len(outputs) <= 5_000:
        raise ValueError("Job manifest must contain between 1 and 5000 outputs.")
    seen: set[Path] = set()
    used_template_ids: set[str] = set()
    for output in outputs:
        if not isinstance(output, dict):
            raise ValueError("Job manifest contains an invalid output entry.")
        pdf = Path(str(output.get("pdf", ""))).resolve(strict=False)
        if pdf.parent != output_root or pdf.suffix.casefold() != ".pdf":
            raise PathPolicyError("Expected PDF path is outside its job output directory.")
        if pdf in seen:
            raise ValueError("Job manifest contains duplicate PDF paths.")
        template_asset_id = output.get("template_asset_id")
        template_layout = output.get("template_layout")
        if template_asset_id is not None:
            asset = template_by_id.get(template_asset_id)
            if asset is None:
                raise ValueError("Job manifest output references an unknown template asset.")
            if (
                template_layout != asset["layout"]
                or output.get("page_setup") != asset["page_setup"]
            ):
                raise ValueError("Job manifest template asset metadata does not match output.")
            used_template_ids.add(template_asset_id)
        elif (
            isinstance(template_layout, str)
            and template_layout.casefold() in template_layouts
        ):
            raise ValueError("Job manifest output is missing its template asset reference.")
        seen.add(pdf)
    if used_template_ids != template_ids:
        raise ValueError("Job manifest contains an unused template asset.")
    return raw, job_root


def _is_reparse(path: Path) -> bool:
    info = path.lstat()
    attributes = getattr(info, "st_file_attributes", 0)
    return path.is_symlink() or bool(attributes & FILE_ATTRIBUTE_REPARSE_POINT)


def _valid_resource_name(value: Any) -> bool:
    return (
        isinstance(value, str)
        and 1 <= len(value) <= 255
        and not value.isspace()
        and not any(ord(character) < 32 or ord(character) == 127 for character in value)
    )


def _audit_pdf(
    item: dict[str, Any], job_root: Path, page_tolerance_mm: float
) -> dict[str, Any]:
    pdf = Path(str(item["pdf"])).resolve(strict=False)
    result = {
        "sheet_index": item.get("sheet_index"),
        "frame_handle": item.get("frame_handle"),
        "pdf": str(pdf),
    }
    if not pdf.exists():
        return {
            **result,
            "status": "missing",
            "size_bytes": None,
            "sha256": None,
            "page_count": None,
        }
    resolved = pdf.resolve(strict=True)
    if resolved.parent != (job_root / "output").resolve(strict=True) or not resolved.is_file():
        return {
            **result,
            "status": "invalid_path",
            "size_bytes": None,
            "sha256": None,
            "page_count": None,
        }
    size = resolved.stat().st_size
    with resolved.open("rb") as stream:
        header = stream.read(5)
    if header != b"%PDF-":
        return {
            **result,
            "status": "invalid_pdf_header",
            "size_bytes": size,
            "sha256": None,
            "page_count": None,
        }
    try:
        with resolved.open("rb") as stream:
            reader = PdfReader(stream, strict=False)
            if reader.is_encrypted:
                return {
                    **result,
                    "status": "encrypted_pdf",
                    "size_bytes": size,
                    "sha256": None,
                    "page_count": None,
                }
            page_count = len(reader.pages)
            if page_count != 1:
                return {
                    **result,
                    "status": "unexpected_page_count",
                    "size_bytes": size,
                    "sha256": None,
                    "page_count": page_count,
                }
            media_box = reader.pages[0].mediabox
            width_points = float(media_box.width)
            height_points = float(media_box.height)
    except (OSError, PdfReadError, TypeError, ValueError):
        return {
            **result,
            "status": "invalid_pdf_structure",
            "size_bytes": size,
            "sha256": None,
            "page_count": None,
        }
    if min(width_points, height_points) <= 0:
        return {
            **result,
            "status": "invalid_page_size",
            "size_bytes": size,
            "sha256": None,
            "page_count": 1,
        }
    geometry = item.get("plot_geometry")
    if not isinstance(geometry, dict):
        return {**result, "status": "invalid_expected_page_size", "page_count": 1}
    expected_values = (geometry.get("paper_width_mm"), geometry.get("paper_height_mm"))
    if not all(isinstance(value, (int, float)) and value > 0 for value in expected_values):
        return {**result, "status": "invalid_expected_page_size", "page_count": 1}
    actual_mm = sorted((width_points * 25.4 / 72, height_points * 25.4 / 72))
    expected_mm = sorted((float(expected_values[0]), float(expected_values[1])))
    dimensions = zip(actual_mm, expected_mm, strict=True)
    if any(abs(actual - expected) > page_tolerance_mm for actual, expected in dimensions):
        return {
            **result,
            "status": "page_size_mismatch",
            "size_bytes": size,
            "sha256": None,
            "page_count": 1,
            "page_width_mm": round(width_points * 25.4 / 72, 3),
            "page_height_mm": round(height_points * 25.4 / 72, 3),
            "expected_paper_width_mm": expected_values[0],
            "expected_paper_height_mm": expected_values[1],
        }
    return {
        **result,
        "status": "valid",
        "size_bytes": size,
        "sha256": _sha256(resolved),
        "page_count": 1,
        "page_width_points": round(width_points, 3),
        "page_height_points": round(height_points, 3),
        "page_width_mm": round(width_points * 25.4 / 72, 3),
        "page_height_mm": round(height_points * 25.4 / 72, 3),
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(HASH_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()
