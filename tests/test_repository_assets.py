import json
import re
import xml.etree.ElementTree as element_tree
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def test_local_mcp_example_is_valid_json() -> None:
    path = REPOSITORY_ROOT / "examples" / "mcp.local.example.json"

    value = json.loads(path.read_text(encoding="utf-8"))

    server = value["mcpServers"]["cadplot"]
    assert server["command"] == "uv"
    assert "CADPLOT_CONFIG" in server["env"]


def test_repository_markdown_local_links_exist() -> None:
    documents = list(REPOSITORY_ROOT.glob("*.md"))
    for folder in ("docs", "bundle", "integrations"):
        documents.extend((REPOSITORY_ROOT / folder).rglob("*.md"))

    missing: list[str] = []
    for document in documents:
        text = document.read_text(encoding="utf-8")
        for target in re.findall(r"\[[^]]+\]\(([^)]+)\)", text):
            if "://" in target or target.startswith("#"):
                continue
            path_part = target.split("#", 1)[0]
            if path_part and not (document.parent / path_part).resolve().exists():
                relative_document = document.relative_to(REPOSITORY_ROOT)
                missing.append(f"{relative_document}: {target}")

    assert missing == []


def test_bundle_routes_supported_autocad_series() -> None:
    path = REPOSITORY_ROOT / "bundle" / "CadPlotMcp.bundle" / "PackageContents.xml"
    root = element_tree.parse(path).getroot()
    routes = {
        (
            item.attrib["SeriesMin"],
            item.attrib["SeriesMax"],
        )
        for item in root.findall("./Components/ComponentEntry/RuntimeRequirements")
    }

    assert routes == {("R20.1", "R20.1"), ("R25.0", "R25.1")}


def test_repository_contains_mit_license() -> None:
    license_text = (REPOSITORY_ROOT / "LICENSE").read_text(encoding="utf-8")

    assert "MIT License" in license_text
    assert "Demir Eren" in license_text


def test_version_adapters_require_all_managed_autocad_references() -> None:
    for release in ("2016", "2025"):
        project = (
            REPOSITORY_ROOT
            / "src"
            / "dotnet"
            / f"CadPlotMcp.AutoCAD{release}"
            / (f"CadPlotMcp.AutoCAD{release}.csproj")
        )
        text = project.read_text(encoding="utf-8")

        assert "AcMgd.dll" in text
        assert "AcDbMgd.dll" in text
        assert "AcCoreMgd.dll" in text
        assert "AutoCadPublishRuntime.cs" in text


def test_api_probe_is_compile_only() -> None:
    script = (REPOSITORY_ROOT / "scripts" / "probe-autocad-api.ps1").read_text(encoding="utf-8")

    assert "dotnet" in script.casefold()
    assert "AutoCAD was not launched" in script
    assert "Start-Process" not in script


def test_autocad_api_series_checker_binds_bundle_build_to_exact_releases() -> None:
    checker = (REPOSITORY_ROOT / "scripts" / "check-autocad-api-series.ps1").read_text(
        encoding="utf-8"
    )
    builder = (REPOSITORY_ROOT / "scripts" / "build-bundle.ps1").read_text(
        encoding="utf-8"
    )

    for required in (
        "AcMgd.dll",
        "AcDbMgd.dll",
        "AcCoreMgd.dll",
        "GetAssemblyName",
        "AssemblyVersion",
        "Get-FileHash",
        "ExpectedSeries",
        "AutoCADLaunched = $false",
        "LivePublishProven = $false",
    ):
        assert required in checker
    assert "Start-Process" not in checker
    assert 'ExpectedSeries "R20.1"' in builder
    assert 'ExpectedSeries "R25.0"' in builder
    assert "check-autocad-api-series.ps1" in builder


def test_local_preflight_is_fail_fast_and_does_not_launch_autocad() -> None:
    script = (REPOSITORY_ROOT / "scripts" / "run-local-preflight.ps1").read_text(encoding="utf-8")

    for required in (
        "uv sync --frozen",
        "audit-source-tree.py",
        "uv run ruff check .",
        "uv run pytest -q",
        "run-synthetic-demo.py",
        "uv build",
        "audit-release-artifacts.py",
        "smoke-wheel-install.py",
        "dotnet\\CadPlotMcp.sln",
        "probe-autocad-api.ps1",
        "autocad_launched = $false",
        "live_publish_proven = $false",
    ):
        assert required in script
    assert '$ErrorActionPreference = "Stop"' in script
    assert "Start-Process" not in script


def test_demo_rehearsal_is_commit_bound_and_keeps_live_claims_false() -> None:
    script = (REPOSITORY_ROOT / "scripts" / "run-demo-rehearsal.ps1").read_text(
        encoding="utf-8"
    )

    for required in (
        "run-local-preflight.ps1",
        "$initialCommitLines = @(Invoke-GitReadOnly",
        "$commitLines = @(Invoke-GitReadOnly",
        '"rev-parse", "HEAD"',
        '"status"',
        '"--porcelain=v1"',
        "Get-FileHash",
        "local_demo_ready = $true",
        "licensed_live_pilot_ready = $false",
        "public_release_ready = $false",
        "autocad_launched = $false",
        "live_publish_proven = $false",
        "company_assets_copied = $false",
        "[System.IO.FileMode]::CreateNew",
    ):
        assert required in script
    assert "Start-Process" not in script


def test_demo_kit_is_readiness_bound_and_never_overwrites() -> None:
    script = (REPOSITORY_ROOT / "scripts" / "build-demo-kit.ps1").read_text(
        encoding="utf-8"
    )

    for required in (
        "ReadinessReport",
        "git archive",
        "Wheel hash no longer matches",
        "current commit",
        "packaging never overwrites",
        "[System.IO.FileMode]::CreateNew",
        "company_assets_copied = $false",
        "autodesk_binaries_included = $false",
        "live_publish_proven = $false",
        "not a live AutoCAD plug-in bundle",
    ):
        assert required in script
    assert "Remove-Item" not in script
    assert "Start-Process" not in script


def test_wheel_smoke_uses_frozen_hashed_dependencies_and_no_source_import() -> None:
    script = (REPOSITORY_ROOT / "scripts" / "smoke-wheel-install.py").read_text(
        encoding="utf-8"
    )

    for required in (
        '"export"',
        '"--frozen"',
        '"--no-emit-project"',
        '"--require-hashes"',
        '"--no-deps"',
        'environment.pop("PYTHONPATH", None)',
        'environment["PYTHONNOUSERSITE"] = "1"',
        '"source_tree_imported": False',
    ):
        assert required in script


def test_local_pilot_initializer_is_no_overwrite_and_reparse_gated() -> None:
    script = (REPOSITORY_ROOT / "scripts" / "new-local-pilot.ps1").read_text(encoding="utf-8")

    assert "SupportsShouldProcess = $true" in script
    assert "Destination already exists; setup never overwrites" in script
    assert "FileAttributes]::ReparsePoint" in script
    assert "company_assets_copied = $false" in script
    assert "publish_enabled = $false" in script
    assert "Remove-Item" not in script


def test_build_and_install_require_exact_bundle_verification() -> None:
    verifier = (REPOSITORY_ROOT / "scripts" / "verify-bundle.ps1").read_text(encoding="utf-8")
    for required in (
        "PackageContents.xml",
        "CadPlotMcp.AutoCAD2016.dll",
        "CadPlotMcp.AutoCAD2025.dll",
        "CadPlotMcp.Core.dll",
        "GetAssemblyName",
        "Get-FileHash",
        "ReparsePoint",
        "redirected file",
        "$expectedDirectories",
        "$PassThru",
    ):
        assert required in verifier

    for script_name in ("build-bundle.ps1", "install-bundle.ps1", "uninstall-bundle.ps1"):
        script = (REPOSITORY_ROOT / "scripts" / script_name).read_text(encoding="utf-8")
        assert "verify-bundle.ps1" in script

    installer = (REPOSITORY_ROOT / "scripts" / "install-bundle.ps1").read_text(
        encoding="utf-8"
    )
    for required in (
        "Assert-NoRedirectedAncestor",
        "Assert-SameBundleHashes",
        ".CadPlotMcp.bundle.installing-",
        "[System.IO.Directory]::Move",
        "Installed verified bundle atomically",
        "non-loadable staging directory was retained",
    ):
        assert required in installer
    assert "Remove-Item" not in installer


def test_uninstaller_is_identity_gated_and_supports_what_if() -> None:
    script = (REPOSITORY_ROOT / "scripts" / "uninstall-bundle.ps1").read_text(encoding="utf-8")

    assert "SupportsShouldProcess = $true" in script
    assert "C2E79B66-6076-40D4-AE45-E725A644B288" in script
    assert "ReparsePoint" in script
    assert "ShouldProcess($destinationBundle" in script
    assert "verify-bundle.ps1" in script
    assert "Remove-Item -LiteralPath $destinationBundle" in script


def test_bundle_install_smoke_is_protocol_only_and_never_launches_autocad() -> None:
    script = (REPOSITORY_ROOT / "scripts" / "smoke-bundle-install.ps1").read_text(
        encoding="utf-8"
    )

    for required in (
        "install-bundle.ps1",
        "uninstall-bundle.ps1",
        "verify-bundle.ps1",
        "-WhatIf",
        "protocol_only_fixture = $true",
        "copied_hashes_verified = $true",
        "existing_install_blocked = $overwriteBlocked",
        "unexpected_file_uninstall_blocked = $unexpectedFileBlocked",
        "autocad_launched = $false",
        "live_publish_proven = $false",
    ):
        assert required in script
    assert "Start-Process" not in script


def test_codex_plugin_manifest_routes_installed_cadplot_cli() -> None:
    plugin_root = REPOSITORY_ROOT / "integrations" / "codex" / "cadplot-mcp"
    manifest = json.loads(
        (plugin_root / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
    )
    mcp = json.loads((plugin_root / ".mcp.json").read_text(encoding="utf-8"))

    assert manifest["name"] == "cadplot-mcp"
    assert manifest["license"] == "MIT"
    assert manifest["mcpServers"] == "./.mcp.json"
    assert "Write" in manifest["interface"]["capabilities"]
    assert mcp["mcpServers"]["cadplot"] == {"command": "cadplot-mcp", "args": []}


def test_deployment_docs_separate_local_and_remote_boundaries() -> None:
    deployment = (REPOSITORY_ROOT / "docs" / "deployment-modes.md").read_text(encoding="utf-8")
    architecture = (REPOSITORY_ROOT / "docs" / "chatgpt-connection.md").read_text(encoding="utf-8")

    assert "Local workstation" in deployment
    assert "ChatGPT web developer pilot" in deployment
    assert "Managed company deployment" in deployment
    assert "Public ChatGPT plugin" in deployment
    assert "Bridge not implemented" in deployment
    assert "named pipe" in deployment
    assert "publish_verified=true" in deployment
    assert "The DWG stays" in architecture
    assert "Not implemented or claimed" in architecture


def test_github_templates_warn_against_proprietary_assets_and_false_evidence() -> None:
    bug = (REPOSITORY_ROOT / ".github" / "ISSUE_TEMPLATE" / "bug_report.yml").read_text(
        encoding="utf-8"
    )
    feature = (REPOSITORY_ROOT / ".github" / "ISSUE_TEMPLATE" / "feature_request.yml").read_text(
        encoding="utf-8"
    )
    pull_request = (REPOSITORY_ROOT / ".github" / "PULL_REQUEST_TEMPLATE.md").read_text(
        encoding="utf-8"
    )
    normalized_pull_request = " ".join(pull_request.split())

    assert "proprietary" in bug.casefold()
    assert "unauthorized AutoCAD" in bug
    assert "does not bypass plan or manifest approval gates" in feature
    assert "Source DWGs remain immutable" in pull_request
    assert "compile-only or synthetic checks as live AutoCAD evidence" in normalized_pull_request


def test_ci_is_bounded_read_only_and_runs_protocol_and_synthetic_smokes() -> None:
    workflow = (REPOSITORY_ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")

    assert "permissions:\n  contents: read" in workflow
    assert "timeout-minutes: 20" in workflow
    assert "cancel-in-progress: true" in workflow
    assert "scripts/smoke-mcp-stdio.py" in workflow
    assert "scripts/run-synthetic-demo.py" in workflow
    assert "uv sync --frozen" in workflow
    assert "scripts/audit-source-tree.py" in workflow
    assert "scripts/smoke-wheel-install.py" in workflow
