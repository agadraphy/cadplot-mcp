from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from cadplot_mcp.config import CadPlotConfig
from cadplot_mcp.security import PathPolicyError

MAX_MANIFEST_BYTES = 1024 * 1024
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
    return {
        "schema_version": 1,
        "job_id": manifest["job_id"],
        "plan_id": manifest["plan_id"],
        "complete": valid == len(results),
        "summary": {
            "expected": len(results),
            "valid": valid,
            "missing": missing,
            "invalid": invalid,
        },
        "outputs": results,
    }


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

    outputs = raw.get("outputs")
    if not isinstance(outputs, list) or not 1 <= len(outputs) <= 5_000:
        raise ValueError("Job manifest must contain between 1 and 5000 outputs.")
    seen: set[Path] = set()
    for output in outputs:
        if not isinstance(output, dict):
            raise ValueError("Job manifest contains an invalid output entry.")
        pdf = Path(str(output.get("pdf", ""))).resolve(strict=False)
        if pdf.parent != output_root or pdf.suffix.casefold() != ".pdf":
            raise PathPolicyError("Expected PDF path is outside its job output directory.")
        if pdf in seen:
            raise ValueError("Job manifest contains duplicate PDF paths.")
        seen.add(pdf)
    return raw, job_root


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
