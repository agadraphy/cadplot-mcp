from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import uuid
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

CYCLONEDX_SCHEMA = "https://cyclonedx.org/schema/bom-1.7.schema.json"
CYCLONEDX_SPEC_VERSION = "1.7"
SBOM_FILENAME = "cadplot-mcp.cdx.json"
_SHA256 = re.compile(r"[0-9a-f]{64}")
_COMMIT = re.compile(r"[0-9a-f]{40}")
_VERSION = re.compile(r"[0-9]+(?:\.[0-9]+){2}(?:[A-Za-z0-9.+-]*)?")
_ARTIFACT_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_SERIAL_NAMESPACE = uuid.UUID("9cd0d035-f7e5-5f13-bbe8-8f3bc8c4bf0e")


def build_sbom(
    *,
    exact_commit: str,
    package_version: str,
    dependency_audit: Mapping[str, Any],
    artifacts: Mapping[str, str | Path],
) -> dict[str, Any]:
    """Build the deterministic, path-redacted CadPlot CycloneDX release profile."""
    _validate_identity(exact_commit, package_version)
    audit = _validate_dependency_audit(dependency_audit)
    if not artifacts:
        raise ValueError("At least one release artifact is required for the SBOM.")

    root_ref = f"pkg:pypi/cadplot-mcp@{package_version}"
    runtime_components = [_python_component(item) for item in audit["packages"]]
    artifact_components = [
        _artifact_component(name, require_plain_file(path))
        for name, path in sorted(artifacts.items(), key=lambda item: item[0])
    ]
    components = sorted(
        [*runtime_components, *artifact_components], key=lambda item: item["bom-ref"]
    )
    direct_refs = sorted(item["bom-ref"] for item in components)
    serial_seed = {
        "exact_commit": exact_commit,
        "package_version": package_version,
        "lock_sha256": audit["lock_sha256"],
        "components": components,
    }
    serial = uuid.uuid5(_SERIAL_NAMESPACE, _canonical_json(serial_seed))
    result: dict[str, Any] = {
        "$schema": CYCLONEDX_SCHEMA,
        "bomFormat": "CycloneDX",
        "specVersion": CYCLONEDX_SPEC_VERSION,
        "serialNumber": f"urn:uuid:{serial}",
        "version": 1,
        "metadata": {
            "timestamp": audit["generated_utc"],
            "tools": {
                "components": [
                    {
                        "type": "application",
                        "name": "cadplot-sbom",
                        "version": package_version,
                    }
                ]
            },
            "component": {
                "type": "application",
                "bom-ref": root_ref,
                "name": "cadplot-mcp",
                "version": package_version,
                "purl": root_ref,
                "licenses": [{"license": {"id": "MIT"}}],
                "properties": [
                    {"name": "cadplot:repository-commit", "value": exact_commit},
                    {"name": "cadplot:uv-lock-sha256", "value": audit["lock_sha256"]},
                    {
                        "name": "cadplot:dependency-requirements-sha256",
                        "value": audit["requirements_sha256"],
                    },
                    {"name": "cadplot:autodesk-binaries-included", "value": "false"},
                    {"name": "cadplot:company-assets-included", "value": "false"},
                    {"name": "cadplot:autocad-launched", "value": "false"},
                    {"name": "cadplot:live-publish-proven", "value": "false"},
                ],
            },
        },
        "components": components,
        "dependencies": [
            {"ref": root_ref, "dependsOn": direct_refs},
            *({"ref": ref, "dependsOn": []} for ref in direct_refs),
        ],
        "compositions": [{"aggregate": "incomplete", "assemblies": [root_ref]}],
    }
    validate_sbom(
        result,
        expected_commit=exact_commit,
        expected_version=package_version,
        expected_artifact_count=len(artifacts),
        expected_runtime_count=len(runtime_components),
    )
    return result


def validate_sbom(
    value: Mapping[str, Any],
    *,
    expected_commit: str | None = None,
    expected_version: str | None = None,
    expected_artifact_count: int | None = None,
    expected_runtime_count: int | None = None,
) -> dict[str, Any]:
    """Validate the strict CadPlot CycloneDX profile without network or local-path trust."""
    required = {
        "$schema",
        "bomFormat",
        "specVersion",
        "serialNumber",
        "version",
        "metadata",
        "components",
        "dependencies",
        "compositions",
    }
    if set(value) != required:
        raise ValueError("SBOM top-level fields are not exact.")
    if (
        value["$schema"] != CYCLONEDX_SCHEMA
        or value["bomFormat"] != "CycloneDX"
        or value["specVersion"] != CYCLONEDX_SPEC_VERSION
        or value["version"] != 1
    ):
        raise ValueError("SBOM CycloneDX identity is invalid.")
    serial = value["serialNumber"]
    if not isinstance(serial, str) or not re.fullmatch(
        r"urn:uuid:[0-9a-f]{8}-[0-9a-f]{4}-5[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
        serial,
    ):
        raise ValueError("SBOM serialNumber must be a deterministic UUIDv5 URN.")

    metadata = value["metadata"]
    if not isinstance(metadata, dict) or set(metadata) != {"timestamp", "tools", "component"}:
        raise ValueError("SBOM metadata fields are not exact.")
    _parse_timestamp(metadata["timestamp"])
    root = metadata["component"]
    if not isinstance(root, dict) or set(root) != {
        "type",
        "bom-ref",
        "name",
        "version",
        "purl",
        "licenses",
        "properties",
    }:
        raise ValueError("SBOM root component fields are not exact.")
    if root["type"] != "application" or root["name"] != "cadplot-mcp":
        raise ValueError("SBOM root component identity is invalid.")
    package_version = root["version"]
    if not isinstance(package_version, str) or not _VERSION.fullmatch(package_version):
        raise ValueError("SBOM package version is invalid.")
    root_ref = f"pkg:pypi/cadplot-mcp@{package_version}"
    if root["bom-ref"] != root_ref or root["purl"] != root_ref:
        raise ValueError("SBOM root package URL is invalid.")
    if expected_version is not None and package_version != expected_version:
        raise ValueError("SBOM package version does not match the release.")
    if root["licenses"] != [{"license": {"id": "MIT"}}]:
        raise ValueError("SBOM root license is invalid.")

    properties = _properties(root["properties"])
    exact_commit = properties.get("cadplot:repository-commit")
    if not isinstance(exact_commit, str) or not _COMMIT.fullmatch(exact_commit):
        raise ValueError("SBOM repository commit is invalid.")
    if expected_commit is not None and exact_commit != expected_commit:
        raise ValueError("SBOM repository commit does not match the release.")
    for name in ("cadplot:uv-lock-sha256", "cadplot:dependency-requirements-sha256"):
        if not _is_sha256(properties.get(name)):
            raise ValueError(f"SBOM property is not SHA-256: {name}")
    for name in (
        "cadplot:autodesk-binaries-included",
        "cadplot:company-assets-included",
        "cadplot:autocad-launched",
        "cadplot:live-publish-proven",
    ):
        if properties.get(name) != "false":
            raise ValueError(f"SBOM crossed a required evidence boundary: {name}")

    components = value["components"]
    if not isinstance(components, list) or not components:
        raise ValueError("SBOM components must be a non-empty list.")
    refs: list[str] = []
    runtime_count = 0
    artifact_count = 0
    for component in components:
        if not isinstance(component, dict):
            raise ValueError("SBOM component entry is invalid.")
        ref = component.get("bom-ref")
        if not isinstance(ref, str) or not ref:
            raise ValueError("SBOM component bom-ref is invalid.")
        refs.append(ref)
        if component.get("type") == "library":
            _validate_runtime_component(component)
            runtime_count += 1
        elif component.get("type") == "file":
            _validate_artifact_component(component)
            artifact_count += 1
        else:
            raise ValueError("SBOM contains an unsupported component type.")
    if refs != sorted(refs) or len(refs) != len(set(refs)):
        raise ValueError("SBOM component references must be sorted and unique.")
    if expected_artifact_count is not None and artifact_count != expected_artifact_count:
        raise ValueError("SBOM release artifact count is invalid.")
    if expected_runtime_count is not None and runtime_count != expected_runtime_count:
        raise ValueError("SBOM runtime dependency count is invalid.")

    serial_seed = {
        "exact_commit": exact_commit,
        "package_version": package_version,
        "lock_sha256": properties["cadplot:uv-lock-sha256"],
        "components": components,
    }
    expected_serial = f"urn:uuid:{uuid.uuid5(_SERIAL_NAMESPACE, _canonical_json(serial_seed))}"
    if serial != expected_serial:
        raise ValueError("SBOM serialNumber is not bound to its exact component inventory.")

    dependencies = value["dependencies"]
    expected_dependencies = [
        {"ref": root_ref, "dependsOn": refs},
        *({"ref": ref, "dependsOn": []} for ref in refs),
    ]
    if dependencies != expected_dependencies:
        raise ValueError("SBOM dependency graph is invalid.")
    if value["compositions"] != [{"aggregate": "incomplete", "assemblies": [root_ref]}]:
        raise ValueError("SBOM composition disclosure is invalid.")

    document = json.loads(_canonical_json(value))
    if _contains_sensitive_path(document):
        raise ValueError("SBOM contains a machine-local path.")
    return {
        "passed": True,
        "spec_version": CYCLONEDX_SPEC_VERSION,
        "exact_commit": exact_commit,
        "package_version": package_version,
        "component_count": len(components),
        "runtime_dependency_count": runtime_count,
        "artifact_count": artifact_count,
        "autocad_launched": False,
        "live_publish_proven": False,
    }


def load_sbom(path: str | Path) -> dict[str, Any]:
    source = require_plain_file(path)
    if source.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("SBOM exceeds the 2 MiB safety limit.")
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("SBOM must be valid UTF-8 JSON.") from exc
    if not isinstance(value, dict):
        raise ValueError("SBOM must be a JSON object.")
    return value


def write_sbom(path: str | Path, value: Mapping[str, Any]) -> Path:
    target = Path(path).expanduser()
    if target.exists() or target.is_symlink():
        raise ValueError("SBOM output already exists; evidence is never overwritten.")
    parent_value = target.parent.absolute()
    _require_no_redirected_ancestor(parent_value)
    parent = parent_value.resolve(strict=True)
    if not parent.is_dir():
        raise ValueError("SBOM output parent must be an existing directory.")
    resolved = parent / target.name
    resolved.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return resolved


def _validate_dependency_audit(value: Mapping[str, Any]) -> dict[str, Any]:
    if value.get("passed") is not True:
        raise ValueError("Dependency audit must have passed before SBOM generation.")
    if value.get("autocad_launched") is not False or value.get("live_publish_proven") is not False:
        raise ValueError("Dependency audit crossed a required evidence boundary.")
    lock = value.get("lock")
    inventory = value.get("python_license_inventory")
    python = value.get("python")
    if not isinstance(lock, dict) or lock.get("file") != "uv.lock":
        raise ValueError("Dependency audit lock identity is invalid.")
    if not isinstance(inventory, dict) or not isinstance(python, dict):
        raise ValueError("Dependency audit package inventory is missing.")
    packages = inventory.get("packages")
    if (
        not isinstance(packages, list)
        or not packages
        or inventory.get("package_count") != len(packages)
        or python.get("package_count") != len(packages)
        or inventory.get("unknown_count") != 0
    ):
        raise ValueError("Dependency audit package inventory is inconsistent.")
    generated_utc = value.get("generated_utc")
    _parse_timestamp(generated_utc)
    lock_sha256 = lock.get("sha256")
    requirements_sha256 = lock.get("requirements_sha256")
    if not _is_sha256(lock_sha256) or not _is_sha256(requirements_sha256):
        raise ValueError("Dependency audit lock hashes are invalid.")
    normalized: list[dict[str, str]] = []
    for item in packages:
        if not isinstance(item, dict) or set(item) != {"name", "version", "license"}:
            raise ValueError("Dependency package inventory fields are not exact.")
        if not all(isinstance(item[key], str) and item[key].strip() for key in item):
            raise ValueError("Dependency package inventory contains an empty value.")
        if item["license"] == "UNKNOWN":
            raise ValueError("Dependency package inventory contains an unknown license.")
        normalized.append({key: item[key].strip() for key in ("name", "version", "license")})
    normalized.sort(key=lambda item: _normalize_package_name(item["name"]))
    names = [_normalize_package_name(item["name"]) for item in normalized]
    if len(names) != len(set(names)):
        raise ValueError("Dependency package inventory contains duplicate packages.")
    return {
        "generated_utc": generated_utc,
        "lock_sha256": lock_sha256,
        "requirements_sha256": requirements_sha256,
        "packages": normalized,
    }


def _python_component(item: Mapping[str, str]) -> dict[str, Any]:
    name = _normalize_package_name(item["name"])
    version = item["version"]
    purl = f"pkg:pypi/{quote(name, safe='.-_')}@{quote(version, safe='.+-_')}"
    return {
        "type": "library",
        "bom-ref": purl,
        "name": name,
        "version": version,
        "purl": purl,
        "licenses": [{"license": {"name": item["license"]}}],
        "properties": [
            {"name": "cadplot:ecosystem", "value": "python"},
            {"name": "cadplot:scope", "value": "runtime"},
        ],
    }


def _artifact_component(name: str, path: Path) -> dict[str, Any]:
    if not _ARTIFACT_NAME.fullmatch(name):
        raise ValueError("Artifact names must be lowercase kebab-case identifiers.")
    return {
        "type": "file",
        "bom-ref": f"urn:cadplot:artifact:{name}",
        "name": name,
        "hashes": [{"alg": "SHA-256", "content": _sha256(path)}],
        "properties": [{"name": "cadplot:release-artifact", "value": "true"}],
    }


def _validate_runtime_component(component: Mapping[str, Any]) -> None:
    if set(component) != {"type", "bom-ref", "name", "version", "purl", "licenses", "properties"}:
        raise ValueError("SBOM runtime component fields are not exact.")
    if component["bom-ref"] != component["purl"] or not str(component["purl"]).startswith(
        "pkg:pypi/"
    ):
        raise ValueError("SBOM runtime component package URL is invalid.")
    if component["name"] != _normalize_package_name(str(component["name"])):
        raise ValueError("SBOM runtime component name is not normalized.")
    licenses = component["licenses"]
    if (
        not isinstance(licenses, list)
        or len(licenses) != 1
        or not isinstance(licenses[0], dict)
        or not isinstance(licenses[0].get("license"), dict)
        or not isinstance(licenses[0]["license"].get("name"), str)
        or not licenses[0]["license"]["name"]
        or licenses[0]["license"]["name"] == "UNKNOWN"
    ):
        raise ValueError("SBOM runtime component license is invalid.")
    if _properties(component["properties"]) != {
        "cadplot:ecosystem": "python",
        "cadplot:scope": "runtime",
    }:
        raise ValueError("SBOM runtime component properties are invalid.")


def _validate_artifact_component(component: Mapping[str, Any]) -> None:
    if set(component) != {"type", "bom-ref", "name", "hashes", "properties"}:
        raise ValueError("SBOM artifact component fields are not exact.")
    name = component["name"]
    if not isinstance(name, str) or not _ARTIFACT_NAME.fullmatch(name):
        raise ValueError("SBOM artifact name is invalid.")
    if component["bom-ref"] != f"urn:cadplot:artifact:{name}":
        raise ValueError("SBOM artifact reference is invalid.")
    hashes = component["hashes"]
    if (
        not isinstance(hashes, list)
        or len(hashes) != 1
        or hashes[0].get("alg") != "SHA-256"
        or not _is_sha256(hashes[0].get("content"))
    ):
        raise ValueError("SBOM artifact hash is invalid.")
    if _properties(component["properties"]) != {"cadplot:release-artifact": "true"}:
        raise ValueError("SBOM artifact properties are invalid.")


def _properties(value: Any) -> dict[str, str]:
    if not isinstance(value, list):
        raise ValueError("SBOM properties must be a list.")
    result: dict[str, str] = {}
    for item in value:
        if not isinstance(item, dict) or set(item) != {"name", "value"}:
            raise ValueError("SBOM property fields are not exact.")
        name = item["name"]
        item_value = item["value"]
        if not isinstance(name, str) or not isinstance(item_value, str) or name in result:
            raise ValueError("SBOM property is invalid or duplicated.")
        result[name] = item_value
    return result


def _validate_identity(exact_commit: str, package_version: str) -> None:
    if not _COMMIT.fullmatch(exact_commit):
        raise ValueError("exact_commit must be a lowercase 40-character Git SHA-1.")
    if not _VERSION.fullmatch(package_version):
        raise ValueError("package_version is invalid.")


def _normalize_package_name(value: str) -> str:
    normalized = re.sub(r"[-_.]+", "-", value.strip()).lower()
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", normalized):
        raise ValueError("Python package name is invalid.")
    return normalized


def require_plain_file(value: str | Path) -> Path:
    candidate = Path(value).expanduser().absolute()
    _require_no_redirected_ancestor(candidate)
    path = candidate.resolve(strict=True)
    if not path.is_file():
        raise ValueError("SBOM input must be a plain file.")
    return path


def _require_no_redirected_ancestor(value: Path) -> None:
    current = value
    while True:
        if not current.exists():
            raise ValueError("SBOM path ancestor does not exist.")
        item = os.lstat(current)
        attributes = getattr(item, "st_file_attributes", 0)
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        if current.is_symlink() or attributes & reparse_flag:
            raise ValueError("SBOM paths must not pass through a symlink or reparse point.")
        parent = current.parent
        if parent == current:
            return
        current = parent


def _parse_timestamp(value: Any) -> datetime:
    if not isinstance(value, str) or len(value) > 40:
        raise ValueError("SBOM timestamp is invalid.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("SBOM timestamp is invalid.") from exc
    if parsed.tzinfo is None:
        raise ValueError("SBOM timestamp must include a timezone.")
    return parsed


def _contains_sensitive_path(value: Any) -> bool:
    rendered = _canonical_json(value)
    return bool(
        re.search(r"(?i)(?:^|[\s\"'=])(?:[a-z]:[\\/]|\\\\|/users/|/home/)", rendered)
        or "Autodesk/AutoCAD" in rendered
    )


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and _SHA256.fullmatch(value) is not None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
