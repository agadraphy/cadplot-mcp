from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from cadplot_mcp.sbom import build_sbom, load_sbom, validate_sbom, write_sbom

COMMIT = "1" * 40


def _audit() -> dict[str, object]:
    return {
        "generated_utc": "2026-08-09T12:34:56+00:00",
        "passed": True,
        "lock": {
            "file": "uv.lock",
            "sha256": "2" * 64,
            "requirements_sha256": "3" * 64,
        },
        "python": {"package_count": 2},
        "python_license_inventory": {
            "package_count": 2,
            "unknown_count": 0,
            "packages": [
                {"name": "PyYAML", "version": "6.0.2", "license": "MIT"},
                {"name": "typing_extensions", "version": "4.15.0", "license": "PSF-2.0"},
            ],
        },
        "autocad_launched": False,
        "live_publish_proven": False,
    }


def _build(tmp_path: Path) -> dict[str, object]:
    wheel = tmp_path / "cadplot.whl"
    source = tmp_path / "source.zip"
    wheel.write_bytes(b"wheel")
    source.write_bytes(b"source")
    return build_sbom(
        exact_commit=COMMIT,
        package_version="0.1.0",
        dependency_audit=_audit(),
        artifacts={"wheel": wheel, "source-archive": source},
    )


def test_sbom_is_deterministic_release_bound_and_path_redacted(tmp_path: Path) -> None:
    first = _build(tmp_path)
    second = _build(tmp_path)

    assert first == second
    evidence = validate_sbom(
        first,
        expected_commit=COMMIT,
        expected_version="0.1.0",
        expected_artifact_count=2,
        expected_runtime_count=2,
    )
    assert evidence["component_count"] == 4
    assert evidence["autocad_launched"] is False
    assert str(tmp_path) not in json.dumps(first)
    artifact = next(item for item in first["components"] if item["name"] == "wheel")
    assert artifact["hashes"][0]["content"] == hashlib.sha256(b"wheel").hexdigest()
    assert first["compositions"] == [
        {"aggregate": "incomplete", "assemblies": ["pkg:pypi/cadplot-mcp@0.1.0"]}
    ]


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda value: value["metadata"]["component"]["properties"][3].__setitem__(
                "value", "true"
            ),
            "evidence boundary",
        ),
        (
            lambda value: value["components"][-1]["hashes"][0].__setitem__("content", "0" * 64),
            "serialNumber",
        ),
        (
            lambda value: value["components"][0].__setitem__("name", "C:\\Users\\secret"),
            "package name is invalid",
        ),
    ],
)
def test_sbom_rejects_tampering(tmp_path: Path, mutate: object, message: str) -> None:
    value = copy.deepcopy(_build(tmp_path))
    mutate(value)  # type: ignore[operator]
    with pytest.raises(ValueError, match=message):
        validate_sbom(value)


def test_sbom_write_is_bom_free_and_never_overwrites(tmp_path: Path) -> None:
    value = _build(tmp_path)
    target = tmp_path / "cadplot-mcp.cdx.json"
    written = write_sbom(target, value)

    assert written.read_bytes().startswith(b"{")
    assert load_sbom(written) == value
    with pytest.raises(ValueError, match="never overwritten"):
        write_sbom(target, value)


def test_sbom_requires_passing_complete_dependency_audit(tmp_path: Path) -> None:
    artifact = tmp_path / "wheel.whl"
    artifact.write_bytes(b"wheel")
    audit = _audit()
    audit["passed"] = False

    with pytest.raises(ValueError, match="must have passed"):
        build_sbom(
            exact_commit=COMMIT,
            package_version="0.1.0",
            dependency_audit=audit,
            artifacts={"wheel": artifact},
        )


def test_sbom_rejects_redirected_artifact(tmp_path: Path) -> None:
    artifact = tmp_path / "wheel.whl"
    artifact.write_bytes(b"wheel")
    link = tmp_path / "wheel-link.whl"
    try:
        link.symlink_to(artifact)
    except OSError:
        pytest.skip("This Windows account cannot create a test symlink.")

    with pytest.raises(ValueError, match="symlink or reparse point"):
        build_sbom(
            exact_commit=COMMIT,
            package_version="0.1.0",
            dependency_audit=_audit(),
            artifacts={"wheel": link},
        )
