from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path
from typing import Any


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Audit exact locked Python production dependencies and .NET packages against "
            "current vulnerability databases."
        )
    )
    parser.add_argument("--uv", default="uv")
    parser.add_argument("--dotnet", default="dotnet")
    parser.add_argument("--repository", default=str(Path(__file__).resolve().parents[1]))
    args = parser.parse_args()

    repository = Path(args.repository).expanduser().resolve(strict=True)
    uv_lock = repository / "uv.lock"
    solution = repository / "src" / "dotnet" / "CadPlotMcp.sln"
    if not uv_lock.is_file() or not solution.is_file():
        return _fail("Repository is missing uv.lock or the .NET solution.")

    try:
        with tempfile.TemporaryDirectory(prefix="cadplot-dependency-audit-") as temporary:
            requirements = Path(temporary) / "requirements.txt"
            export_result = _run(
                [
                    args.uv,
                    "export",
                    "--frozen",
                    "--no-dev",
                    "--no-emit-project",
                    "--format",
                    "requirements-txt",
                ],
                cwd=repository,
            )
            requirements.write_text(export_result.stdout, encoding="utf-8", newline="\n")
            python_result = _run_audit(
                [
                    sys.executable,
                    "-m",
                    "pip_audit",
                    "--requirement",
                    str(requirements),
                    "--require-hashes",
                    "--no-deps",
                    "--format",
                    "json",
                ],
                cwd=repository,
            )
            dotnet_result = _run_json(
                [
                    args.dotnet,
                    "list",
                    str(solution),
                    "package",
                    "--vulnerable",
                    "--include-transitive",
                    "--format",
                    "json",
                ],
                cwd=repository,
            )

            python_dependencies = python_result.get("dependencies")
            if not isinstance(python_dependencies, list) or not python_dependencies:
                raise ValueError("pip-audit returned no locked production dependencies.")
            python_vulnerabilities = sum(
                len(item.get("vulns", []))
                for item in python_dependencies
                if isinstance(item, dict) and isinstance(item.get("vulns", []), list)
            )
            dotnet_projects = dotnet_result.get("projects")
            if not isinstance(dotnet_projects, list) or len(dotnet_projects) < 4:
                raise ValueError(".NET audit did not cover all expected projects.")
            dotnet_sources = dotnet_result.get("sources")
            if not isinstance(dotnet_sources, list) or not dotnet_sources:
                raise ValueError(".NET audit returned no configured advisory source.")
            dotnet_vulnerabilities = _count_named_lists(dotnet_result, "vulnerabilities")
            license_inventory = _license_inventory(python_dependencies)

            result = {
                "schema_version": 1,
                "generated_utc": datetime.now(UTC).isoformat(),
                "passed": (
                    python_vulnerabilities == 0
                    and dotnet_vulnerabilities == 0
                    and license_inventory["unknown_count"] == 0
                ),
                "lock": {
                    "file": "uv.lock",
                    "sha256": _sha256(uv_lock),
                    "requirements_sha256": _sha256(requirements),
                },
                "python": {
                    "tool": _version([sys.executable, "-m", "pip_audit", "--version"]),
                    "package_count": len(python_dependencies),
                    "vulnerability_count": python_vulnerabilities,
                    "database": "PyPI Advisory Database via pip-audit",
                },
                "dotnet": {
                    "tool": _version([args.dotnet, "--version"]),
                    "project_count": len(dotnet_projects),
                    "vulnerability_count": dotnet_vulnerabilities,
                    "source_count": len(dotnet_sources),
                    "database": "configured NuGet audit sources",
                },
                "python_license_inventory": license_inventory,
                "network_database_check": True,
                "autocad_launched": False,
                "live_publish_proven": False,
            }
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        return _fail(str(exc))

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


def _run(command: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise subprocess.CalledProcessError(result.returncode, command, detail)
    return result


def _run_json(command: list[str], *, cwd: Path) -> dict[str, Any]:
    result = _run(command, cwd=cwd)
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Dependency audit returned invalid JSON: {command[0]}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"Dependency audit returned a non-object: {command[0]}")
    return value


def _run_audit(command: list[str], *, cwd: Path) -> dict[str, Any]:
    result = subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False)
    if result.returncode not in {0, 1}:
        detail = (result.stderr or result.stdout).strip()
        raise subprocess.CalledProcessError(result.returncode, command, detail)
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError("pip-audit returned invalid JSON.") from exc
    if not isinstance(value, dict):
        raise ValueError("pip-audit returned a non-object.")
    return value


def _count_named_lists(value: Any, key: str) -> int:
    if isinstance(value, dict):
        count = 0
        for name, item in value.items():
            if name.casefold() == key.casefold() and isinstance(item, list):
                count += len(item)
            else:
                count += _count_named_lists(item, key)
        return count
    if isinstance(value, list):
        return sum(_count_named_lists(item, key) for item in value)
    return 0


def _license_inventory(dependencies: list[Any]) -> dict[str, Any]:
    packages: list[dict[str, str]] = []
    for item in dependencies:
        if not isinstance(item, dict):
            raise ValueError("pip-audit dependency entry is invalid.")
        name = item.get("name")
        version = item.get("version")
        if not isinstance(name, str) or not name or not isinstance(version, str) or not version:
            raise ValueError("pip-audit dependency identity is invalid.")
        try:
            installed = distribution(name)
        except PackageNotFoundError as exc:
            raise ValueError(
                f"Installed metadata is unavailable for audited package: {name}"
            ) from exc
        if installed.version != version:
            raise ValueError(
                f"Installed metadata version does not match the audited lock: {name} "
                f"({installed.version} != {version})"
            )
        expression = _declared_license(installed.metadata)
        packages.append({"name": name, "version": version, "license": expression})

    packages.sort(key=lambda value: value["name"].casefold())
    names = [item["name"].casefold() for item in packages]
    if len(names) != len(set(names)):
        raise ValueError("Dependency license inventory contains duplicate packages.")
    return {
        "package_count": len(packages),
        "unknown_count": sum(item["license"] == "UNKNOWN" for item in packages),
        "packages": packages,
    }


def _declared_license(metadata: Any) -> str:
    candidates = [metadata.get("License-Expression"), metadata.get("License")]
    for candidate in candidates:
        if not isinstance(candidate, str):
            continue
        normalized = " ".join(candidate.split())
        if normalized and normalized.casefold() != "unknown" and len(normalized) <= 200:
            return normalized
    classifiers = [
        value.removeprefix("License :: ").strip()
        for value in metadata.get_all("Classifier", [])
        if isinstance(value, str) and value.startswith("License :: ")
    ]
    classifiers = sorted(set(value for value in classifiers if value))
    return "; ".join(classifiers) if classifiers else "UNKNOWN"


def _version(command: list[str]) -> str:
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise ValueError(f"Could not read tool version: {command[0]}")
    value = (result.stdout or result.stderr).strip()
    if not value or len(value) > 200 or any(ord(character) < 32 for character in value):
        raise ValueError(f"Tool returned an invalid version string: {command[0]}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _fail(message: str) -> int:
    print(json.dumps({"passed": False, "error": message}, ensure_ascii=False, indent=2))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
