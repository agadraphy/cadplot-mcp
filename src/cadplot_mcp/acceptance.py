from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import zipfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from cadplot_mcp.pilot import validate_bundle_build_evidence, validate_pilot_evidence

SHA256 = re.compile(r"[0-9a-f]{64}")
COMMIT = re.compile(r"[0-9a-f]{40}")
MAX_JSON_BYTES = 1024 * 1024
MAX_ARCHIVE_ENTRY_BYTES = 100 * 1024 * 1024
MAX_ARCHIVE_TOTAL_BYTES = 1024 * 1024 * 1024
PROPRIETARY_SUFFIXES = {
    ".dwg",
    ".dwt",
    ".pc3",
    ".pmp",
    ".ctb",
    ".stb",
    ".pdf",
    ".bak",
    ".sv$",
    ".7z",
    ".rar",
}
AUTODESK_MANAGED_NAMES = {"acmgd.dll", "acdbmgd.dll", "accoremgd.dll"}
REQUIRED_KIT_PATHS = {
    "LICENSE",
    "README.md",
    "README.tr.md",
    "THIRD_PARTY_NOTICES.md",
    "autocad/CadPlotMcp.bundle.zip",
    "autocad/bundle-build.json",
    "python/pyproject.toml",
    "python/uv.lock",
    "scripts/verify-release-kit.ps1",
    "scripts/release-acceptance.py",
    "docs/pilot-evidence.md",
    "docs/release-acceptance.md",
}
REPORT_FIELDS = {
    "schema_version",
    "completed_utc",
    "exact_commit",
    "package_version",
    "release_kit_manifest_sha256",
    "release_kit_archive_sha256",
    "wheel_sha256",
    "bundle_sha256",
    "bundle_build_manifest_sha256",
    "pilot_evidence_sha256",
    "accepted_releases",
    "licensed_live_pilot_ready",
    "live_publish_proven",
    "company_publication_approved",
    "maintainer_release_approved",
    "public_release_ready",
    "company_assets_included",
    "autodesk_binaries_included",
}
OUTER_MANIFEST_FIELDS = {
    "schema_version",
    "exact_commit",
    "package_version",
    "created_utc",
    "kit_directory",
    "kit_archive",
    "kit_manifest_sha256",
    "kit_archive_sha256",
    "dependency_audit_ran",
    "dependency_audit",
    "matching_sdk_bundle_built",
    "local_demo_ready",
    "licensed_live_pilot_ready",
    "public_release_ready",
    "company_assets_copied",
    "autodesk_binaries_included",
    "autocad_launched",
    "live_publish_proven",
}
INNER_MANIFEST_FIELDS = {
    "schema_version",
    "exact_commit",
    "package_version",
    "created_utc",
    "wheel",
    "source_archive",
    "files",
    "dependency_audit_ran",
    "dependency_audit",
    "synthetic_batch_rehearsal",
    "matching_sdk_bundle_built",
    "local_demo_ready",
    "licensed_live_pilot_ready",
    "public_release_ready",
    "company_assets_copied",
    "autodesk_binaries_included",
    "autocad_launched",
    "live_publish_proven",
}


def build_release_acceptance(
    release_root_value: str | Path,
    pilot_evidence_value: str | Path,
    *,
    company_publication_approved: bool,
    maintainer_release_approved: bool,
    completed_utc: str | None = None,
) -> dict[str, Any]:
    """Bind a verified release kit to distinct licensed 2016/2025 pilot evidence."""
    evidence = _collect_acceptance_inputs(release_root_value, pilot_evidence_value)
    report = {
        "schema_version": 1,
        "completed_utc": completed_utc or datetime.now(UTC).isoformat(),
        **evidence,
        "licensed_live_pilot_ready": True,
        "live_publish_proven": True,
        "company_publication_approved": company_publication_approved,
        "maintainer_release_approved": maintainer_release_approved,
        "public_release_ready": (
            company_publication_approved and maintainer_release_approved
        ),
        "company_assets_included": False,
        "autodesk_binaries_included": False,
    }
    return validate_release_acceptance(
        report,
        release_root_value=release_root_value,
        pilot_evidence_value=pilot_evidence_value,
    )


def validate_release_acceptance(
    raw: Any,
    *,
    release_root_value: str | Path,
    pilot_evidence_value: str | Path,
) -> dict[str, Any]:
    """Recompute artifact identities and validate a sanitized acceptance report."""
    if not isinstance(raw, dict) or set(raw) != REPORT_FIELDS:
        raise ValueError("Release acceptance must contain exactly the documented fields.")
    if raw["schema_version"] != 1:
        raise ValueError("Unsupported release-acceptance schema.")
    _require_timestamp(raw["completed_utc"])
    if not isinstance(raw["exact_commit"], str) or not COMMIT.fullmatch(
        raw["exact_commit"]
    ):
        raise ValueError("exact_commit must be a full lowercase Git commit SHA.")
    if not isinstance(raw["package_version"], str) or not re.fullmatch(
        r"[0-9]+(?:\.[0-9]+){2}(?:[A-Za-z0-9.+-]*)?", raw["package_version"]
    ):
        raise ValueError("package_version is invalid.")
    for field in (
        "release_kit_manifest_sha256",
        "release_kit_archive_sha256",
        "wheel_sha256",
        "bundle_sha256",
        "bundle_build_manifest_sha256",
        "pilot_evidence_sha256",
    ):
        _require_digest(raw[field], field)
    if raw["accepted_releases"] != ["2016", "2025"]:
        raise ValueError("accepted_releases must be exactly 2016 and 2025.")
    for field in ("licensed_live_pilot_ready", "live_publish_proven"):
        if raw[field] is not True:
            raise ValueError(f"Release acceptance requires {field}=true.")
    for field in ("company_assets_included", "autodesk_binaries_included"):
        if raw[field] is not False:
            raise ValueError(f"Release acceptance requires {field}=false.")
    for field in ("company_publication_approved", "maintainer_release_approved"):
        if not isinstance(raw[field], bool):
            raise ValueError(f"{field} must be boolean.")
    expected_public = (
        raw["company_publication_approved"] and raw["maintainer_release_approved"]
    )
    if raw["public_release_ready"] is not expected_public:
        raise ValueError("public_release_ready does not match the two explicit approvals.")

    expected = _collect_acceptance_inputs(release_root_value, pilot_evidence_value)
    for field, value in expected.items():
        if raw[field] != value:
            raise ValueError(f"Release acceptance artifact mismatch: {field}.")
    return raw


def load_and_validate_release_acceptance(
    acceptance_value: str | Path,
    *,
    release_root_value: str | Path,
    pilot_evidence_value: str | Path,
) -> dict[str, Any]:
    acceptance = _plain_file(acceptance_value, label="Release acceptance")
    raw = _load_json(acceptance, label="Release acceptance")
    return validate_release_acceptance(
        raw,
        release_root_value=release_root_value,
        pilot_evidence_value=pilot_evidence_value,
    )


def require_new_acceptance_output(value: str | Path) -> Path:
    candidate = _absolute_path(value)
    parent = _plain_directory(candidate.parent, label="Acceptance output parent")
    output = parent / candidate.name
    if output.exists():
        raise ValueError("Output already exists; release acceptance is never overwritten.")
    return output


def _collect_acceptance_inputs(
    release_root_value: str | Path,
    pilot_evidence_value: str | Path,
) -> dict[str, Any]:
    release_root = _plain_directory(release_root_value, label="Release root")
    _require_plain_tree(release_root)
    if {item.name for item in release_root.iterdir()} != {
        "CadPlotMcp.release",
        "CadPlotMcp.release.zip",
        "release-kit-build.json",
    }:
        raise ValueError("Release-kit top-level contents are not exact.")

    kit_root = release_root / "CadPlotMcp.release"
    archive_path = release_root / "CadPlotMcp.release.zip"
    outer_path = release_root / "release-kit-build.json"
    manifest_path = kit_root / "release-kit.json"
    outer = _load_json(
        _plain_file(outer_path, label="Outer release manifest"),
        label="Outer release manifest",
    )
    manifest = _load_json(
        _plain_file(manifest_path, label="Release-kit manifest"),
        label="Release-kit manifest",
    )
    _validate_release_manifest_identity(outer, manifest)

    manifest_hash = _sha256(manifest_path)
    archive_hash = _sha256(archive_path)
    if outer.get("kit_manifest_sha256") != manifest_hash:
        raise ValueError("Release-kit manifest hash does not match outer evidence.")
    if outer.get("kit_archive_sha256") != archive_hash:
        raise ValueError("Release-kit archive hash does not match outer evidence.")
    _validate_kit_files(kit_root, manifest)
    _validate_kit_archive(archive_path, kit_root)

    wheel = manifest.get("wheel")
    if not isinstance(wheel, dict) or set(wheel) != {"file", "sha256"}:
        raise ValueError("Release-kit wheel evidence fields are invalid.")
    wheel_path = _safe_kit_file(kit_root, wheel["file"], label="Wheel")
    if not re.fullmatch(r"cadplot_mcp-[0-9A-Za-z.]+-py3-none-any\.whl", wheel_path.name):
        raise ValueError("Release-kit wheel name is invalid.")
    wheel_hash = _sha256(wheel_path)
    if wheel.get("sha256") != wheel_hash:
        raise ValueError("Release-kit wheel hash is invalid.")
    source_path = _safe_kit_file(
        kit_root, manifest.get("source_archive"), label="Source archive"
    )
    if not re.fullmatch(r"cadplot-mcp-source-[0-9a-f]{7}\.zip", source_path.name):
        raise ValueError("Release-kit source archive name is invalid.")
    _validate_public_asset_boundary(kit_root, source_path)

    bundle_path = kit_root / "autocad" / "CadPlotMcp.bundle.zip"
    bundle_manifest_path = kit_root / "autocad" / "bundle-build.json"
    bundle = validate_bundle_build_evidence(bundle_path, bundle_manifest_path)
    if (
        bundle["repository_commit"] != manifest["exact_commit"]
        or bundle["package_version"] != manifest["package_version"]
    ):
        raise ValueError("Embedded bundle identity does not match the release kit.")

    pilot_path = _plain_file(pilot_evidence_value, label="Pilot evidence")
    pilot_raw = _load_json(pilot_path, label="Pilot evidence", max_bytes=256 * 1024)
    pilot = validate_pilot_evidence(pilot_raw)
    comparisons = {
        "repository_commit": bundle["repository_commit"],
        "package_version": bundle["package_version"],
        "bundle_sha256": bundle["bundle_sha256"],
        "bundle_build_manifest_sha256": bundle["bundle_build_manifest_sha256"],
        "adapter_sha256": bundle["adapter_sha256"],
    }
    for field, value in comparisons.items():
        if pilot_raw.get(field) != value:
            raise ValueError(f"Pilot evidence does not match the release kit: {field}.")

    return {
        "exact_commit": manifest["exact_commit"],
        "package_version": manifest["package_version"],
        "release_kit_manifest_sha256": manifest_hash,
        "release_kit_archive_sha256": archive_hash,
        "wheel_sha256": wheel_hash,
        "bundle_sha256": bundle["bundle_sha256"],
        "bundle_build_manifest_sha256": bundle["bundle_build_manifest_sha256"],
        "pilot_evidence_sha256": _sha256(pilot_path),
        "accepted_releases": pilot["accepted_releases"],
    }


def _validate_release_manifest_identity(outer: Any, manifest: Any) -> None:
    if not isinstance(outer, dict) or set(outer) != OUTER_MANIFEST_FIELDS:
        raise ValueError("Outer release-kit manifest fields are not exact.")
    if not isinstance(manifest, dict) or set(manifest) != INNER_MANIFEST_FIELDS:
        raise ValueError("Inner release-kit manifest fields are not exact.")
    if outer.get("schema_version") != 1 or manifest.get("schema_version") != 1:
        raise ValueError("Unsupported release-kit manifest schema.")
    if (
        outer.get("exact_commit") != manifest.get("exact_commit")
        or not isinstance(manifest.get("exact_commit"), str)
        or not COMMIT.fullmatch(manifest["exact_commit"])
        or outer.get("package_version") != manifest.get("package_version")
        or outer.get("kit_directory") != "CadPlotMcp.release"
        or outer.get("kit_archive") != "CadPlotMcp.release.zip"
    ):
        raise ValueError("Release-kit identity fields are invalid or inconsistent.")
    for evidence in (outer, manifest):
        for field in ("matching_sdk_bundle_built", "local_demo_ready"):
            if evidence.get(field) is not True:
                raise ValueError(f"Release kit requires {field}=true.")
        for field in (
            "licensed_live_pilot_ready",
            "public_release_ready",
            "company_assets_copied",
            "autodesk_binaries_included",
            "autocad_launched",
            "live_publish_proven",
        ):
            if evidence.get(field) is not False:
                raise ValueError(f"Pre-acceptance release kit requires {field}=false.")
        if evidence.get("dependency_audit_ran") is not True:
            raise ValueError("Release kit requires dependency_audit_ran=true.")
        audit = evidence.get("dependency_audit")
        if not isinstance(audit, dict) or audit.get("passed") is not True:
            raise ValueError("Release-kit dependency audit is invalid.")
    batch = manifest.get("synthetic_batch_rehearsal")
    if not isinstance(batch, dict) or any(
        batch.get(field) != expected
        for field, expected in {
            "target_drawings": 300,
            "ready": 300,
            "staged": 300,
            "outputs_complete": 300,
            "execution_verified": 0,
            "publish_verified": 0,
            "manual_review_without_receipts": 300,
            "source_unchanged": True,
            "synthetic": True,
        }.items()
    ):
        raise ValueError("Release kit has no valid 300-drawing synthetic evidence.")


def _validate_kit_files(kit_root: Path, manifest: dict[str, Any]) -> None:
    recorded = manifest.get("files")
    if not isinstance(recorded, list):
        raise ValueError("Release-kit file evidence must be a list.")
    expected: dict[str, str] = {}
    for item in recorded:
        if not isinstance(item, dict) or set(item) != {"path", "sha256"}:
            raise ValueError("Release-kit file evidence fields are invalid.")
        path = _safe_relative_path(item["path"])
        if path in expected:
            raise ValueError("Release-kit file evidence contains duplicate paths.")
        _require_digest(item["sha256"], f"release-kit file {path}")
        expected[path] = item["sha256"]
    actual = {
        path.relative_to(kit_root).as_posix(): path
        for path in kit_root.rglob("*")
        if path.is_file() and path.name != "release-kit.json"
    }
    if set(expected) != set(actual):
        raise ValueError("Release-kit file evidence set is not exact.")
    if not REQUIRED_KIT_PATHS.issubset(actual):
        raise ValueError("Release-kit required public file set is incomplete.")
    for relative, path in actual.items():
        if _sha256(path) != expected[relative]:
            raise ValueError(f"Release-kit file hash mismatch: {relative}.")


def _validate_public_asset_boundary(kit_root: Path, source_archive: Path) -> None:
    for path in kit_root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.casefold() in PROPRIETARY_SUFFIXES:
            raise ValueError(f"Release kit contains a prohibited asset type: {path.name}.")
        if path.name.casefold() in AUTODESK_MANAGED_NAMES:
            raise ValueError(f"Release kit contains an Autodesk managed binary: {path.name}.")
    try:
        with zipfile.ZipFile(source_archive) as archive:
            entries = [item for item in archive.infolist() if not item.is_dir()]
            total = 0
            for entry in entries:
                name = PurePosixPath(entry.filename.replace("\\", "/")).name
                suffix = PurePosixPath(name).suffix.casefold()
                if suffix in PROPRIETARY_SUFFIXES:
                    raise ValueError(f"Source archive contains a prohibited asset type: {name}.")
                if name.casefold() in AUTODESK_MANAGED_NAMES:
                    raise ValueError(f"Source archive contains an Autodesk managed binary: {name}.")
                total += entry.file_size
                if entry.file_size > MAX_ARCHIVE_ENTRY_BYTES or total > MAX_ARCHIVE_TOTAL_BYTES:
                    raise ValueError("Source archive exceeds the inspection safety limit.")
    except zipfile.BadZipFile as exc:
        raise ValueError("Release-kit source archive is not a valid ZIP file.") from exc


def _validate_kit_archive(archive_path: Path, kit_root: Path) -> None:
    expected = {
        f"CadPlotMcp.release/{path.relative_to(kit_root).as_posix()}": path
        for path in kit_root.rglob("*")
        if path.is_file()
    }
    try:
        with zipfile.ZipFile(archive_path) as archive:
            entries = [item for item in archive.infolist() if not item.is_dir()]
            names = [item.filename.replace("\\", "/") for item in entries]
            if len(names) != len(set(names)) or set(names) != set(expected):
                raise ValueError("Release-kit archive entry set is not exact.")
            total = 0
            for entry, name in zip(entries, names, strict=True):
                if entry.flag_bits & 0x1:
                    raise ValueError("Release-kit archive contains an encrypted entry.")
                total += entry.file_size
                if entry.file_size > MAX_ARCHIVE_ENTRY_BYTES or total > MAX_ARCHIVE_TOTAL_BYTES:
                    raise ValueError("Release-kit archive exceeds the extraction safety limit.")
                digest = hashlib.sha256()
                with archive.open(entry) as stream:
                    while chunk := stream.read(1024 * 1024):
                        digest.update(chunk)
                if digest.hexdigest() != _sha256(expected[name]):
                    raise ValueError(f"Release-kit archive entry hash mismatch: {name}.")
    except zipfile.BadZipFile as exc:
        raise ValueError("Release-kit archive is not a valid ZIP file.") from exc


def _safe_kit_file(kit_root: Path, value: Any, *, label: str) -> Path:
    relative = _safe_relative_path(value)
    path = (kit_root / Path(*PurePosixPath(relative).parts)).resolve(strict=True)
    if kit_root not in path.parents or not path.is_file() or _is_reparse(path):
        raise ValueError(f"{label} path escapes the release kit or is redirected.")
    return path


def _safe_relative_path(value: Any) -> str:
    if not isinstance(value, str) or "\\" in value:
        raise ValueError("Release-kit path must be a normalized relative POSIX path.")
    path = PurePosixPath(value)
    if not value or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("Release-kit path is unsafe.")
    return path.as_posix()


def _plain_file(value: str | Path, *, label: str) -> Path:
    candidate = _absolute_path(value)
    _require_plain_ancestors(candidate.parent, label=label)
    if not candidate.is_file() or _is_reparse(candidate):
        raise ValueError(f"{label} must be a plain file.")
    return candidate.resolve(strict=True)


def _plain_directory(value: str | Path, *, label: str) -> Path:
    candidate = _absolute_path(value)
    _require_plain_ancestors(candidate, label=label)
    if not candidate.is_dir() or _is_reparse(candidate):
        raise ValueError(f"{label} must be a plain directory.")
    return candidate.resolve(strict=True)


def _require_plain_tree(root: Path) -> None:
    for path in root.rglob("*"):
        if _is_reparse(path):
            raise ValueError(f"Release kit contains a redirected item: {path.name}.")


def _require_plain_ancestors(path: Path, *, label: str) -> None:
    current = path
    while True:
        if _is_reparse(current):
            raise ValueError(f"{label} path passes through a redirected ancestor.")
        if current.parent == current:
            return
        current = current.parent


def _is_reparse(path: Path) -> bool:
    info = path.lstat()
    attributes = getattr(info, "st_file_attributes", 0)
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return path.is_symlink() or bool(attributes & reparse)


def _absolute_path(value: str | Path) -> Path:
    return Path(os.path.abspath(Path(value).expanduser()))


def _load_json(path: Path, *, label: str, max_bytes: int = MAX_JSON_BYTES) -> Any:
    if path.stat().st_size > max_bytes:
        raise ValueError(f"{label} exceeds the size limit.")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} must be valid UTF-8 JSON.") from exc


def _require_timestamp(value: Any) -> None:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("completed_utc must be a timezone-qualified timestamp.") from exc
    if parsed.tzinfo is None:
        raise ValueError("completed_utc must be a timezone-qualified timestamp.")


def _require_digest(value: Any, field: str) -> None:
    if not isinstance(value, str) or not SHA256.fullmatch(value):
        raise ValueError(f"{field} must be a lowercase SHA-256 digest.")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()
