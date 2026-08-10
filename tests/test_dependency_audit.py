from __future__ import annotations

import runpy
import subprocess
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPOSITORY_ROOT / "scripts" / "audit-dependencies.py"


class _Metadata:
    def __init__(self, values: dict[str, str], classifiers: list[str] | None = None) -> None:
        self._values = values
        self._classifiers = classifiers or []

    def get(self, key: str) -> str | None:
        return self._values.get(key)

    def get_all(self, key: str, default: list[str]) -> list[str]:
        return self._classifiers if key == "Classifier" else default


def test_vulnerability_counter_finds_nested_advisory_lists() -> None:
    namespace = runpy.run_path(str(SCRIPT))
    count = namespace["_count_named_lists"]

    value = {
        "projects": [
            {"frameworks": [{"packages": [{"vulnerabilities": [{"id": "A"}]}]}]},
            {"frameworks": [{"packages": [{"vulnerabilities": []}]}]},
        ]
    }

    assert count(value, "vulnerabilities") == 1


def test_declared_license_prefers_expression_and_falls_back_to_classifiers() -> None:
    namespace = runpy.run_path(str(SCRIPT))
    declared_license = namespace["_declared_license"]

    assert (
        declared_license(_Metadata({"License-Expression": " MIT OR Apache-2.0 "}))
        == "MIT OR Apache-2.0"
    )
    assert (
        declared_license(
            _Metadata({}, ["License :: OSI Approved :: MIT License", "Topic :: Utilities"])
        )
        == "OSI Approved :: MIT License"
    )
    assert declared_license(_Metadata({})) == "UNKNOWN"


def test_dependency_audit_binds_locked_production_set_and_both_ecosystems() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    for required in (
        '"--frozen"',
        '"--no-dev"',
        '"--no-emit-project"',
        "requirements.write_text(export_result.stdout",
        '"--require-hashes"',
        '"--no-deps"',
        '"pip_audit"',
        '"--vulnerable"',
        '"--include-transitive"',
        'raise ValueError(".NET audit returned no configured advisory source.")',
        '"network_database_check": True',
        '"python_license_inventory": license_inventory',
        'license_inventory["unknown_count"] == 0',
        '"autocad_launched": False',
        '"live_publish_proven": False',
    ):
        assert required in source


def test_dotnet_audit_restores_before_listing_packages() -> None:
    namespace = runpy.run_path(str(SCRIPT))
    run_dotnet_audit = namespace["_run_dotnet_audit"]
    calls: list[list[str]] = []

    def fake_run(command: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="{}", stderr="")

    run_dotnet_audit.__globals__["_run"] = fake_run
    repository = Path("C:/clean-checkout")
    solution = repository / "src" / "dotnet" / "CadPlotMcp.sln"

    assert run_dotnet_audit("dotnet", solution=solution, cwd=repository) == {}
    assert calls == [
        ["dotnet", "restore", str(solution), "--nologo"],
        [
            "dotnet",
            "list",
            str(solution),
            "package",
            "--vulnerable",
            "--include-transitive",
            "--format",
            "json",
        ],
    ]
