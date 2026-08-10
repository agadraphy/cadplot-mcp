from __future__ import annotations

import hashlib
import json
import re
import struct
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any

from pypdf import PdfReader
from pypdf.errors import LimitReachedError, PdfReadError

from cadplot_mcp.config import CadPlotConfig
from cadplot_mcp.fingerprint import fingerprint_drawing
from cadplot_mcp.security import (
    FILE_ATTRIBUTE_REPARSE_POINT,
    PathPolicyError,
    require_plain_directory_path,
)

MAX_MANIFEST_BYTES = 1024 * 1024
MAX_RECEIPT_BYTES = 64 * 1024
MAX_OUTPUT_PDF_BYTES = 128 * 1024 * 1024
MAX_DECODED_PAGE_CONTENT_BYTES = 64 * 1024 * 1024
MAX_MARKING_SCAN_BYTES = 8 * 1024 * 1024
HASH_CHUNK_BYTES = 1024 * 1024
RECEIPT_OUTPUT_DOMAIN = b"cadplot-receipt-outputs-v1\0"
PDF_WHITESPACE = frozenset({0, 9, 10, 12, 13, 32})
PDF_DELIMITERS = frozenset(b"()<>[]{}/%")
PDF_MARKING_OPERATORS = frozenset(
    {
        b"S",
        b"s",
        b"f",
        b"F",
        b"f*",
        b"B",
        b"B*",
        b"b",
        b"b*",
        b"sh",
        b"Tj",
        b"TJ",
        b"'",
        b'"',
        b"Do",
        b"BI",
        b"INLINE IMAGE",
    }
)


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
    receipt_output_binding_verified = False
    if (
        outputs_complete
        and receipt["found"]
        and receipt["receipt"]["state"] == "succeeded"
    ):
        output_evidence = [
            {
                "sheet_index": item["sheet_index"],
                "file": Path(item["pdf"]).name,
                "size_bytes": item["size_bytes"],
                "sha256": item["sha256"],
            }
            for item in results
        ]
        receipt_output_binding_verified = bool(
            receipt["receipt"]["output_count"] == len(output_evidence)
            and receipt["receipt"]["outputs_sha256"]
            == build_receipt_output_digest(output_evidence)
        )
    execution_verified = bool(
        receipt["found"]
        and receipt["receipt"]["state"] == "succeeded"
        and receipt_output_binding_verified
    )
    source = audit_source_drawing(manifest, config)
    source_unchanged = source["status"] == "unchanged"
    return {
        "schema_version": 1,
        "job_id": manifest["job_id"],
        "plan_id": manifest["plan_id"],
        "complete": outputs_complete,
        "source_unchanged": source_unchanged,
        "source": source,
        "outputs_complete": outputs_complete,
        "receipt_output_binding_verified": receipt_output_binding_verified,
        "execution_verified": execution_verified,
        "publish_verified": outputs_complete and execution_verified and source_unchanged,
        "summary": {
            "expected": len(results),
            "valid": valid,
            "missing": missing,
            "invalid": invalid,
        },
        "outputs": results,
        "execution_receipt": receipt,
    }


def audit_source_drawing(
    manifest: dict[str, Any], config: CadPlotConfig
) -> dict[str, Any]:
    """Revalidate that the published snapshot is still the current authorized source."""
    expected = manifest.get("source_fingerprint")
    if not isinstance(expected, dict):
        raise ValueError("Job manifest contains an invalid source fingerprint.")
    expected_sha256 = expected.get("sha256")
    if (
        not isinstance(expected_sha256, str)
        or re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None
    ):
        raise ValueError("Job manifest contains an invalid source fingerprint.")
    source_path = config.path_policy.require_allowed(
        str(manifest.get("source_drawing", "")), suffix=".dwg"
    )
    if not source_path.is_file():
        raise ValueError("Job manifest source drawing is not a file.")
    current = fingerprint_drawing(source_path, config.path_policy)
    expected_size = expected["size_bytes"]
    expected_modified_ns = expected["modified_ns"]
    matches = {
        "sha256": expected_sha256 == current["sha256"],
        "size_bytes": expected_size == current["size_bytes"],
        "modified_ns": expected_modified_ns == current["modified_ns"],
    }
    return {
        "status": "unchanged" if all(matches.values()) else "changed",
        "expected_sha256": expected_sha256,
        "current_sha256": current["sha256"],
        "expected_size_bytes": expected_size,
        "current_size_bytes": current["size_bytes"],
        "expected_modified_ns": expected_modified_ns,
        "current_modified_ns": current["modified_ns"],
        "matches": matches,
    }


def pdf_page_marking_evidence(page: Any) -> dict[str, int]:
    """Count decoded content bytes and operators that can place marks on one PDF page."""
    contents = page.get_contents()
    if contents is None:
        return {"content_stream_bytes": 0, "marking_operator_count": 0}
    content_data = contents.get_data()
    if len(content_data) > MAX_DECODED_PAGE_CONTENT_BYTES:
        raise LimitReachedError(
            "Decoded page content exceeds the 64 MiB CadPlot audit safety limit."
        )
    scan_data = content_data[:MAX_MARKING_SCAN_BYTES]
    try:
        marking_operator_count = _count_marking_operators(scan_data)
    except PdfReadError:
        if len(content_data) > MAX_MARKING_SCAN_BYTES:
            raise LimitReachedError(
                "No complete marking evidence was found within the 8 MiB scan limit."
            ) from None
        raise
    if marking_operator_count < 1 and len(content_data) > MAX_MARKING_SCAN_BYTES:
        raise LimitReachedError(
            "No marking operator was found within the 8 MiB scan limit."
        )
    return {
        "content_stream_bytes": len(content_data),
        "marking_operator_count": marking_operator_count,
    }


def _count_marking_operators(content: bytes) -> int:
    """Lex PDF content without materializing pypdf's potentially huge operation list."""
    index = 0
    length = len(content)
    containers: list[bytes] = []
    marking_found = False
    while index < length:
        current = content[index]
        if current in PDF_WHITESPACE:
            index += 1
            continue
        if current == ord("%"):
            index += 1
            while index < length and content[index] not in {10, 13}:
                index += 1
            continue
        if current == ord("("):
            index = _skip_pdf_literal_string(content, index + 1)
            continue
        if current == ord("<") and index + 1 < length and content[index + 1] == ord("<"):
            containers.append(b">>")
            index += 2
            continue
        if current == ord("<"):
            index += 1
            while index < length and content[index] != ord(">"):
                index += 1
            if index >= length:
                raise PdfReadError("Unterminated hexadecimal string in PDF content stream.")
            index += 1
            continue
        if current == ord("/"):
            index += 1
            while (
                index < length
                and content[index] not in PDF_WHITESPACE
                and content[index] not in PDF_DELIMITERS
            ):
                index += 1
            continue
        if current == ord("["):
            containers.append(b"]")
            index += 1
            continue
        if current == ord("]"):
            if not containers or containers.pop() != b"]":
                raise PdfReadError("Unbalanced array in PDF content stream.")
            index += 1
            continue
        if current == ord(">") and index + 1 < length and content[index + 1] == ord(">"):
            if not containers or containers.pop() != b">>":
                raise PdfReadError("Unbalanced dictionary in PDF content stream.")
            index += 2
            continue
        if current in PDF_DELIMITERS:
            raise PdfReadError("Unexpected delimiter in PDF content stream.")
        start = index
        while (
            index < length
            and content[index] not in PDF_WHITESPACE
            and content[index] not in PDF_DELIMITERS
        ):
            index += 1
        operator = content[start:index]
        if not containers and operator in PDF_MARKING_OPERATORS:
            marking_found = True
            if operator == b"BI":
                return 1
    if containers:
        raise PdfReadError("Unterminated container in PDF content stream.")
    return int(marking_found)


def _skip_pdf_literal_string(content: bytes, index: int) -> int:
    depth = 1
    length = len(content)
    while index < length and depth:
        current = content[index]
        if current == ord("\\"):
            index += 2
            continue
        if current == ord("("):
            depth += 1
        elif current == ord(")"):
            depth -= 1
        index += 1
    if depth:
        raise PdfReadError("Unterminated literal string in PDF content stream.")
    return index


def read_publish_receipt(
    manifest_value: str | Path,
    config: CadPlotConfig,
) -> dict[str, Any]:
    """Read and cross-check immutable plug-in execution evidence for a staged job."""
    manifest, job_root = load_staged_manifest(manifest_value, config)
    receipt_path = job_root / "receipt.json"
    if not receipt_path.exists():
        return {"found": False, "receipt": None}
    if _is_reparse(receipt_path):
        raise PathPolicyError("Publish receipt cannot be a symlink or reparse point.")
    resolved = receipt_path.resolve(strict=True)
    if resolved.parent != job_root.resolve(strict=True) or not resolved.is_file():
        raise PathPolicyError("Publish receipt is outside its job boundary.")
    if resolved.stat().st_size > MAX_RECEIPT_BYTES:
        raise ValueError("Publish receipt exceeds the 64 KiB safety limit.")
    try:
        raw = json.loads(resolved.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Publish receipt is not valid UTF-8 JSON.") from exc
    if not isinstance(raw, dict) or raw.get("schema_version") != 2:
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
    common_fields = {
        "schema_version",
        "plan_id",
        "manifest_sha256",
        "state",
        "output_count",
        "outputs_sha256",
        "completed_utc",
    }
    if state == "succeeded":
        if set(raw) != common_fields or error is not None:
            raise ValueError("Successful publish receipt fields are not exact.")
        if (
            not isinstance(raw.get("output_count"), int)
            or isinstance(raw["output_count"], bool)
            or raw["output_count"] != len(manifest["outputs"])
            or not isinstance(raw.get("outputs_sha256"), str)
            or re.fullmatch(r"[0-9a-f]{64}", raw["outputs_sha256"]) is None
        ):
            raise ValueError("Successful publish receipt output binding is invalid.")
    else:
        if set(raw) != common_fields | {"error"}:
            raise ValueError("Failed publish receipt fields are not exact.")
        if (
            not isinstance(error, str)
            or not re.fullmatch(r"[A-Za-z0-9_:-]{1,128}", error)
        ):
            raise ValueError("Failed publish receipt has an invalid error code.")
        if raw.get("output_count") != 0 or raw.get("outputs_sha256") is not None:
            raise ValueError("Failed publish receipt cannot bind successful outputs.")
    completed = raw.get("completed_utc")
    try:
        parsed = datetime.fromisoformat(completed)
    except (TypeError, ValueError) as exc:
        raise ValueError("Publish receipt has an invalid completion timestamp.") from exc
    if parsed.tzinfo is None:
        raise ValueError("Publish receipt completion timestamp must include a timezone.")
    return {"found": True, "receipt": raw}


def build_receipt_output_digest(outputs: list[dict[str, Any]]) -> str:
    """Build the cross-runtime canonical digest used by receipt schema v2."""
    if not isinstance(outputs, list) or not 1 <= len(outputs) <= 5_000:
        raise ValueError("Receipt output evidence must contain between 1 and 5000 items.")
    digest = hashlib.sha256()
    digest.update(RECEIPT_OUTPUT_DOMAIN)
    digest.update(struct.pack(">q", len(outputs)))
    for item in outputs:
        if not isinstance(item, dict):
            raise ValueError("Receipt output evidence item is invalid.")
        sheet_index = item.get("sheet_index")
        file_name = item.get("file")
        size_bytes = item.get("size_bytes")
        sha256 = item.get("sha256")
        if (
            not isinstance(sheet_index, int)
            or isinstance(sheet_index, bool)
            or sheet_index < 1
            or not isinstance(file_name, str)
            or not file_name
            or Path(file_name).name != file_name
            or Path(file_name).suffix.casefold() != ".pdf"
            or any(ord(character) < 32 or ord(character) == 127 for character in file_name)
            or not isinstance(size_bytes, int)
            or isinstance(size_bytes, bool)
            or not 1 <= size_bytes <= (2**63 - 1)
            or not isinstance(sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", sha256) is None
        ):
            raise ValueError("Receipt output evidence item is invalid.")
        file_bytes = file_name.encode("utf-8")
        digest.update(struct.pack(">q", sheet_index))
        digest.update(struct.pack(">q", len(file_bytes)))
        digest.update(file_bytes)
        digest.update(struct.pack(">q", size_bytes))
        digest.update(bytes.fromhex(sha256))
    return digest.hexdigest()


def load_staged_manifest(
    manifest_value: str | Path,
    config: CadPlotConfig,
) -> tuple[dict[str, Any], Path]:
    if config.workspace_root is None:
        raise ValueError("workspace_root must be configured before auditing outputs.")
    workspace_value = require_plain_directory_path(config.workspace_root)
    workspace_root = workspace_value.resolve(strict=True)
    if workspace_value != workspace_root:
        raise PathPolicyError("Configured workspace_root resolves through a filesystem redirect.")
    manifest_candidate = Path(manifest_value).expanduser().absolute()
    if manifest_candidate.name != "manifest.json":
        raise PathPolicyError("Expected a job manifest named manifest.json.")
    require_plain_directory_path(manifest_candidate.parent)
    if not manifest_candidate.is_file():
        raise PathPolicyError("Job manifest is not a regular file.")
    if _is_reparse(manifest_candidate):
        raise PathPolicyError("Job manifest cannot be a symlink or reparse point.")
    manifest_path = manifest_candidate.resolve(strict=True)
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
    source_root_value = job_root / "source"
    output_root_value = job_root / "output"
    if (
        _is_reparse(job_root)
        or _is_reparse(source_root_value)
        or _is_reparse(output_root_value)
    ):
        raise PathPolicyError("Job workspace cannot contain a redirected directory.")
    output_root = output_root_value.resolve(strict=True)
    if Path(str(raw.get("output_directory", ""))).resolve(strict=True) != output_root:
        raise PathPolicyError("Manifest output_directory is outside its job boundary.")

    staged_drawing_value = Path(str(raw.get("staged_drawing", ""))).expanduser().absolute()
    if _is_reparse(staged_drawing_value):
        raise PathPolicyError("Manifest staged_drawing cannot be a symlink or reparse point.")
    staged_drawing = staged_drawing_value.resolve(strict=True)
    if (
        job_root / "source" not in staged_drawing.parents
        or staged_drawing.suffix.casefold() != ".dwg"
    ):
        raise PathPolicyError("Manifest staged_drawing is outside its job boundary.")
    source_fingerprint = raw.get("source_fingerprint")
    if (
        not isinstance(source_fingerprint, dict)
        or not isinstance(source_fingerprint.get("sha256"), str)
        or re.fullmatch(r"[0-9a-f]{64}", source_fingerprint["sha256"]) is None
        or not isinstance(source_fingerprint.get("size_bytes"), int)
        or isinstance(source_fingerprint["size_bytes"], bool)
        or source_fingerprint["size_bytes"] < 1
        or not isinstance(source_fingerprint.get("modified_ns"), int)
        or isinstance(source_fingerprint["modified_ns"], bool)
        or source_fingerprint["modified_ns"] < 0
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
        pdf_value = Path(str(output.get("pdf", ""))).expanduser().absolute()
        if pdf_value.parent != output_root_value or pdf_value.suffix.casefold() != ".pdf":
            raise PathPolicyError("Expected PDF path is outside its job output directory.")
        if pdf_value.exists() and _is_reparse(pdf_value):
            raise PathPolicyError("Expected PDF cannot be a symlink or reparse point.")
        pdf = pdf_value.resolve(strict=False)
        if pdf.parent != output_root:
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
    pdf_value = Path(str(item["pdf"])).expanduser().absolute()
    pdf = pdf_value.resolve(strict=False)
    output_root_value = job_root / "output"
    result = {
        "sheet_index": item.get("sheet_index"),
        "frame_handle": item.get("frame_handle"),
        "pdf": str(pdf),
    }
    if not pdf_value.exists():
        return {
            **result,
            "status": "missing",
            "size_bytes": None,
            "sha256": None,
            "page_count": None,
        }
    if _is_reparse(output_root_value) or _is_reparse(pdf_value):
        return {
            **result,
            "status": "invalid_path",
            "size_bytes": None,
            "sha256": None,
            "page_count": None,
        }
    resolved = pdf_value.resolve(strict=True)
    if resolved.parent != output_root_value.resolve(strict=True) or not resolved.is_file():
        return {
            **result,
            "status": "invalid_path",
            "size_bytes": None,
            "sha256": None,
            "page_count": None,
        }
    before = resolved.stat()
    size = before.st_size
    if size > MAX_OUTPUT_PDF_BYTES:
        return {
            **result,
            "status": "pdf_too_large",
            "size_bytes": size,
            "sha256": None,
            "page_count": None,
            "max_size_bytes": MAX_OUTPUT_PDF_BYTES,
        }
    try:
        pdf_bytes = resolved.read_bytes()
        after_read = resolved.stat()
    except OSError:
        return {
            **result,
            "status": "invalid_pdf_structure",
            "size_bytes": size,
            "sha256": None,
            "page_count": None,
        }
    if len(pdf_bytes) != size or _file_snapshot_changed(before, after_read):
        return {
            **result,
            "status": "pdf_changed_during_audit",
            "size_bytes": len(pdf_bytes),
            "sha256": None,
            "page_count": None,
        }
    if pdf_bytes[:5] != b"%PDF-":
        return {
            **result,
            "status": "invalid_pdf_header",
            "size_bytes": size,
            "sha256": None,
            "page_count": None,
        }
    page_count: int | None = None
    try:
        reader = PdfReader(BytesIO(pdf_bytes), strict=False)
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
        page = reader.pages[0]
        media_box = page.mediabox
        width_points = float(media_box.width)
        height_points = float(media_box.height)
        rotation = int(page.get("/Rotate", 0) or 0)
        marking = pdf_page_marking_evidence(page)
        content_stream_bytes = marking["content_stream_bytes"]
        marking_operator_count = marking["marking_operator_count"]
    except LimitReachedError:
        return {
            **result,
            "status": "pdf_content_limit_exceeded",
            "size_bytes": size,
            "sha256": None,
            "page_count": page_count,
            "max_decoded_content_bytes": MAX_DECODED_PAGE_CONTENT_BYTES,
            "max_marking_scan_bytes": MAX_MARKING_SCAN_BYTES,
        }
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
    if rotation % 90 != 0:
        return {
            **result,
            "status": "invalid_page_rotation",
            "size_bytes": size,
            "sha256": None,
            "page_count": 1,
        }
    if rotation % 180 != 0:
        width_points, height_points = height_points, width_points
    geometry = item.get("plot_geometry")
    if not isinstance(geometry, dict):
        return {**result, "status": "invalid_expected_page_size", "page_count": 1}
    expected_values = (geometry.get("paper_width_mm"), geometry.get("paper_height_mm"))
    expected_rotation = geometry.get("rotation_degrees")
    if (
        expected_rotation not in {0, 90}
        or isinstance(expected_rotation, bool)
        or not all(
            isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0
            for value in expected_values
        )
    ):
        return {**result, "status": "invalid_expected_page_size", "page_count": 1}
    actual_mm = (width_points * 25.4 / 72, height_points * 25.4 / 72)
    expected_mm = (
        (float(expected_values[0]), float(expected_values[1]))
        if expected_rotation == 0
        else (float(expected_values[1]), float(expected_values[0]))
    )
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
            "expected_paper_width_mm": expected_mm[0],
            "expected_paper_height_mm": expected_mm[1],
        }
    if marking_operator_count < 1:
        return {
            **result,
            "status": "blank_pdf_page",
            "size_bytes": size,
            "sha256": None,
            "page_count": 1,
            "content_stream_bytes": content_stream_bytes,
            "marking_operator_count": 0,
        }
    try:
        after_parse = resolved.stat()
        current_path = pdf_value.resolve(strict=True)
        path_redirected = (
            _is_reparse(output_root_value)
            or _is_reparse(pdf_value)
            or current_path != resolved
        )
    except OSError:
        after_parse = None
        path_redirected = True
    if (
        after_parse is None
        or path_redirected
        or _file_snapshot_changed(before, after_parse)
    ):
        return {
            **result,
            "status": "pdf_changed_during_audit",
            "size_bytes": size,
            "sha256": None,
            "page_count": None,
        }
    return {
        **result,
        "status": "valid",
        "size_bytes": size,
        "sha256": hashlib.sha256(pdf_bytes).hexdigest(),
        "page_count": 1,
        "page_width_points": round(width_points, 3),
        "page_height_points": round(height_points, 3),
        "page_width_mm": round(width_points * 25.4 / 72, 3),
        "page_height_mm": round(height_points * 25.4 / 72, 3),
        "content_stream_bytes": content_stream_bytes,
        "marking_operator_count": marking_operator_count,
    }


def _file_snapshot_changed(before: Any, after: Any) -> bool:
    return any(
        getattr(before, field, None) != getattr(after, field, None)
        for field in ("st_dev", "st_ino", "st_size", "st_mtime_ns")
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(HASH_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()
