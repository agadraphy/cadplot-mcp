from __future__ import annotations

import tomllib
from pathlib import Path

from cadplot_protocol import remote_protocol as canonical

from cadplot_mcp import remote_protocol as compatibility


def test_legacy_worker_import_reexports_canonical_protocol_models() -> None:
    assert compatibility.WorkerTaskEnvelope is canonical.WorkerTaskEnvelope
    assert compatibility.WorkerResultEnvelope is canonical.WorkerResultEnvelope
    assert compatibility.PROTOCOL_VERSION == canonical.PROTOCOL_VERSION


def test_root_wheel_bundles_protocol_without_unpublished_runtime_dependency() -> None:
    project_root = Path(__file__).resolve().parents[1]
    pyproject = tomllib.loads((project_root / "pyproject.toml").read_text(encoding="utf-8"))

    wheel_packages = pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"]
    assert "src/cadplot_mcp" in wheel_packages
    assert "packages/protocol/src/cadplot_protocol" in wheel_packages

    runtime_dependencies = pyproject["project"]["dependencies"]
    assert not any(
        dependency.casefold().startswith("cadplot-protocol") for dependency in runtime_dependencies
    )


def test_root_sdist_excludes_local_uv_cache() -> None:
    project_root = Path(__file__).resolve().parents[1]
    pyproject = tomllib.loads((project_root / "pyproject.toml").read_text(encoding="utf-8"))

    exclusions = pyproject["tool"]["hatch"]["build"]["targets"]["sdist"]["exclude"]
    assert "/.uv-cache" in exclusions
    assert "**/.uv-cache" in (project_root / ".dockerignore").read_text(encoding="utf-8")


def test_standalone_distributions_package_the_mit_license() -> None:
    project_root = Path(__file__).resolve().parents[1]
    canonical_license = (project_root / "LICENSE").read_text(encoding="utf-8")

    for relative_root in (Path("packages/protocol"), Path("services/gateway")):
        package_root = project_root / relative_root
        pyproject = tomllib.loads((package_root / "pyproject.toml").read_text(encoding="utf-8"))
        assert pyproject["project"]["license"] == {"file": "LICENSE"}
        assert (package_root / "LICENSE").read_text(encoding="utf-8").strip() == (
            canonical_license.strip()
        )

    dockerfile = (project_root / "services/gateway/Dockerfile").read_text(encoding="utf-8")
    assert "services/gateway/LICENSE" in dockerfile
