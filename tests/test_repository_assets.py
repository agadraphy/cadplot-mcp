import json
import re
import xml.etree.ElementTree as element_tree
from pathlib import Path

import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def test_local_mcp_example_is_valid_json() -> None:
    path = REPOSITORY_ROOT / "examples" / "mcp.local.example.json"

    value = json.loads(path.read_text(encoding="utf-8"))

    server = value["mcpServers"]["cadplot"]
    assert server["command"] == "uv"
    assert "CADPLOT_CONFIG" in server["env"]
    assert server["env"]["CADPLOT_PIPE_NAME"] == "cadplot-mcp"
    assert server["env"]["CADPLOT_AUTOCAD_PROGID"] == "AutoCAD.Application"


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


def test_completion_audit_preserves_local_and_live_evidence_boundaries() -> None:
    audit = (REPOSITORY_ROOT / "docs" / "completion-audit.md").read_text(
        encoding="utf-8"
    )

    for required in (
        "Never modify source DWGs",
        "Detect labelled frames and office resources",
        "Address the intended AutoCAD release",
        "Publish bounded batches including 300 jobs",
        "schema-v2 manifest-and-output-bound receipt",
        "Install and remove a release safely",
        "Connect an MCP-capable model safely",
        "Ship as auditable open source",
        "local_demo_ready=true",
        "live_publish_proven=false",
        "licensed_live_pilot_ready=false",
        "public_release_ready=false",
        "R20.1",
        "R25.0",
        "schema-v7 two-version pilot/recovery document",
    ):
        assert required in audit


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

    assert routes == {("R20.1", "R20.1"), ("R25.0", "R25.0")}


def test_bundle_verifier_requires_exact_module_to_runtime_routes() -> None:
    verifier = (REPOSITORY_ROOT / "scripts" / "verify-bundle.ps1").read_text(
        encoding="utf-8"
    )
    smoke = (REPOSITORY_ROOT / "scripts" / "smoke-bundle-install.ps1").read_text(
        encoding="utf-8"
    )

    for required in (
        "exactly the 2016 and 2025 adapter routes",
        'Series = "R20.1"',
        'Series = "R25.0"',
        'AppType -cne ".Net"',
        'LoadReasons -cne "LoadOnAutoCADStartup"',
        'OS -cne "Win64"',
        'Platform -cne "AutoCAD"',
        "SeriesMin -cne $expectedRoute.Series",
        "SeriesMax -cne $expectedRoute.Series",
    ):
        assert required in verifier
    assert 'SeriesMax="R25.1"' in smoke
    assert "bundle_runtime_route_tamper_blocked" in smoke


def test_repository_contains_mit_license() -> None:
    license_text = (REPOSITORY_ROOT / "LICENSE").read_text(encoding="utf-8")

    assert "MIT License" in license_text
    assert "Demir Eren" in license_text


def test_source_distribution_excludes_all_local_virtual_environments() -> None:
    pyproject = (REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    gitignore = (REPOSITORY_ROOT / ".gitignore").read_text(encoding="utf-8")

    assert "[tool.hatch.build.targets.sdist]" in pyproject
    assert 'exclude = ["/.venv*"]' in pyproject
    assert ".venv-*/" in gitignore


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
    assert '"R25.1" =' not in script
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


def test_matching_sdk_bundle_build_isolated_from_stale_repository_outputs() -> None:
    builder = (REPOSITORY_ROOT / "scripts" / "build-bundle.ps1").read_text(
        encoding="utf-8"
    )

    for required in (
        "New-IsolatedBuildRoot",
        "Remove-IsolatedBuildRoot",
        "cadplot-matching-sdk-build-",
        '[ValidateSet("Release")]',
        "--no-incremental",
        "--artifacts-path $build2016Root",
        "--artifacts-path $build2025Root",
        '"bin\\CadPlotMcp.AutoCAD2016\\$configurationPivot"',
        '"bin\\CadPlotMcp.AutoCAD2025\\$configurationPivot"',
        "-p:UseSharedCompilation=false",
        "Isolated matching-SDK build did not produce a required DLL",
        "[System.IO.Directory]::Delete($resolved, $true)",
    ):
        assert required in builder
    assert "CadPlotMcp.AutoCAD2016\\bin\\$Configuration" not in builder
    assert "CadPlotMcp.AutoCAD2025\\bin\\$Configuration" not in builder
    assert "Remove-Item" not in builder


def test_shared_autocad_runtime_has_explicit_cross_version_nullable_context() -> None:
    runtime = (
        REPOSITORY_ROOT
        / "src"
        / "dotnet"
        / "CadPlotMcp.AutoCAD.Shared"
        / "AutoCadPublishRuntime.cs"
    ).read_text(encoding="utf-8")

    assert runtime.startswith("#nullable disable\n")


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
        "queue_protocol_rehearsal.queue_capacity -ne 7",
        "queue_protocol_rehearsal.deferred_results -ne 285",
        "queue_protocol_rehearsal.status_batches -ne 15",
        "queue_exact_retry_identity_preserved",
        "queue_status_identity_preserved",
        "publish_verified -ne 0",
        "orientation_mismatch_rejected",
        "manual_review_without_receipts",
        "evidence_digest",
        "SummaryPath",
        "uv build",
        "audit-release-artifacts.py",
        "smoke-wheel-install.py",
        "tunnel_preflight_target_probed -ne $true",
        "chatgpt_eval_plan_prepared -ne $true",
        "chatgpt_eval_case_count -ne 13",
        "wheel_install_smoke = [ordered]@{",
        "smoke-demo-kit.ps1",
        "smoke-bundle-install.ps1",
        "dotnet\\CadPlotMcp.sln",
        "probe-durable-queue.ps1",
        "durable_queue_recovery = $durableQueueEvidence",
        "probe-autocad-api.ps1",
        "audit-dependencies.py",
        "python_license_inventory",
        "unknown_count -ne 0",
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
        "wheel_install_smoke = $preflightSummary.wheel_install_smoke",
        "durable_queue_recovery = $preflightSummary.durable_queue_recovery",
        "Installed wheel smoke hash no longer matches",
        "chatgpt_eval_plan_prepared -ne $true",
        "chatgpt_eval_case_count -ne 13",
        "api_probe = $preflightSummary.api_probe",
        "unexpectedly contains API evidence",
        "target_drawings -ne 300",
        "publish_verified -ne 0",
        "orientation_mismatch_rejected -ne $true",
        "[System.IO.FileMode]::CreateNew",
        "ReportPath",
        "Readiness report target already exists",
        "Readiness report parent must not be a symlink or junction",
    ):
        assert required in script
    assert "Start-Process" not in script


def test_complete_release_builder_is_single_command_fail_closed_orchestration() -> None:
    script = (REPOSITORY_ROOT / "scripts" / "build-complete-release.ps1").read_text(
        encoding="utf-8"
    )

    for required in (
        "AutoCAD2016SdkDir",
        "AutoCAD2025SdkDir",
        "Complete release build requires a clean worktree",
        "Assert-SeparateRoots",
        "build never overwrites",
        "run-demo-rehearsal.ps1",
        "ReportPath = $ReadinessReport",
        "AuditDependencies = $true",
        "build-bundle.ps1",
        "verify-bundle-release.ps1",
        "build-release-kit.ps1",
        "verify-release-kit.ps1",
        "MatchingSdkBundleBuilt -ne $true",
        "matching_sdk_bundle_built = $true",
        "self_verification_passed = $true",
        "autocad_launched = $false",
        "live_publish_proven = $false",
        "licensed_live_pilot_ready = $false",
        "public_release_ready = $false",
    ):
        assert required in script
    assert "Remove-Item" not in script
    assert "Start-Process" not in script


def test_autodesk_sdk_guide_keeps_licensed_inputs_external() -> None:
    guide = (REPOSITORY_ROOT / "docs" / "autodesk-sdk-prerequisites.md").read_text(
        encoding="utf-8"
    )

    for required in (
        "R20.1",
        "R25.0",
        "AcMgd.dll",
        "AcDbMgd.dll",
        "AcCoreMgd.dll",
        "ObjectARX SDK `inc` directory",
        "does not redistribute Autodesk DLLs",
        "never downloads, installs, or accepts it",
        "build-complete-release.ps1",
        "autocad_launched=false",
        "live_publish_proven=false",
    ):
        assert required in guide


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
        "Assert-NoRedirectedAncestor",
        "verify-demo-kit.ps1",
        "verify-demo-archive.ps1",
        "Compress-Archive",
        '"demo-kit-build.json"',
        '"pazartesi-demo-tr.md"',
        '"secure-tunnel-handoff.md"',
        '"completion-audit.md"',
        "self_verification_passed = $true",
        "archive_verification_passed = $true",
        "archive_file_count = $archiveVerification.ArchiveFileCount",
        "machine_paths_included = $false",
        "company_assets_copied = $false",
        "autodesk_binaries_included = $false",
        "live_publish_proven = $false",
        "300-drawing synthetic batch evidence",
        "synthetic_batch_rehearsal = $batch",
        "api_probe = $publicApiProbe",
        "Get-PublicApiProbeEvidence",
        "invalid compile-only API evidence",
        "dependency_audit_ran",
        "python_license_inventory",
        "installed local-target probe evidence",
        "wheel_install_smoke = $wheelSmoke",
        "not a live AutoCAD plug-in bundle",
    ):
        assert required in script
    assert "Remove-Item" not in script
    assert "Start-Process" not in script


def test_demo_kit_verifier_is_exact_path_redacted_and_tamper_smoked() -> None:
    verifier = (REPOSITORY_ROOT / "scripts" / "verify-demo-kit.ps1").read_text(
        encoding="utf-8"
    )
    archive_verifier = (
        REPOSITORY_ROOT / "scripts" / "verify-demo-archive.ps1"
    ).read_text(encoding="utf-8")
    smoke = (REPOSITORY_ROOT / "scripts" / "smoke-demo-kit.ps1").read_text(
        encoding="utf-8"
    )
    workflow = (REPOSITORY_ROOT / ".github" / "workflows" / "tests.yml").read_text(
        encoding="utf-8"
    )

    for required in (
        "schema_version -ne 2",
        '"api_directory"',
        "Demo-kit file set is not exact",
        '"pazartesi-demo-tr.md"',
        '"secure-tunnel-handoff.md"',
        '"completion-audit.md"',
        "Demo-kit file hash mismatch",
        "MachinePathsIncluded = $false",
        "target_drawings -ne 300",
        "python_license_inventory",
        "dependency license inventory is incomplete",
        "installed local-target probe evidence",
        "tunnel_preflight_target_probed -ne $true",
        "queue_deferred_results -ne 285",
        "orientation_mismatch_rejected -ne $true",
        "live_publish_proven -ne $false",
    ):
        assert required in verifier
    for required in (
        "verify-demo-kit.ps1",
        "verify-demo-archive.ps1",
        "machine_paths_redacted = $true",
        "archive_verified = $true",
        "archive_outer_identity_tamper_blocked",
        "archive_hash_tamper_blocked",
        "archive_traversal_tamper_blocked",
        "dependency_license_tamper_blocked",
        "queue_backpressure_tamper_blocked",
        "orientation_evidence_tamper_blocked",
        "tunnel_target_probe_tamper_blocked",
        "wheel_tamper_blocked = $true",
        "Remove-Item -LiteralPath $resolvedRoot -Recurse -Force",
    ):
        assert required in smoke
    for required in (
        "demo-kit-build.json",
        "Demo delivery archive contains an unsafe entry name or size",
        "Demo delivery archive hash mismatch",
        "Demo delivery archive entry hash mismatch",
        "verify-demo-kit.ps1",
        "ZipFile]::OpenRead",
        "MachinePathsIncluded = $false",
        "LivePublishProven = $false",
    ):
        assert required in archive_verifier
    assert "run-local-preflight.ps1 -SkipSync -AuditDependencies" in workflow
    assert "Start-Process" not in verifier
    assert "Start-Process" not in archive_verifier
    assert "Expand-Archive" not in archive_verifier


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
        '"cadplot-collect-recovery"',
        '"cadplot-assemble-pilot"',
        '"cadplot-validate-pilot"',
        '"cadplot-tunnel-preflight"',
        '"cadplot-chatgpt-eval"',
        '"pilot_cli_commands": len(pilot_commands)',
        '"cadplot-acceptance"',
        '"acceptance_cli_commands": len(acceptance_commands)',
        '"tunnel_preflight_redacted": True',
        '"tunnel_preflight_target_probed": True',
        '"tunnel_preflight_tool_surface_sha256"',
        '"chatgpt_eval_plan_prepared": True',
        '"chatgpt_eval_case_count"',
        'or "runtime-secret-sentinel" in tunnel_output',
        '"inspector_worker_protocol": True',
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
        "[Parameter(Mandatory = $true)]",
        "verify-bundle-release.ps1",
        "AllowProtocolOnlyFixture",
        "Bundle source must be the exact CadPlotMcp.bundle child",
        "not bound to a verified matching-SDK release",
        "Assert-NoRedirectedAncestor",
        "Assert-SameBundleHashes",
        ".CadPlotMcp.bundle.installing-",
        "[System.IO.Directory]::Move",
        "Installed verified bundle atomically",
        "non-loadable staging directory was retained",
    ):
        assert required in installer
    assert r'artifacts\CadPlotMcp.bundle' not in installer
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
        "queueCapacity",
        "queuePending",
        "queueRunning",
        "queueAvailable",
        "GetTelemetry",
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
    for required in ("R20.1", "R25.0"):
        assert required in identity
    assert 'String.Equals(runtimeSeries, "R25.1"' not in identity
    for required in (
        '"build_commit"',
        '"plugin_sha256"',
        '"runtime_series"',
        '"runtimeSupported"',
        "validate_bundle_build_evidence",
        "Bundle archive entry hash mismatch",
        "running plug-in commit mismatch",
        "running plug-in binary mismatch",
        '"schema_version": 7',
        '"queue_authentication"',
        '"queueAuthentication"',
        '"template_assets"',
        '"published_pdf"',
        '"receipt_output_count"',
        '"receipt_outputs_sha256"',
        '"receipt_output_binding_verified"',
        '"visual_reference"',
        '"batch_recovery"',
        "build_batch_recovery_evidence",
        "exactly the approved batch",
        "source_sha256_after",
        "staged_sha256_after",
    ):
        assert required in pilot
    assert "--bundle-build-manifest" in assembler
    assert "--recovery-2016" in assembler
    assert "--recovery-2025" in assembler
    assert "--repository-commit" not in assembler
    assert "--reference-pdf" in assembler
    for visual_flag in (
        "--accept-orientation",
        "--accept-crop",
        "--accept-viewport-scale",
        "--accept-lineweights",
        "--accept-plot-style",
        "--accept-fonts",
        "--accept-title-block",
    ):
        assert visual_flag in assembler or "VISUAL_CHECKS" in assembler
    assert "--accept-visual-checks" not in assembler


def test_durable_queue_intent_is_fail_closed_and_release_evidenced() -> None:
    core = REPOSITORY_ROOT / "src" / "dotnet" / "CadPlotMcp.Core"
    journal = (core / "PublishQueueJournal.cs").read_text(encoding="utf-8")
    authentication = (core / "PublishQueueAuthentication.cs").read_text(encoding="utf-8")
    jobs = (core / "PublishJobs.cs").read_text(encoding="utf-8")
    worker = (core / "PublishWorker.cs").read_text(encoding="utf-8")
    protocol = (core / "Protocol.cs").read_text(encoding="utf-8")
    runtime = (
        REPOSITORY_ROOT
        / "src"
        / "dotnet"
        / "CadPlotMcp.AutoCAD.Shared"
        / "AutoCadPublishRuntime.cs"
    ).read_text(encoding="utf-8")
    probe = (REPOSITORY_ROOT / "scripts" / "probe-durable-queue.ps1").read_text(
        encoding="utf-8"
    )

    for required in (
        ".cadplot-queue-request.json",
        ".cadplot-queue-started.json",
        ".cadplot-queue-cancelled.json",
        "FileMode.CreateNew",
        "File.Move(temporaryPath, finalPath)",
        "MaxJobDirectories",
        "publish_queue_recovery_exceeds_capacity",
        'Error = "job_interrupted"',
        "ReadReceiptSnapshot",
        "RequirePlainFile",
        "PublishJobValidator.Validate",
        "PublishManifestReader.Validate",
        "publish_queue_request_authentication_failed",
        "publish_queue_started_authentication_failed",
        "publish_queue_cancelled_authentication_failed",
        "_authenticator.Verify",
    ):
        assert required in journal
    for required in (
        "invalid_job_id",
        "_journal.RecordPending(request)",
        "_journal.RecordStarted(request)",
        "_journal.RecordCancelled(pendingRequest)",
        "var blocked = _pending.Dequeue()",
        "RecoveredOnStartup",
        "InterruptedOnStartup",
        "CancelledOnStartup",
        "PublishQueueKeyStore.LoadOrCreate",
        "PublishQueueJournal.AuthenticationScheme",
    ):
        assert required in jobs
    assert "out startError" in worker
    for required in (
        "queueRecoveredOnStartup",
        "queueInterruptedOnStartup",
        "queueCancelledOnStartup",
        "queueAuthentication",
        "publishInitializationError",
    ):
        assert required in protocol
    assert 'publishInitializationError = "publish_queue_initialization_failed"' in runtime
    for test_name in (
        "PendingApprovedIntentRecoversAfterRestartWithExactIdentity",
        "InterruptedRunningIntentIsNeverAutomaticallyReplayed",
        "TerminalReceiptRestoresStatusAfterRestart",
        "TamperedPendingIntentDisablesRecovery",
        "ExistingReceiptCannotBeQueuedAsFreshWork",
        "ForgedUnsignedPendingIntentCannotAuthorizeRestart",
        "ForeignProtectedKeyCannotAuthorizeRestart",
        "TamperedStartedIntentDisablesRecovery",
        "PendingCancellationIsDurableAndNeverReplayed",
        "PendingCancellationIsExactAndIdempotent",
        "RunningJobCannotBeCancelled",
        "TamperedCancelledIntentDisablesRecovery",
        "AuthenticationKeyIsDpapiProtectedOutsideWorkspace",
        "AuthenticationKeyInsideWorkspaceIsRejected",
        "CorruptAuthenticationKeyDisablesInitialization",
    ):
        assert test_name in probe
    for required in (
        "CryptProtectData",
        "CryptUnprotectData",
        "HMACSHA256",
        "RandomNumberGenerator.Create()",
        "FileMode.CreateNew",
        "queue-auth-key-v1.bin",
        "The queue authentication key must be outside the trusted workspace",
    ):
        assert required in authentication
    assert "windows-dpapi-current-user+hmac-sha256-v1" in probe
    assert "net45_dpapi_runtime_proven" in probe
    assert 'net45_core_image_runtime = $net45Assembly.ImageRuntimeVersion' in probe
    assert "production-core-net45+net8-with-synthetic-files" in probe
    assert "exact_test_count -ne 15" in (
        REPOSITORY_ROOT / "scripts" / "run-local-preflight.ps1"
    ).read_text(encoding="utf-8")
    assert "Start-Process" not in probe

    for script_name in (
        "run-local-preflight.ps1",
        "run-demo-rehearsal.ps1",
        "build-demo-kit.ps1",
        "verify-demo-kit.ps1",
        "smoke-demo-kit.ps1",
        "build-release-kit.ps1",
        "verify-release-kit.ps1",
        "smoke-release-kit.ps1",
    ):
        script = (REPOSITORY_ROOT / "scripts" / script_name).read_text(encoding="utf-8")
        assert "durable_queue_recovery" in script
        assert "windows-dpapi-current-user+hmac-sha256-v1" in script
    for smoke_name in ("smoke-demo-kit.ps1", "smoke-release-kit.ps1"):
        smoke = (REPOSITORY_ROOT / "scripts" / smoke_name).read_text(encoding="utf-8")
        assert "durable_queue_tamper_blocked" in smoke


def test_publish_geometry_and_layout_scale_are_revalidated_before_plotting() -> None:
    runtime = (
        REPOSITORY_ROOT
        / "src"
        / "dotnet"
        / "CadPlotMcp.AutoCAD.Shared"
        / "AutoCadPublishRuntime.cs"
    ).read_text(encoding="utf-8")
    contract = (
        REPOSITORY_ROOT
        / "src"
        / "dotnet"
        / "CadPlotMcp.Core"
        / "PublishGeometryContract.cs"
    ).read_text(encoding="utf-8")
    planner = (REPOSITORY_ROOT / "src" / "cadplot_mcp" / "planner.py").read_text(
        encoding="utf-8"
    )
    audit = (REPOSITORY_ROOT / "src" / "cadplot_mcp" / "audit.py").read_text(
        encoding="utf-8"
    )

    for required in (
        "settings.UseStandardScale",
        "StdScaleType.StdScale1To1",
        "settings.CustomPrintScale",
        "page_setup_scale_not_one_to_one",
        "PublishGeometryContract.PaperDimensionsMatch",
    ):
        assert required in runtime
    for required in (
        "plot_geometry_rotation_mismatch",
        "plot_geometry_aspect_mismatch",
        "derived_scale_mismatch",
        "plot_scale_mismatch",
        "IsOneToOneCustomScale",
        "PaperDimensionsMatch",
    ):
        assert required in contract
    assert 'rotation = int(page.get("/Rotate", 0) or 0)' in audit
    assert "actual_mm = (width_points" in audit
    assert "actual_mm = sorted" not in audit
    assert '"scale_tolerance_ratio": config.scale_tolerance_ratio' in planner
    assert "has_verified_one_to_one_scale" in planner


def test_block_definition_frame_detection_is_bounded_read_only_and_fail_closed() -> None:
    detector = (
        REPOSITORY_ROOT / "src" / "cadplot_mcp" / "backends" / "autocad_com.py"
    ).read_text(encoding="utf-8")
    guide = (REPOSITORY_ROOT / "docs" / "frame-detection.md").read_text(
        encoding="utf-8"
    )

    for required in (
        "MAX_BLOCK_DEFINITION_DEPTH = 8",
        "MAX_BLOCK_DEFINITION_ENTITIES = 1_000",
        "MAX_BLOCK_DEFINITION_TEXTS = 100",
        "document, \"Blocks\"",
        "AcDbAttributeDefinition",
        "cyclic block definition",
        "xref definition is not inspected",
        "layout definition is not inspected",
        "bounded definition traversal ",
        "was incomplete",
        "confidence=0.85 if definition_label_used else 0.9",
    ):
        assert required in detector
    assert ".Explode(" not in detector
    for required in (
        "never calls `Explode`",
        "8 nested",
        "definitions, 1,000",
        "1,000 entities",
        "100 text values",
        "partial evidence",
    ):
        assert required in guide


def test_publish_outputs_are_staged_until_all_plots_and_dwg_discard_succeed() -> None:
    runtime = (
        REPOSITORY_ROOT
        / "src"
        / "dotnet"
        / "CadPlotMcp.AutoCAD.Shared"
        / "AutoCadPublishRuntime.cs"
    ).read_text(encoding="utf-8")
    transaction = (
        REPOSITORY_ROOT
        / "src"
        / "dotnet"
        / "CadPlotMcp.Core"
        / "PublishOutputTransaction.cs"
    ).read_text(encoding="utf-8")

    assert "outputTransaction.GetTemporaryPath(index)" in runtime
    assert "outputTransaction.Commit()" in runtime
    assert runtime.index("document.CloseAndDiscard();") < runtime.index(
        "outputTransaction.Commit()"
    )
    for required in (
        '".partial.pdf"',
        "File.Move(entry.TemporaryPath, entry.FinalPath)",
        '"temporary_output_missing"',
        '"temporary_output_empty"',
        '"output_commit_partial"',
        "TryRollbackPromotedOutputs",
        "FileSha256.Compute(entry.TemporaryPath)",
        "FileSha256.Compute(entry.FinalPath)",
        "File.Move(entry.FinalPath, entry.TemporaryPath)",
        "File.Delete(entry.TemporaryPath)",
    ):
        assert required in transaction


def test_success_receipts_bind_the_exact_published_pdf_set_cross_runtime() -> None:
    receipt = (
        REPOSITORY_ROOT
        / "src"
        / "dotnet"
        / "CadPlotMcp.Core"
        / "PublishReceipt.cs"
    ).read_text(encoding="utf-8")
    journal = (
        REPOSITORY_ROOT
        / "src"
        / "dotnet"
        / "CadPlotMcp.Core"
        / "PublishQueueJournal.cs"
    ).read_text(encoding="utf-8")
    audit = (REPOSITORY_ROOT / "src" / "cadplot_mcp" / "audit.py").read_text(
        encoding="utf-8"
    )
    decision = (
        REPOSITORY_ROOT
        / "docs"
        / "adr"
        / "0008-output-bound-publish-receipts.md"
    ).read_text(encoding="utf-8")

    for required in (
        'DataMember(Name = "output_count")',
        'DataMember(Name = "outputs_sha256")',
        'SchemaVersion = 2',
        '"cadplot-receipt-outputs-v1\\0"',
        "PublishReceiptOutputBinding.TryCompute",
        '"receipt_output_missing"',
        '"receipt_output_changed"',
        '"receipt_output_redirected"',
    ):
        assert required in receipt
    for required in (
        "receipt.SchemaVersion != 2",
        '"publish_queue_receipt_output_mismatch"',
        "PublishReceiptOutputBinding.TryCompute",
    ):
        assert required in journal
    for required in (
        'RECEIPT_OUTPUT_DOMAIN = b"cadplot-receipt-outputs-v1\\0"',
        "build_receipt_output_digest",
        '"receipt_output_binding_verified"',
        'raw.get("schema_version") != 2',
    ):
        assert required in audit
    for required in (
        "schema version 2",
        "cadplot-receipt-outputs-v1\\0",
        "signed 64-bit big-endian",
        "Schema-v1 receipts fail closed",
        "integrity binding, not a third-party digital signature",
    ):
        assert required in decision


def test_custom_pipe_name_is_symmetric_across_client_and_both_adapters() -> None:
    protocol = (
        REPOSITORY_ROOT / "src" / "dotnet" / "CadPlotMcp.Core" / "Protocol.cs"
    ).read_text(encoding="utf-8")
    host = (
        REPOSITORY_ROOT
        / "src"
        / "dotnet"
        / "CadPlotMcp.Core"
        / "NamedPipeCommandHost.cs"
    ).read_text(encoding="utf-8")
    runtime = (
        REPOSITORY_ROOT
        / "src"
        / "dotnet"
        / "CadPlotMcp.AutoCAD.Shared"
        / "AutoCadPublishRuntime.cs"
    ).read_text(encoding="utf-8")
    plugin_2016 = (
        REPOSITORY_ROOT
        / "src"
        / "dotnet"
        / "CadPlotMcp.AutoCAD2016"
        / "Plugin.cs"
    ).read_text(encoding="utf-8")
    plugin_2025 = (
        REPOSITORY_ROOT
        / "src"
        / "dotnet"
        / "CadPlotMcp.AutoCAD2025"
        / "Plugin.cs"
    ).read_text(encoding="utf-8")
    client = (REPOSITORY_ROOT / "src" / "cadplot_mcp" / "pipe_client.py").read_text(
        encoding="utf-8"
    )
    inspector = (
        REPOSITORY_ROOT
        / "src"
        / "cadplot_mcp"
        / "backends"
        / "autocad_com.py"
    ).read_text(encoding="utf-8")
    isolated_inspector = (
        REPOSITORY_ROOT
        / "src"
        / "cadplot_mcp"
        / "backends"
        / "isolated_autocad.py"
    ).read_text(encoding="utf-8")
    inspector_worker = (
        REPOSITORY_ROOT / "src" / "cadplot_mcp" / "inspector_worker.py"
    ).read_text(encoding="utf-8")
    environment = (
        REPOSITORY_ROOT / "src" / "cadplot_mcp" / "environment.py"
    ).read_text(encoding="utf-8")
    deployment = (REPOSITORY_ROOT / "docs" / "deployment-modes.md").read_text(
        encoding="utf-8"
    )
    decision = (
        REPOSITORY_ROOT
        / "docs"
        / "adr"
        / "0009-version-bound-local-autocad-routing.md"
    ).read_text(encoding="utf-8")

    assert '"^[A-Za-z0-9._-]{1,128}$"' in protocol
    assert "PipeProtocol.ResolvePipeName(pipeName)" in host
    assert "var firstPipe = CreatePipe()" in host
    assert "ListenLoop(firstPipe)" in host
    assert "pipe.Disconnect()" in host
    for adapter_source in (runtime, plugin_2016, plugin_2025):
        assert 'Environment.GetEnvironmentVariable("CADPLOT_PIPE_NAME")' in adapter_source
    assert 'os.environ.get("CADPLOT_PIPE_NAME", DEFAULT_PIPE_NAME)' in client
    for required in (
        'os.environ.get("CADPLOT_AUTOCAD_PROGID")',
        'client.GetActiveObject(self.progid)',
        "AUTOCAD_PROGID_PATTERN",
        "20\\.1",
        "25\\.0",
        "AutoCAD COM identity mismatch",
    ):
        assert required in inspector
    for required in ('"schema_version": 2', '"autocad_progid"'):
        assert required in isolated_inspector
    for required in ('value["schema_version"] != 2', 'value["autocad_progid"]'):
        assert required in inspector_worker
    for required in (
        "PROGID_RUNTIME_SERIES",
        'report["plugin"]["inspection_identity_matched"]',
        "AutoCAD COM/plug-in runtime mismatch",
    ):
        assert required in environment
    for required in (
        "cadplot-mcp-2016",
        "cadplot-mcp-2025",
        "AutoCAD.Application.20.1",
        "AutoCAD.Application.25.0",
        "validate_environment",
        "get_autocad_plugin_status",
    ):
        assert required in deployment
    for required in (
        "version-independent `GetObject",
        "CADPLOT_AUTOCAD_PROGID",
        "CADPLOT_PIPE_NAME",
        "inspection_identity_matched=false",
        "never `CreateObject`",
        "licensed acceptance gates",
    ):
        assert required in decision


def test_external_layout_templates_are_hash_bound_and_imported_only_from_job_copy() -> None:
    runtime = (
        REPOSITORY_ROOT
        / "src"
        / "dotnet"
        / "CadPlotMcp.AutoCAD.Shared"
        / "AutoCadPublishRuntime.cs"
    ).read_text(encoding="utf-8")
    manifest = (
        REPOSITORY_ROOT
        / "src"
        / "dotnet"
        / "CadPlotMcp.Core"
        / "PublishManifest.cs"
    ).read_text(encoding="utf-8")
    workspace = (REPOSITORY_ROOT / "src" / "cadplot_mcp" / "workspace.py").read_text(
        encoding="utf-8"
    )

    for required in (
        "current_template = fingerprint_template(",
        "shutil.copy2(template_source, staged_template)",
        '"template_assets": template_assets',
        '"template_asset_id"',
    ):
        assert required in workspace
    for required in (
        'DataMember(Name = "staged_template")',
        '"template_asset_outside_job"',
        '"template_asset_changed"',
        "FileSha256.Compute(path)",
    ):
        assert required in manifest
    for required in (
        "ImportTemplateLayouts(database, manifest.TemplateAssets)",
        "FileSha256.Compute(asset.StagedTemplate)",
        "external.ReadDwgFile(",
        "external.WblockCloneObjects(",
        "DuplicateRecordCloning.MangleName",
        "importedLayout.CopyFrom(externalLayout)",
    ):
        assert required in runtime
    assert runtime.index("ImportTemplateLayouts(database") < runtime.index(
        "PreflightLayouts(database"
    )


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
        "collect-batch-recovery.py",
        "assemble-pilot-evidence.py",
        "validate-pilot-evidence.py",
        "release-acceptance.py",
        "new-local-pilot.ps1",
        "pilot-evidence.md",
        "release-acceptance.md",
        "autodesk-sdk-prerequisites.md",
        "completion-audit.md",
        "CHANGELOG.md",
        "self_verification_passed = $verification.Passed",
        "Readiness report has invalid compile-only API evidence",
        "current lock-bound dependency-audit evidence",
        "python_license_inventory",
        "valid installed local-target probe evidence",
        "wheel_install_smoke = $wheelSmoke",
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
        "python_license_inventory",
        "dependency license inventory is incomplete",
        "installed local-target probe evidence",
        "tunnel_preflight_target_probed -ne $true",
        '"scripts/verify-release-kit.ps1"',
        '"scripts/new-local-pilot.ps1"',
        '"scripts/collect-pilot-run.py"',
        '"scripts/collect-batch-recovery.py"',
        '"scripts/assemble-pilot-evidence.py"',
        '"scripts/validate-pilot-evidence.py"',
        '"scripts/release-acceptance.py"',
        '"docs/pilot-evidence.md"',
        '"docs/release-acceptance.md"',
        '"docs/autodesk-sdk-prerequisites.md"',
        '"docs/completion-audit.md"',
        '"CHANGELOG.md"',
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
    assert "install-python.ps1" in guide
    assert "verify-python-install.ps1" in guide
    assert "mandatory hashes" in guide
    assert "no source checkout is required" in guide
    assert "cadplot-collect-pilot.cmd\" --help" in guide
    assert "cadplot-collect-recovery.cmd\" --help" in guide
    assert "cadplot-assemble-pilot.cmd\" --help" in guide
    assert "cadplot-validate-pilot.cmd\" --help" in guide
    assert "cadplot-acceptance.cmd\" --help" in guide
    assert "cadplot-tunnel-preflight.cmd\" --help" in guide
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
        "dependency_license_tamper_blocked",
        "tunnel_target_probe_tamper_blocked",
        "AllowProtocolOnlyFixture",
        "autocad_launched = $false",
        "live_publish_proven = $false",
        "synthetic_batch_rehearsal",
        "orientation_mismatch_rejected = $true",
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
    installer = (REPOSITORY_ROOT / "scripts" / "install-bundle.ps1").read_text(encoding="utf-8")

    assert "SupportsShouldProcess = $true" in script
    assert "C2E79B66-6076-40D4-AE45-E725A644B288" in script
    assert "Refusing to use a drive root" in script
    assert "ReparsePoint" in script
    assert "ShouldProcess($destinationBundle" in script
    assert "verify-bundle.ps1" in script
    assert "Get-BundleHashSignature" in script
    assert "Move-Item -LiteralPath $destinationBundle -Destination $quarantine" in script
    assert "^\\.cadplot-bundle-removing-[0-9a-f]{32}$" in script
    assert "[System.IO.Directory]::Delete($deletePath, $true)" in script
    assert "Remove-Item -LiteralPath $destinationBundle -Recurse" not in script
    for bundle_mutator in (installer, script):
        assert 'Get-Process -Name "acad"' in bundle_mutator
        assert "Close every AutoCAD process" in bundle_mutator
        assert "Stop-Process" not in bundle_mutator


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
        "protocol_only_install_rejected_without_test_consent = $protocolOnlyInstallRejected",
        "AllowProtocolOnlyFixture",
        "bundle_release_verified = $true",
        "bundle_release_archive_tamper_blocked = $archiveTamperBlocked",
        "release_kit_self_verification_passed",
        "release_kit_tunnel_target_probe_tamper_blocked",
        "copied_hashes_verified = $true",
        "bundle_install_autocad_process_blocked = $installProcessGuardBlocked",
        "existing_install_blocked = $overwriteBlocked",
        "unexpected_file_uninstall_blocked = $unexpectedFileBlocked",
        "bundle_uninstall_what_if_safe = $true",
        "bundle_uninstall_drive_root_blocked = $driveRootBlocked",
        "bundle_uninstall_autocad_process_blocked = $uninstallProcessGuardBlocked",
        "bundle_uninstall_quarantine_removed = $true",
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
    assert "Local targets/preflight implemented" in deployment
    assert "cadplot-mcp-http --port 8765" in deployment
    assert "does not yet implement that managed HTTPS proxy" in deployment
    assert "named pipe" in deployment
    assert "publish_verified=true" in deployment
    assert "The DWG stays" in architecture
    assert "Not implemented or claimed" in architecture
    assert "loopback-only Streamable HTTP" in architecture
    assert "cadplot-tunnel-preflight" in architecture
    assert "chatgpt-evaluation.md" in deployment
    assert "ChatGPT tool-selection evaluation" in architecture


def test_secure_tunnel_handoff_is_secret_free_and_keeps_live_gates_external() -> None:
    guide = (REPOSITORY_ROOT / "docs" / "secure-tunnel-handoff.md").read_text(
        encoding="utf-8"
    )
    preflight = (REPOSITORY_ROOT / "src" / "cadplot_mcp" / "tunnel_preflight.py").read_text(
        encoding="utf-8"
    )

    for required in (
        "cadplot-tunnel-preflight --transport stdio --probe-target",
        "local_target_proven=true",
        "tool_surface_sha256",
        "--output <new-file.json>",
        "chatgpt-evaluation.md",
        "tunnel-client doctor --profile cadplot-local --explain",
        "--mcp-command \"cadplot-mcp\"",
        "Platform tunnel creation",
        "licensed AutoCAD 2016 and 2025 pilot evidence",
        "publish_verified=true",
        "OpenAI Secure MCP Tunnel",
    ):
        assert required in guide
    for required in (
        '"secrets_included": False',
        '"machine_paths_included": False',
        '"network_check_performed": False',
        '"admin_permission_check_performed": False',
        '"autocad_launched": False',
        '"live_tunnel_proven": False',
        '"live_publish_proven": False',
        '"--output"',
    ):
        assert required in preflight


def test_release_python_installer_is_locked_no_overwrite_and_verified() -> None:
    installer = (REPOSITORY_ROOT / "scripts" / "install-python.ps1").read_text(
        encoding="utf-8"
    )
    verifier = (REPOSITORY_ROOT / "scripts" / "verify-python-install.ps1").read_text(
        encoding="utf-8"
    )
    uninstaller = (REPOSITORY_ROOT / "scripts" / "uninstall-python.ps1").read_text(
        encoding="utf-8"
    )
    kit_builder = (REPOSITORY_ROOT / "scripts" / "build-release-kit.ps1").read_text(
        encoding="utf-8"
    )
    kit_verifier = (REPOSITORY_ROOT / "scripts" / "verify-release-kit.ps1").read_text(
        encoding="utf-8"
    )
    bundle_smoke = (REPOSITORY_ROOT / "scripts" / "smoke-bundle-install.ps1").read_text(
        encoding="utf-8"
    )
    release_installer = (REPOSITORY_ROOT / "scripts" / "install-release-kit.ps1").read_text(
        encoding="utf-8"
    )
    install_verifier = (
        REPOSITORY_ROOT / "scripts" / "verify-release-install.ps1"
    ).read_text(encoding="utf-8")
    release_uninstaller = (
        REPOSITORY_ROOT / "scripts" / "uninstall-release-kit.ps1"
    ).read_text(encoding="utf-8")

    for required in (
        "SupportsShouldProcess = $true",
        '"--frozen"',
        '"--require-hashes"',
        '"--no-deps"',
        "installer never overwrites",
        "Move-Item -LiteralPath $staging -Destination $target",
        "verify-python-install.ps1",
        "source_tree_imported = $false",
        "live_publish_proven = $false",
    ):
        assert required in installer
    assert "Remove-Item -LiteralPath $staging -Recurse" not in installer
    for required in (
        "FileAttributes]::ReparsePoint",
        "Python installation evidence hash mismatch",
        "Installed package inventory no longer matches",
        "cadplot-doctor.cmd",
        "cadplot-mcp-http.cmd",
        "cadplot-tunnel-preflight.cmd",
        "cadplot-chatgpt-eval.cmd",
        "PYTHONDONTWRITEBYTECODE",
        "[System.IO.File]::Delete($inventoryScript)",
        "[Environment]::SetEnvironmentVariable",
    ):
        assert required in verifier
    for required in (
        "SupportsShouldProcess = $true",
        "verify-python-install.ps1",
        "manifest changed during verification",
        "Move-Item -LiteralPath $root -Destination $quarantine",
        "^\\.cadplot-python-removing-[0-9a-f]{32}$",
        "[System.IO.Directory]::Delete($deletePath, $true)",
        "LivePublishProven = $false",
    ):
        assert required in uninstaller
    assert "Remove-Item -LiteralPath $root -Recurse" not in uninstaller
    for name in (
        "install-python.ps1",
        "verify-python-install.ps1",
        "uninstall-python.ps1",
        "install-release-kit.ps1",
        "verify-release-install.ps1",
        "uninstall-release-kit.ps1",
        "loopback-http.md",
        "secure-tunnel-handoff.md",
        "chatgpt-evaluation.md",
    ):
        assert name in kit_builder
        assert name in kit_verifier
    for required in (
        "SupportsShouldProcess = $true",
        "verify-release-kit.ps1",
        "Test-PathsOverlap",
        "must not be a drive or share root",
        "Assert-MatchingBundleHashes",
        "reuse_verified_structure",
        "reuse_verified",
        "Keep the AutoCAD-loadable bundle last",
        "Exercise every component installer's discoverable checks before the first mutation",
        "AutoCADLaunched = $false",
        "PublishEnabled = $false",
        "LivePublishProven = $false",
        'Get-Process -Name "acad"',
        "Assert-InstallReceipt",
        "[System.IO.FileMode]::CreateNew",
        "install-receipts",
        "release_kit_manifest_sha256",
        "payload_sha256",
        "Get-JsonSha256",
        "autocad_running_at_install = $false",
        "verify-release-install.ps1",
        "InstallVerified = $true",
    ):
        assert required in release_installer
    for required in (
        "verify-release-kit.ps1",
        "verify-bundle.ps1",
        "verify-python-install.ps1",
        "CadPlot install receipt payload digest is invalid",
        "Assert-RecordedAbsolutePath",
        "Assert-NoRedirectedAncestor",
        "Assert-JsonFalse",
        "Test-PathsOverlap",
        "must not be a drive or share root",
        "exact commit-bound pilot evidence path",
        "AllowRemovedComponents",
        "InstallationComplete",
        "BundlePresent",
        "PythonPresent",
        'Join-Path $kitRoot "autocad\\CadPlotMcp.bundle"',
        "ConfigChangedSinceInstall",
        "AutoCADLaunched = $false",
        "PublishEnabled = $false",
        "LivePublishProven = $false",
    ):
        assert required in install_verifier
    for forbidden in (
        "Start-Process",
        "Get-Process",
        "WriteAllText",
        "WriteAllBytes",
        "Copy-Item",
        "Move-Item",
        "Remove-Item",
        "New-Item",
    ):
        assert forbidden not in install_verifier
    for required in (
        "SupportsShouldProcess = $true",
        'Get-Process -Name "acad"',
        "verify-release-install.ps1",
        "AllowRemovedComponents = $true",
        "Exercise every required child uninstaller before the first mutation",
        "uninstall-bundle.ps1",
        "uninstall-python.ps1",
        'BundleAction = $bundleAction',
        'PythonAction = $pythonAction',
        'PilotAction = "preserve"',
        'ReceiptAction = "preserve"',
        "BundleRemovedThisRun",
        "PythonRemovedThisRun",
        "PilotPreserved = $true",
        "ReceiptPreserved = $true",
        "AutoCADLaunched = $false",
        "PublishEnabled = $false",
        "LivePublishProven = $false",
    ):
        assert required in release_uninstaller
    assert "Stop-Process" not in release_uninstaller
    assert "Remove-Item" not in release_uninstaller
    for field in (
        "python_install_what_if_safe",
        "python_install_locked_dependencies",
        "python_install_overwrite_blocked",
        "python_install_tamper_blocked",
        "python_uninstall_what_if_safe",
        "python_uninstall_verified",
        "release_install_what_if_safe",
        "release_install_tool_preflight_blocked",
        "release_install_overlap_blocked",
        "release_install_completed",
        "release_install_resume_verified",
        "release_install_bundle_last",
        "release_install_receipt_verified",
        "release_install_receipt_tamper_blocked",
        "release_install_receipt_independent_verified",
        "release_install_receipt_independent_tamper_blocked",
        "release_install_config_change_reported",
        "release_uninstall_autocad_process_blocked",
        "release_uninstall_what_if_safe",
        "release_uninstall_partial_resume_verified",
        "release_uninstall_completed",
        "release_uninstall_idempotent",
        "release_uninstall_pilot_preserved",
        "release_uninstall_receipt_preserved",
    ):
        assert field in bundle_smoke


def test_server_routes_drawing_inspection_through_bounded_helper() -> None:
    server = (REPOSITORY_ROOT / "src" / "cadplot_mcp" / "server.py").read_text(
        encoding="utf-8"
    )
    isolation = (REPOSITORY_ROOT / "docs" / "inspection-isolation.md").read_text(
        encoding="utf-8"
    )

    assert "IsolatedAutoCADInspector" in server
    assert "AutoCADComInspector" not in server
    assert "inspection_timeout_seconds" in server
    assert "standard input" in isolation
    assert "never kills AutoCAD" in isolation
    assert "licensed-pilot" in isolation


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
    dependabot = yaml.safe_load(
        (REPOSITORY_ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")
    )
    preflight = (REPOSITORY_ROOT / "scripts" / "run-local-preflight.ps1").read_text(
        encoding="utf-8"
    )

    assert "permissions:\n  contents: read" in workflow
    assert "timeout-minutes: 20" in workflow
    assert "cancel-in-progress: true" in workflow
    assert 'python-version: ["3.11", "3.12", "3.13"]' in workflow
    assert "fail-fast: false" in workflow
    assert "integrated-preflight:" in workflow
    assert "uv sync --frozen" in workflow
    assert "run-local-preflight.ps1 -SkipSync -AuditDependencies" in workflow
    assert "persist-credentials: false" in workflow
    action_pins = re.findall(r"uses:\s+[^\s@]+@([0-9a-f]{40})", workflow)
    assert action_pins == [
        "11d5960a326750d5838078e36cf38b85af677262",
        "d0d8abe699bfb85fec6de9f7adb5ae17292296ff",
        "a26af69be951a213d495a4c3e4e4022e16d87065",
        "11d5960a326750d5838078e36cf38b85af677262",
        "d0d8abe699bfb85fec6de9f7adb5ae17292296ff",
        "a26af69be951a213d495a4c3e4e4022e16d87065",
        "67a3573c9a986a3f9c594539f4ab511d57bb3ce9",
    ]
    assert {item["package-ecosystem"] for item in dependabot["updates"]} == {
        "uv",
        "nuget",
        "github-actions",
    }
    for required in (
        "scripts\\smoke-mcp-stdio.py",
        "scripts\\run-synthetic-demo.py",
        "scripts\\audit-source-tree.py",
        "scripts\\smoke-wheel-install.py",
        '"smoke-demo-kit.ps1"',
    ):
        assert required in preflight
