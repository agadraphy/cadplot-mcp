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
        assert "RepositoryCommit" in text
        assert "RequireRepositoryCommit" in text


def test_api_probe_is_compile_only() -> None:
    script = (REPOSITORY_ROOT / "scripts" / "probe-autocad-api.ps1").read_text(encoding="utf-8")
    project = (
        REPOSITORY_ROOT
        / "src"
        / "dotnet"
        / "CadPlotMcp.AutoCADApiProbe"
        / "CadPlotMcp.AutoCADApiProbe.csproj"
    ).read_text(encoding="utf-8")

    assert "dotnet" in script.casefold()
    assert '"R20.1" = "net45"' in script
    assert '"R24.3" = "net48"' in script
    assert '"R25.0" = "net8.0-windows"' in script
    assert "--framework $targetFramework" in script
    assert "[switch]$PassThru" in script
    assert "check-autocad-api-series.ps1" in script
    assert 'evidence_scope = "compile-only"' in script
    assert "autocad_launched = $false" in script
    assert "live_publish_proven = $false" in script
    assert "net45;net48;net8.0-windows" in project
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
        "run-synthetic-batch-demo.py",
        "300-drawing bounded synthetic batch workflow",
        "target_drawings -ne 300",
        "publish_verified -ne 0",
        "manual_review_without_receipts",
        "evidence_digest",
        "SummaryPath",
        "uv build",
        "audit-release-artifacts.py",
        "smoke-wheel-install.py",
        "smoke-bundle-install.ps1",
        "dotnet\\CadPlotMcp.sln",
        "probe-autocad-api.ps1",
        "api_probe = if ($apiProbeRan)",
        "evidence_scope -cne \"compile-only\"",
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
        "synthetic_batch_rehearsal",
        "api_probe = $preflightSummary.api_probe",
        "unexpectedly contains API evidence",
        "target_drawings -ne 300",
        "publish_verified -ne 0",
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
        "300-drawing synthetic batch evidence",
        "synthetic_batch_rehearsal = $batch",
        "api_probe = $readiness.api_probe",
        "invalid compile-only API evidence",
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
        '"cadplot-collect-pilot"',
        '"cadplot-assemble-pilot"',
        '"cadplot-validate-pilot"',
        '"pilot_cli_commands": len(pilot_commands)',
    ):
        assert required in script


def test_local_pilot_initializer_is_no_overwrite_and_reparse_gated() -> None:
    script = (REPOSITORY_ROOT / "scripts" / "new-local-pilot.ps1").read_text(encoding="utf-8")

    assert "SupportsShouldProcess = $true" in script
    assert "Destination already exists; setup never overwrites" in script
    assert "FileAttributes]::ReparsePoint" in script
    assert "company_assets_copied = $false" in script
    assert "publish_enabled = $false" in script
    assert r"examples\config.inventory.example.yaml" in script
    assert r"config\config.inventory.example.yaml" in script
    assert "cadplot-doctor --config" in script
    assert "uv run cadplot-doctor" not in script
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


def test_bundle_build_is_commit_bound_no_overwrite_and_release_verified() -> None:
    builder = (REPOSITORY_ROOT / "scripts" / "build-bundle.ps1").read_text(
        encoding="utf-8"
    )
    release_verifier = (
        REPOSITORY_ROOT / "scripts" / "verify-bundle-release.ps1"
    ).read_text(encoding="utf-8")

    for required in (
        '"rev-parse", "HEAD"',
        '"status"',
        "Real bundle build requires a clean worktree",
        "build never overwrites",
        "audit-source-tree.py",
        "audit-release-artifacts.py",
        "bundle-build.json",
        "[System.IO.FileMode]::CreateNew",
        "matching_sdk_bundle_built = $true",
        '"-p:RepositoryCommit=$commit"',
        "autocad_launched = $false",
        "live_publish_proven = $false",
        "verify-bundle-release.ps1",
    ):
        assert required in builder
    assert "Remove-Item" not in builder

    for required in (
        "$expectedTopLevel",
        "verify-bundle.ps1",
        "R20.1",
        "R25.0",
        "matching-SDK build",
        "archive_sha256",
        "ZipFile]::OpenRead",
        "archive entry hash mismatch",
        "LivePublishProven = $false",
    ):
        assert required in release_verifier
    assert "Expand-Archive" not in release_verifier
    assert "Start-Process" not in release_verifier


def test_live_plugin_and_pilot_evidence_bind_running_binary_to_bundle() -> None:
    protocol = (
        REPOSITORY_ROOT / "src" / "dotnet" / "CadPlotMcp.Core" / "Protocol.cs"
    ).read_text(encoding="utf-8")
    runtime = (
        REPOSITORY_ROOT
        / "src"
        / "dotnet"
        / "CadPlotMcp.AutoCAD.Shared"
        / "AutoCadPublishRuntime.cs"
    ).read_text(encoding="utf-8")
    pilot = (REPOSITORY_ROOT / "src" / "cadplot_mcp" / "pilot.py").read_text(
        encoding="utf-8"
    )
    identity = (
        REPOSITORY_ROOT
        / "src"
        / "dotnet"
        / "CadPlotMcp.Core"
        / "AutoCadRuntimeIdentity.cs"
    ).read_text(encoding="utf-8")
    assembler = (REPOSITORY_ROOT / "src" / "cadplot_mcp" / "pilot_cli.py").read_text(
        encoding="utf-8"
    )

    for required in (
        "buildCommit",
        "pluginSha256",
        "BuildCommit",
        "PluginSha256",
        "runtimeSeries",
        "runtimeSupported",
    ):
        assert required in protocol
    for required in (
        "Assembly.GetExecutingAssembly()",
        'ReadAssemblyMetadata(pluginAssembly, "RepositoryCommit")',
        "SHA256.Create()",
        "assembly.Location",
        "AutoCadRuntimeIdentity.NormalizeSeries",
        "AutoCadRuntimeIdentity.IsSupported",
    ):
        assert required in runtime
    for required in ("R20.1", "R25.0", "R25.1"):
        assert required in identity
    for required in (
        '"build_commit"',
        '"plugin_sha256"',
        '"runtime_series"',
        '"runtimeSupported"',
        "validate_bundle_build_evidence",
        "Bundle archive entry hash mismatch",
        "running plug-in commit mismatch",
        "running plug-in binary mismatch",
        '"schema_version": 2',
    ):
        assert required in pilot
    assert "--bundle-build-manifest" in assembler
    assert "--repository-commit" not in assembler


def test_combined_release_kit_binds_wheel_bundle_source_and_commit() -> None:
    builder = (REPOSITORY_ROOT / "scripts" / "build-release-kit.ps1").read_text(
        encoding="utf-8"
    )
    verifier = (REPOSITORY_ROOT / "scripts" / "verify-release-kit.ps1").read_text(
        encoding="utf-8"
    )

    for required in (
        "verify-bundle-release.ps1",
        "ReadinessReport",
        "Release-kit build requires a clean worktree",
        "matching-SDK artifact for the current commit",
        "Wheel hash no longer matches",
        "Python wheel and AutoCAD bundle versions do not match",
        "git archive",
        "uv.lock",
        "release-kit.json",
        "release-kit-build.json",
        "build never overwrites",
        "[System.IO.FileMode]::CreateNew",
        "matching_sdk_bundle_built = $true",
        "licensed_live_pilot_ready = $false",
        "public_release_ready = $false",
        "autocad_launched = $false",
        "live_publish_proven = $false",
        "verify-release-kit.ps1",
        "300-drawing synthetic batch evidence",
        "synthetic_batch_rehearsal = $batch",
        r'Join-Path $kitRoot "scripts\verify-release-kit.ps1"',
        "collect-pilot-run.py",
        "assemble-pilot-evidence.py",
        "validate-pilot-evidence.py",
        "new-local-pilot.ps1",
        "pilot-evidence.md",
        "self_verification_passed = $verification.Passed",
        "Readiness report has invalid compile-only API evidence",
    ):
        assert required in builder
    assert "Remove-Item" not in builder
    assert "Start-Process" not in builder

    for required in (
        "$expectedTopLevel",
        "$expectedDirectories",
        "$fixedFiles",
        "verify-bundle-release.ps1",
        "Release-kit file hash mismatch",
        "kit_manifest_sha256",
        "kit_archive_sha256",
        "ZipFile]::OpenRead",
        "archive entry hash mismatch",
        "MatchingSdkBundleBuilt = $manifest.matching_sdk_bundle_built -eq $true",
        "LivePublishProven = $false",
        "300-drawing synthetic batch evidence",
        '"scripts/verify-release-kit.ps1"',
        '"scripts/new-local-pilot.ps1"',
        '"scripts/collect-pilot-run.py"',
        '"scripts/assemble-pilot-evidence.py"',
        '"scripts/validate-pilot-evidence.py"',
        '"docs/pilot-evidence.md"',
    ):
        assert required in verifier
    assert "Expand-Archive" not in verifier
    assert "Start-Process" not in verifier


def test_release_kit_install_guide_keeps_live_and_company_assets_external() -> None:
    guide = (REPOSITORY_ROOT / "docs" / "release-kit-install.md").read_text(
        encoding="utf-8"
    )

    assert "verify-release-kit.ps1" in guide
    assert "install-bundle.ps1" in guide
    assert "uv tool install $wheelPath" in guide
    assert "no source checkout is required" in guide
    assert "cadplot-collect-pilot --help" in guide
    assert "cadplot-assemble-pilot --help" in guide
    assert "cadplot-validate-pilot --help" in guide
    assert "new-local-pilot.ps1" in guide
    assert "publisher authenticity" in guide
    assert "--from" not in guide
    assert "CADPLOT_ENABLE_PUBLISH" in guide
    assert "company DWG, PC3, PMP, CTB/STB, DWT" in guide
    assert "does not mean AutoCAD was launched" in guide


def test_release_kit_smoke_is_explicitly_protocol_only_and_tamper_checked() -> None:
    script = (REPOSITORY_ROOT / "scripts" / "smoke-release-kit.ps1").read_text(
        encoding="utf-8"
    )

    for required in (
        "protocol_only_fixture = $true",
        "matching_sdk_bundle_built = $false",
        "protocol_only_rejected_as_real",
        "exact_tree_and_hashes_verified",
        "archive_tamper_blocked",
        "AllowProtocolOnlyFixture",
        "autocad_launched = $false",
        "live_publish_proven = $false",
        "synthetic_batch_rehearsal",
        "manual_review_without_receipts = 300",
        "embedded_self_verification_passed = $true",
    ):
        assert required in script
    assert "Start-Process" not in script


def test_synthetic_batch_runner_is_bounded_and_never_overwrites_evidence() -> None:
    script = (REPOSITORY_ROOT / "scripts" / "run-synthetic-batch-demo.py").read_text(
        encoding="utf-8"
    )

    assert 'parser.add_argument("--drawings", type=int, default=300)' in script
    assert 'output.open("x"' in script
    assert "run_synthetic_batch_demo" in script
    assert "canonical_batch_demo_digest" in script


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
        "protocol_only_rejected_as_real = $protocolOnlyRejected",
        "bundle_release_verified = $true",
        "bundle_release_archive_tamper_blocked = $archiveTamperBlocked",
        "release_kit_self_verification_passed",
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
