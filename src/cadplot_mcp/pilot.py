from __future__ import annotations

import hashlib
import json
import math
import re
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from cadplot_mcp.audit import audit_publish_outputs, load_staged_manifest
from cadplot_mcp.config import CadPlotConfig
from cadplot_mcp.security import FILE_ATTRIBUTE_REPARSE_POINT

SHA256 = re.compile(r"[0-9a-f]{64}")
PLAN_ID = re.compile(r"sha256:[0-9a-f]{64}")
COMMIT = re.compile(r"[0-9a-f]{40}")
MAX_REFERENCE_PDF_BYTES = 128 * 1024 * 1024
VISUAL_CHECKS = {
    "orientation",
    "crop",
    "viewport_scale",
    "lineweights",
    "plot_style",
    "fonts",
    "title_block",
}
QUEUE_AUTHENTICATION_SCHEME = "windows-dpapi-current-user+hmac-sha256-v1"
RUN_FIELDS = {
    "autocad_release",
    "product",
    "adapter",
    "build_commit",
    "plugin_sha256",
    "runtime_series",
    "queue_authentication",
    "licensed",
    "authorized_test_asset",
    "plan_id",
    "manifest_sha256",
    "receipt_manifest_sha256",
    "receipt_state",
    "source_sha256_before",
    "source_sha256_after",
    "staged_sha256_before",
    "staged_sha256_after",
    "template_assets",
    "published_pdf",
    "visual_reference",
    "publish_verified",
    "restart_receipt_verified",
    "visual_checks",
    "approved_by",
    "completed_utc",
}
VISUAL_REFERENCE_FIELDS = {
    "sha256",
    "size_bytes",
    "page_count",
    "page_width_mm",
    "page_height_mm",
    "comparison_tolerance_mm",
}
PDF_EVIDENCE_FIELDS = {
    "sha256",
    "size_bytes",
    "page_count",
    "page_width_mm",
    "page_height_mm",
}
TEMPLATE_ASSET_FIELDS = {
    "id",
    "layout",
    "page_setup",
    "size_bytes",
    "approved_sha256",
    "source_sha256_after",
    "staged_sha256_after",
}
EXPECTED = {
    "2016": {"adapter": "autocad-2016-net45", "acadver": "R20.1"},
    "2025": {"adapter": "autocad-2025-net8", "acadver": "R25.0"},
}
BUNDLE_BUILD_FIELDS = {
    "schema_version",
    "exact_commit",
    "package_version",
    "created_utc",
    "api_identity",
    "bundle",
    "source_tree_audit_passed",
    "bundle_verification_passed",
    "archive_audit_passed",
    "matching_sdk_bundle_built",
    "company_assets_copied",
    "autodesk_binaries_included",
    "autocad_launched",
    "live_publish_proven",
}
BUNDLE_FILES = {
    "LICENSE",
    "PackageContents.xml",
    "Contents/Windows/2016/CadPlotMcp.AutoCAD2016.dll",
    "Contents/Windows/2016/CadPlotMcp.Core.dll",
    "Contents/Windows/2025/CadPlotMcp.AutoCAD2025.dll",
    "Contents/Windows/2025/CadPlotMcp.Core.dll",
}
ADAPTER_PATHS = {
    "2016": "Contents/Windows/2016/CadPlotMcp.AutoCAD2016.dll",
    "2025": "Contents/Windows/2025/CadPlotMcp.AutoCAD2025.dll",
}


def build_pilot_run_evidence(
    manifest_value: str | Path,
    config: CadPlotConfig,
    *,
    autocad_release: str,
    plugin_status: dict[str, Any],
    approved_by: str,
    licensed: bool,
    authorized_test_asset: bool,
    restart_receipt_verified: bool,
    visual_checks: dict[str, bool],
    reference_pdf: str | Path,
    completed_utc: str | None = None,
) -> dict[str, Any]:
    """Build one read-only live-pilot record from cross-checked job evidence."""
    if autocad_release not in EXPECTED:
        raise ValueError("autocad_release must be 2016 or 2025.")
    for field in (
        "ok",
        "readOnly",
        "workspaceConfigured",
        "publishEnabled",
        "runtimeSupported",
    ):
        if plugin_status.get(field) is not True:
            raise ValueError(f"Live AutoCAD status requires {field}=true.")
    if plugin_status.get("queueAuthentication") != QUEUE_AUTHENTICATION_SCHEME:
        raise ValueError("Live AutoCAD status requires authenticated durable queue intent.")
    report = audit_publish_outputs(manifest_value, config)
    if report.get("publish_verified") is not True:
        raise ValueError("Pilot run requires publish_verified=true output evidence.")
    if len(report.get("outputs", [])) != 1:
        raise ValueError("Licensed pilot evidence must come from exactly one output sheet.")

    manifest, _ = load_staged_manifest(manifest_value, config)
    source = config.path_policy.require_allowed(manifest["source_drawing"], suffix=".dwg")
    staged = Path(manifest["staged_drawing"]).resolve(strict=True)
    source_before = manifest["source_fingerprint"]["sha256"]
    receipt = report["execution_receipt"]["receipt"]
    completed = completed_utc or datetime.now(UTC).isoformat()
    output = report["outputs"][0]
    run = {
        "autocad_release": autocad_release,
        "product": plugin_status.get("product"),
        "adapter": plugin_status.get("adapter"),
        "build_commit": plugin_status.get("buildCommit"),
        "plugin_sha256": plugin_status.get("pluginSha256"),
        "runtime_series": plugin_status.get("runtimeSeries"),
        "queue_authentication": plugin_status.get("queueAuthentication"),
        "licensed": licensed,
        "authorized_test_asset": authorized_test_asset,
        "plan_id": manifest["plan_id"],
        "manifest_sha256": _sha256(Path(manifest_value).expanduser().resolve(strict=True)),
        "receipt_manifest_sha256": receipt["manifest_sha256"],
        "receipt_state": receipt["state"],
        "source_sha256_before": source_before,
        "source_sha256_after": _sha256(source),
        "staged_sha256_before": source_before,
        "staged_sha256_after": _sha256(staged),
        "template_assets": _collect_template_asset_evidence(manifest, config),
        "published_pdf": _collect_published_pdf(output),
        "visual_reference": _collect_visual_reference(
            reference_pdf,
            config,
            output,
        ),
        "publish_verified": report["publish_verified"],
        "restart_receipt_verified": restart_receipt_verified,
        "visual_checks": visual_checks,
        "approved_by": approved_by,
        "completed_utc": completed,
    }
    return validate_pilot_run_evidence(run)


def validate_pilot_run_evidence(raw: Any) -> dict[str, Any]:
    """Validate one licensed-workstation run before it is assembled with the other release."""
    return _validate_run(raw)


def assemble_pilot_evidence(
    run_2016: Any,
    run_2025: Any,
    *,
    repository_commit: str,
    bundle_sha256: str,
    bundle_build_manifest_sha256: str,
    package_version: str,
    adapter_sha256: dict[str, str],
) -> dict[str, Any]:
    """Assemble and validate the immutable two-release pilot evidence document."""
    validated_2016 = validate_pilot_run_evidence(run_2016)
    validated_2025 = validate_pilot_run_evidence(run_2025)
    if validated_2016["autocad_release"] != "2016":
        raise ValueError("run_2016 must contain AutoCAD 2016 evidence.")
    if validated_2025["autocad_release"] != "2025":
        raise ValueError("run_2025 must contain AutoCAD 2025 evidence.")
    raw = {
        "schema_version": 5,
        "repository_commit": repository_commit,
        "package_version": package_version,
        "bundle_sha256": bundle_sha256,
        "bundle_build_manifest_sha256": bundle_build_manifest_sha256,
        "adapter_sha256": adapter_sha256,
        "runs": [validated_2016, validated_2025],
    }
    validate_pilot_evidence(raw)
    return raw


def validate_pilot_evidence(raw: Any) -> dict[str, Any]:
    """Require separate, complete licensed-workstation evidence for 2016 and 2025."""
    if not isinstance(raw, dict) or set(raw) != {
        "schema_version",
        "repository_commit",
        "package_version",
        "bundle_sha256",
        "bundle_build_manifest_sha256",
        "adapter_sha256",
        "runs",
    }:
        raise ValueError("Pilot evidence must contain exactly the documented top-level fields.")
    if raw["schema_version"] != 5:
        raise ValueError("Unsupported pilot evidence schema.")
    if not isinstance(raw["repository_commit"], str) or not COMMIT.fullmatch(
        raw["repository_commit"]
    ):
        raise ValueError("repository_commit must be a full lowercase Git commit SHA.")
    _require_digest(raw["bundle_sha256"], "bundle_sha256")
    _require_digest(
        raw["bundle_build_manifest_sha256"], "bundle_build_manifest_sha256"
    )
    if not isinstance(raw["package_version"], str) or not re.fullmatch(
        r"[0-9]+(?:\.[0-9]+){2}(?:[A-Za-z0-9.+-]*)?", raw["package_version"]
    ):
        raise ValueError("package_version is invalid.")
    adapter_sha256 = raw["adapter_sha256"]
    if not isinstance(adapter_sha256, dict) or set(adapter_sha256) != {"2016", "2025"}:
        raise ValueError("adapter_sha256 must contain exactly 2016 and 2025.")
    for release, digest in adapter_sha256.items():
        _require_digest(digest, f"adapter_sha256.{release}")
    runs = raw["runs"]
    if not isinstance(runs, list) or len(runs) != 2:
        raise ValueError("Pilot evidence must contain exactly one 2016 and one 2025 run.")
    validated = [_validate_run(run) for run in runs]
    releases = [run["autocad_release"] for run in validated]
    if sorted(releases) != ["2016", "2025"]:
        raise ValueError("Pilot evidence must contain distinct AutoCAD 2016 and 2025 runs.")
    for run in validated:
        release = run["autocad_release"]
        if run["build_commit"] != raw["repository_commit"]:
            raise ValueError(f"AutoCAD {release} running plug-in commit mismatch.")
        if run["plugin_sha256"] != adapter_sha256[release]:
            raise ValueError(f"AutoCAD {release} running plug-in binary mismatch.")
    return {
        "valid": True,
        "schema_version": 5,
        "repository_commit": raw["repository_commit"],
        "package_version": raw["package_version"],
        "bundle_sha256": raw["bundle_sha256"],
        "bundle_build_manifest_sha256": raw["bundle_build_manifest_sha256"],
        "accepted_releases": sorted(releases),
    }


def load_and_validate_pilot_evidence(path: str | Path) -> dict[str, Any]:
    evidence = Path(path).expanduser().resolve(strict=True)
    if evidence.stat().st_size > 256 * 1024:
        raise ValueError("Pilot evidence exceeds the 256 KiB limit.")
    try:
        raw = json.loads(evidence.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Pilot evidence must be valid UTF-8 JSON.") from exc
    return validate_pilot_evidence(raw)


def _validate_run(run: Any) -> dict[str, Any]:
    if not isinstance(run, dict) or set(run) != RUN_FIELDS:
        raise ValueError("Each pilot run must contain exactly the documented fields.")
    release = run["autocad_release"]
    if release not in EXPECTED:
        raise ValueError("autocad_release must be 2016 or 2025.")
    expected = EXPECTED[release]
    if run["adapter"] != expected["adapter"]:
        raise ValueError(f"AutoCAD {release} adapter identity mismatch.")
    if run["runtime_series"] != expected["acadver"]:
        raise ValueError(f"AutoCAD {release} normalized runtime series mismatch.")
    if run["queue_authentication"] != QUEUE_AUTHENTICATION_SCHEME:
        raise ValueError(f"AutoCAD {release} queue authentication mismatch.")
    product = run["product"]
    if (
        not isinstance(product, str)
        or f"AutoCAD {release}" not in product
        or f"ACADVER {expected['acadver']}" not in product
    ):
        raise ValueError(f"AutoCAD {release} product/ACADVER evidence mismatch.")
    if not isinstance(run["build_commit"], str) or not COMMIT.fullmatch(
        run["build_commit"]
    ):
        raise ValueError(f"AutoCAD {release} build_commit is invalid.")
    _require_digest(run["plugin_sha256"], "plugin_sha256")
    for flag in (
        "licensed",
        "authorized_test_asset",
        "publish_verified",
        "restart_receipt_verified",
    ):
        if run[flag] is not True:
            raise ValueError(f"AutoCAD {release} requires {flag}=true.")
    if not isinstance(run["plan_id"], str) or not PLAN_ID.fullmatch(run["plan_id"]):
        raise ValueError(f"AutoCAD {release} plan_id is invalid.")
    for field in (
        "manifest_sha256",
        "receipt_manifest_sha256",
        "source_sha256_before",
        "source_sha256_after",
        "staged_sha256_before",
        "staged_sha256_after",
    ):
        _require_digest(run[field], field)
    if run["manifest_sha256"] != run["receipt_manifest_sha256"]:
        raise ValueError(f"AutoCAD {release} receipt is not bound to the approved manifest.")
    if run["source_sha256_before"] != run["source_sha256_after"]:
        raise ValueError(f"AutoCAD {release} source DWG changed during the pilot.")
    if run["staged_sha256_before"] != run["staged_sha256_after"]:
        raise ValueError(f"AutoCAD {release} staged DWG changed during the pilot.")
    _validate_template_assets(run["template_assets"], release)
    if run["receipt_state"] != "succeeded":
        raise ValueError(f"AutoCAD {release} receipt_state must be succeeded.")
    _validate_pdf_evidence(run["published_pdf"], release, "published_pdf")
    _validate_visual_reference(run["visual_reference"], release)
    tolerance = float(run["visual_reference"]["comparison_tolerance_mm"])
    for field in ("page_width_mm", "page_height_mm"):
        if abs(
            float(run["published_pdf"][field])
            - float(run["visual_reference"][field])
        ) > tolerance:
            raise ValueError(
                f"AutoCAD {release} visual reference geometry does not match published PDF."
            )
    checks = run["visual_checks"]
    if not isinstance(checks, dict) or set(checks) != VISUAL_CHECKS:
        raise ValueError(f"AutoCAD {release} visual_checks fields are incomplete.")
    if any(value is not True for value in checks.values()):
        raise ValueError(f"AutoCAD {release} visual acceptance is incomplete.")
    approved_by = run["approved_by"]
    if not isinstance(approved_by, str) or not approved_by.strip() or len(approved_by) > 200:
        raise ValueError(f"AutoCAD {release} approved_by is invalid.")
    completed = run["completed_utc"]
    try:
        parsed = datetime.fromisoformat(completed)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"AutoCAD {release} completed_utc is invalid.") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"AutoCAD {release} completed_utc must include a timezone.")
    return run


def _collect_visual_reference(
    reference_pdf_value: str | Path,
    config: CadPlotConfig,
    output: dict[str, Any],
) -> dict[str, Any]:
    supplied = Path(reference_pdf_value).expanduser().absolute()
    if _is_reparse(supplied):
        raise ValueError("Visual reference PDF must not be a symlink or reparse point.")
    reference = config.path_policy.require_allowed(supplied, suffix=".pdf")
    if not reference.is_file():
        raise ValueError("Visual reference PDF must be a regular file.")
    size = reference.stat().st_size
    if size <= 0 or size > MAX_REFERENCE_PDF_BYTES:
        raise ValueError("Visual reference PDF must be between 1 byte and 128 MiB.")
    with reference.open("rb") as stream:
        if stream.read(5) != b"%PDF-":
            raise ValueError("Visual reference does not have a PDF header.")
    try:
        with reference.open("rb") as stream:
            reader = PdfReader(stream, strict=False)
            if reader.is_encrypted:
                raise ValueError("Visual reference PDF must not be encrypted.")
            if len(reader.pages) != 1:
                raise ValueError("Visual reference PDF must contain exactly one page.")
            media_box = reader.pages[0].mediabox
            width_mm = float(media_box.width) * 25.4 / 72
            height_mm = float(media_box.height) * 25.4 / 72
    except (OSError, PdfReadError, TypeError, ValueError) as exc:
        if isinstance(exc, ValueError) and str(exc).startswith("Visual reference"):
            raise
        raise ValueError("Visual reference PDF structure is invalid.") from exc
    if not all(math.isfinite(value) and value > 0 for value in (width_mm, height_mm)):
        raise ValueError("Visual reference PDF page size is invalid.")
    output_width = output.get("page_width_mm")
    output_height = output.get("page_height_mm")
    if not all(
        isinstance(value, (int, float)) and math.isfinite(value) and value > 0
        for value in (output_width, output_height)
    ):
        raise ValueError("Published PDF is missing validated page geometry.")
    if (
        abs(width_mm - float(output_width)) > config.pdf_page_tolerance_mm
        or abs(height_mm - float(output_height)) > config.pdf_page_tolerance_mm
    ):
        raise ValueError(
            "Visual reference PDF orientation or page size does not match the published PDF."
        )
    return {
        "sha256": _sha256(reference),
        "size_bytes": size,
        "page_count": 1,
        "page_width_mm": round(width_mm, 3),
        "page_height_mm": round(height_mm, 3),
        "comparison_tolerance_mm": config.pdf_page_tolerance_mm,
    }


def _collect_published_pdf(output: dict[str, Any]) -> dict[str, Any]:
    return {
        "sha256": output["sha256"],
        "size_bytes": output["size_bytes"],
        "page_count": output["page_count"],
        "page_width_mm": output["page_width_mm"],
        "page_height_mm": output["page_height_mm"],
    }


def _validate_pdf_evidence(raw: Any, release: str, label: str) -> None:
    if not isinstance(raw, dict) or set(raw) != PDF_EVIDENCE_FIELDS:
        raise ValueError(f"AutoCAD {release} {label} fields are incomplete.")
    _require_digest(raw["sha256"], f"{label}.sha256")
    if (
        not isinstance(raw["size_bytes"], int)
        or isinstance(raw["size_bytes"], bool)
        or raw["size_bytes"] < 1
    ):
        raise ValueError(f"AutoCAD {release} {label} size is invalid.")
    if raw["page_count"] != 1:
        raise ValueError(f"AutoCAD {release} {label} must contain one page.")
    for field in ("page_width_mm", "page_height_mm"):
        value = raw[field]
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(value)
            or value <= 0
        ):
            raise ValueError(f"AutoCAD {release} {label} page size is invalid.")


def _validate_visual_reference(raw: Any, release: str) -> None:
    if not isinstance(raw, dict) or set(raw) != VISUAL_REFERENCE_FIELDS:
        raise ValueError(f"AutoCAD {release} visual_reference fields are incomplete.")
    _require_digest(raw["sha256"], "visual_reference.sha256")
    if (
        not isinstance(raw["size_bytes"], int)
        or isinstance(raw["size_bytes"], bool)
        or not 1 <= raw["size_bytes"] <= MAX_REFERENCE_PDF_BYTES
    ):
        raise ValueError(f"AutoCAD {release} visual_reference size is invalid.")
    if raw["page_count"] != 1:
        raise ValueError(f"AutoCAD {release} visual_reference must contain one page.")
    for field in ("page_width_mm", "page_height_mm"):
        value = raw[field]
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(value)
            or value <= 0
        ):
            raise ValueError(f"AutoCAD {release} visual_reference page size is invalid.")
    tolerance = raw["comparison_tolerance_mm"]
    if (
        not isinstance(tolerance, (int, float))
        or isinstance(tolerance, bool)
        or not math.isfinite(tolerance)
        or not 0 <= tolerance <= 10
    ):
        raise ValueError(f"AutoCAD {release} visual_reference tolerance is invalid.")


def _is_reparse(path: Path) -> bool:
    try:
        stat = path.lstat()
    except OSError:
        return False
    return path.is_symlink() or bool(
        getattr(stat, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT
    )


def _collect_template_asset_evidence(
    manifest: dict[str, Any], config: CadPlotConfig
) -> list[dict[str, Any]]:
    assets = manifest.get("template_assets", [])
    if not assets:
        return []
    if config.template_path_policy is None:
        raise ValueError("Pilot template assets require configured template_roots.")
    profiles = {profile.id: profile for profile in config.paper_profiles}
    evidence: list[dict[str, Any]] = []
    for asset in sorted(assets, key=lambda item: item["id"]):
        profile = profiles.get(asset["id"])
        source_value = asset.get("source_template")
        if not isinstance(source_value, str) or not source_value.strip():
            raise ValueError(f"Pilot template asset {asset['id']!r} has no source identity.")
        source = config.template_path_policy.require_allowed(source_value)
        if (
            profile is None
            or profile.template_drawing != source
            or profile.template_sha256 != asset["sha256"]
            or profile.template_layout != asset["layout"]
            or profile.page_setup != asset["page_setup"]
        ):
            raise ValueError(
                f"Pilot template asset {asset['id']!r} does not match the active office profile."
            )
        staged = Path(asset["staged_template"]).resolve(strict=True)
        source_hash = _sha256(source)
        staged_hash = _sha256(staged)
        if source.stat().st_size != asset["size_bytes"] or source_hash != asset["sha256"]:
            raise ValueError(
                f"Pilot template asset {asset['id']!r} source changed after approval."
            )
        if staged.stat().st_size != asset["size_bytes"] or staged_hash != asset["sha256"]:
            raise ValueError(
                f"Pilot template asset {asset['id']!r} staged copy changed after approval."
            )
        evidence.append(
            {
                "id": asset["id"],
                "layout": asset["layout"],
                "page_setup": asset["page_setup"],
                "size_bytes": asset["size_bytes"],
                "approved_sha256": asset["sha256"],
                "source_sha256_after": source_hash,
                "staged_sha256_after": staged_hash,
            }
        )
    return evidence


def _validate_template_assets(value: Any, release: str) -> None:
    if not isinstance(value, list) or len(value) > 100:
        raise ValueError(f"AutoCAD {release} template_assets collection is invalid.")
    ids: set[str] = set()
    layouts: set[str] = set()
    for asset in value:
        if not isinstance(asset, dict) or set(asset) != TEMPLATE_ASSET_FIELDS:
            raise ValueError(f"AutoCAD {release} template asset fields are invalid.")
        asset_id = asset["id"]
        if (
            not isinstance(asset_id, str)
            or re.fullmatch(r"[A-Za-z0-9_-]{1,64}", asset_id) is None
            or asset_id in ids
        ):
            raise ValueError(f"AutoCAD {release} template asset id is invalid or duplicate.")
        ids.add(asset_id)
        for field in ("layout", "page_setup"):
            name = asset[field]
            if (
                not isinstance(name, str)
                or not 1 <= len(name) <= 255
                or name.isspace()
                or any(ord(character) < 32 or ord(character) == 127 for character in name)
            ):
                raise ValueError(f"AutoCAD {release} template asset metadata is invalid.")
        layout_key = asset["layout"].casefold()
        if layout_key in layouts:
            raise ValueError(f"AutoCAD {release} template asset layout is duplicate.")
        layouts.add(layout_key)
        size = asset["size_bytes"]
        if not isinstance(size, int) or isinstance(size, bool) or size < 1:
            raise ValueError(f"AutoCAD {release} template asset size is invalid.")
        for field in (
            "approved_sha256",
            "source_sha256_after",
            "staged_sha256_after",
        ):
            _require_digest(asset[field], f"template_assets.{field}")
        if (
            asset["approved_sha256"] != asset["source_sha256_after"]
            or asset["approved_sha256"] != asset["staged_sha256_after"]
        ):
            raise ValueError(f"AutoCAD {release} template asset changed during the pilot.")


def validate_bundle_build_evidence(
    bundle_archive_value: str | Path,
    build_manifest_value: str | Path,
) -> dict[str, Any]:
    """Cross-check a production bundle archive, build manifest, API IDs, and inner hashes."""
    bundle_archive = Path(bundle_archive_value).expanduser().resolve(strict=True)
    build_manifest = Path(build_manifest_value).expanduser().resolve(strict=True)
    if not bundle_archive.is_file() or bundle_archive.is_symlink():
        raise ValueError("Bundle archive must be a plain file.")
    if not build_manifest.is_file() or build_manifest.is_symlink():
        raise ValueError("Bundle build manifest must be a plain file.")
    if build_manifest.stat().st_size > 1024 * 1024:
        raise ValueError("Bundle build manifest exceeds the 1 MiB safety limit.")
    try:
        raw = json.loads(build_manifest.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Bundle build manifest must be valid UTF-8 JSON.") from exc
    if not isinstance(raw, dict) or set(raw) != BUNDLE_BUILD_FIELDS:
        raise ValueError("Bundle build manifest fields are not exact.")
    if raw["schema_version"] != 1:
        raise ValueError("Unsupported bundle build manifest schema.")
    if not isinstance(raw["exact_commit"], str) or not COMMIT.fullmatch(raw["exact_commit"]):
        raise ValueError("Bundle build exact_commit is invalid.")
    if not isinstance(raw["package_version"], str) or not re.fullmatch(
        r"[0-9]+(?:\.[0-9]+){2}(?:[A-Za-z0-9.+-]*)?", raw["package_version"]
    ):
        raise ValueError("Bundle build package_version is invalid.")
    for field in (
        "source_tree_audit_passed",
        "bundle_verification_passed",
        "archive_audit_passed",
        "matching_sdk_bundle_built",
    ):
        if raw[field] is not True:
            raise ValueError(f"Bundle build requires {field}=true.")
    for field in (
        "company_assets_copied",
        "autodesk_binaries_included",
        "autocad_launched",
        "live_publish_proven",
    ):
        if raw[field] is not False:
            raise ValueError(f"Bundle build requires {field}=false.")
    _validate_api_identity(raw["api_identity"])

    bundle = raw["bundle"]
    if not isinstance(bundle, dict) or set(bundle) != {
        "directory",
        "archive",
        "archive_sha256",
        "files",
    }:
        raise ValueError("Bundle build archive fields are not exact.")
    if bundle["directory"] != "CadPlotMcp.bundle" or bundle["archive"] != bundle_archive.name:
        raise ValueError("Bundle build archive names are invalid.")
    archive_sha256 = _sha256(bundle_archive)
    if bundle["archive_sha256"] != archive_sha256:
        raise ValueError("Bundle archive hash does not match its build manifest.")
    files = bundle["files"]
    if not isinstance(files, list) or len(files) != len(BUNDLE_FILES):
        raise ValueError("Bundle build file evidence count is invalid.")
    hashes: dict[str, str] = {}
    for item in files:
        if not isinstance(item, dict) or set(item) != {"path", "sha256"}:
            raise ValueError("Bundle build file evidence fields are invalid.")
        path = item["path"]
        if path in hashes:
            raise ValueError("Bundle build file evidence contains duplicate paths.")
        _require_digest(item["sha256"], f"bundle file {path}")
        hashes[path] = item["sha256"]
    if set(hashes) != BUNDLE_FILES:
        raise ValueError("Bundle build file evidence set is invalid.")

    expected_entries = {f"CadPlotMcp.bundle/{path}": digest for path, digest in hashes.items()}
    try:
        with zipfile.ZipFile(bundle_archive) as archive:
            entries = [entry for entry in archive.infolist() if not entry.is_dir()]
            names = [entry.filename.replace("\\", "/") for entry in entries]
            if len(names) != len(expected_entries) or set(names) != set(expected_entries):
                raise ValueError("Bundle archive entry set is invalid.")
            if len(names) != len(set(names)):
                raise ValueError("Bundle archive contains duplicate entries.")
            for entry, name in zip(entries, names, strict=True):
                if entry.file_size > 100 * 1024 * 1024:
                    raise ValueError("Bundle archive entry exceeds the 100 MiB safety limit.")
                digest = hashlib.sha256()
                with archive.open(entry) as stream:
                    while chunk := stream.read(1024 * 1024):
                        digest.update(chunk)
                if digest.hexdigest() != expected_entries[name]:
                    raise ValueError(f"Bundle archive entry hash mismatch: {name}")
    except zipfile.BadZipFile as exc:
        raise ValueError("Bundle archive is not a valid ZIP file.") from exc

    return {
        "repository_commit": raw["exact_commit"],
        "package_version": raw["package_version"],
        "bundle_sha256": archive_sha256,
        "bundle_build_manifest_sha256": _sha256(build_manifest),
        "adapter_sha256": {
            release: hashes[path] for release, path in ADAPTER_PATHS.items()
        },
    }


def _validate_api_identity(value: Any) -> None:
    if not isinstance(value, dict) or set(value) != {"autocad_2016", "autocad_2025"}:
        raise ValueError("Bundle build API identity fields are invalid.")
    expected = {
        "autocad_2016": ("R20.1", "20.1."),
        "autocad_2025": ("R25.0", "25.0."),
    }
    expected_names = {"AcMgd.dll", "AcDbMgd.dll", "AcCoreMgd.dll"}
    for key, (series, version_prefix) in expected.items():
        identity = value[key]
        if not isinstance(identity, dict) or set(identity) != {
            "detected_series",
            "assemblies",
        }:
            raise ValueError(f"{key} API identity fields are invalid.")
        if identity["detected_series"] != series:
            raise ValueError(f"{key} API identity series mismatch.")
        assemblies = identity["assemblies"]
        if not isinstance(assemblies, list) or len(assemblies) != 3:
            raise ValueError(f"{key} API assembly evidence count is invalid.")
        names = set()
        for assembly in assemblies:
            if not isinstance(assembly, dict) or set(assembly) != {
                "name",
                "assembly_version",
                "sha256",
            }:
                raise ValueError(f"{key} API assembly evidence fields are invalid.")
            names.add(assembly["name"])
            if not str(assembly["assembly_version"]).startswith(version_prefix):
                raise ValueError(f"{key} API assembly version mismatch.")
            _require_digest(assembly["sha256"], f"{key} API assembly")
        if names != expected_names:
            raise ValueError(f"{key} API assembly names are invalid.")


def _require_digest(value: Any, field: str) -> None:
    if not isinstance(value, str) or not SHA256.fullmatch(value):
        raise ValueError(f"{field} must be a lowercase SHA-256 digest.")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()
