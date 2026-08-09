[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$BundleReleaseRoot
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$resolvedBundleRelease = [System.IO.Path]::GetFullPath($BundleReleaseRoot)
$bundleEvidence = & (Join-Path $PSScriptRoot "verify-bundle-release.ps1") `
    -ReleaseRoot $resolvedBundleRelease `
    -PassThru `
    -AllowProtocolOnlyFixture
if (
    $bundleEvidence.MatchingSdkBundleBuilt -ne $false -or
    $bundleEvidence.ProtocolOnlyFixture -ne $true
) {
    throw "Release-kit smoke requires a protocol-only bundle fixture."
}

$smokeRoot = Join-Path (
    [System.IO.Path]::GetTempPath()
) ("cadplot-release-kit-smoke-{0}" -f [Guid]::NewGuid().ToString("N"))
$resolvedSmokeRoot = [System.IO.Path]::GetFullPath($smokeRoot)
$resolvedTempRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath()).TrimEnd('\') + '\'
if (-not $resolvedSmokeRoot.StartsWith($resolvedTempRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Release-kit smoke root escaped the system temporary directory."
}
$installRoot = Join-Path (
    [System.IO.Path]::GetTempPath()
) ("cadplot-python-install-smoke-{0}" -f [Guid]::NewGuid().ToString("N"))
$orchestratedRoot = Join-Path (
    [System.IO.Path]::GetTempPath()
) ("cadplot-orchestrated-install-smoke-{0}" -f [Guid]::NewGuid().ToString("N"))
$orchestratedPythonRoot = Join-Path $orchestratedRoot "python"
$orchestratedBundleRoot = Join-Path $orchestratedRoot "plugins"
$orchestratedPilotRoot = Join-Path $orchestratedRoot "pilot"
$requirementsAuditPath = Join-Path (
    [System.IO.Path]::GetTempPath()
) ("cadplot-requirements-audit-{0}.txt" -f [Guid]::NewGuid().ToString("N"))
$dependencyAuditInputPath = Join-Path (
    [System.IO.Path]::GetTempPath()
) ("cadplot-dependency-audit-{0}.json" -f [Guid]::NewGuid().ToString("N"))

function Write-SmokeJson {
    param([string]$Path, $Value)
    [System.IO.File]::WriteAllText(
        $Path,
        ($Value | ConvertTo-Json -Depth 7),
        [System.Text.UTF8Encoding]::new($false)
    )
}

function Invoke-SmokeNativeQuiet {
    param([string]$FilePath, [string[]]$Arguments, [string]$FailureMessage)
    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $FilePath @Arguments 2>&1 | Out-Null
        $exitCode = $LASTEXITCODE
    }
    finally { $ErrorActionPreference = $previousPreference }
    if ($exitCode -ne 0) { throw "$FailureMessage (exit $exitCode)" }
}

function Remove-SmokeDirectory {
    param([Parameter(Mandatory = $true)][string]$Path)

    $resolved = [System.IO.Path]::GetFullPath($Path).TrimEnd('\')
    if (
        -not $resolved.StartsWith($resolvedTempRoot, [System.StringComparison]::OrdinalIgnoreCase) -or
        $resolved -ieq $resolvedTempRoot.TrimEnd('\')
    ) {
        throw "Smoke cleanup target escaped the system temporary directory: $resolved"
    }
    if (Test-Path -LiteralPath $resolved) {
        $extended = if ($resolved.StartsWith('\\')) {
            "\\?\UNC\$($resolved.Substring(2))"
        }
        else { "\\?\$resolved" }
        [System.IO.Directory]::Delete($extended, $true)
    }
}

try {
    $kitRoot = Join-Path $resolvedSmokeRoot "CadPlotMcp.release"
    $null = New-Item -ItemType Directory -Path $kitRoot
    foreach ($directory in @("autocad", "python", "source", "scripts", "docs", "config")) {
        $null = New-Item -ItemType Directory -Path (Join-Path $kitRoot $directory)
    }
    Copy-Item -LiteralPath (Join-Path $resolvedBundleRelease "CadPlotMcp.bundle") `
        -Destination (Join-Path $kitRoot "autocad\CadPlotMcp.bundle") -Recurse
    Copy-Item -LiteralPath (Join-Path $resolvedBundleRelease "CadPlotMcp.bundle.zip") `
        -Destination (Join-Path $kitRoot "autocad\CadPlotMcp.bundle.zip")
    Copy-Item -LiteralPath (Join-Path $resolvedBundleRelease "bundle-build.json") `
        -Destination (Join-Path $kitRoot "autocad\bundle-build.json")

    $wheels = @(Get-ChildItem -LiteralPath (Join-Path $repoRoot "dist") -File `
        -Filter "cadplot_mcp-*-py3-none-any.whl")
    if ($wheels.Count -ne 1) { throw "Release-kit smoke expected exactly one built wheel." }
    Copy-Item -LiteralPath $wheels[0].FullName -Destination (Join-Path $kitRoot "python")
    Copy-Item -LiteralPath (Join-Path $repoRoot "pyproject.toml") -Destination (Join-Path $kitRoot "python")
    Copy-Item -LiteralPath (Join-Path $repoRoot "uv.lock") -Destination (Join-Path $kitRoot "python")
    $sourceName = "cadplot-mcp-source-0000000.zip"
    $sourcePath = Join-Path $kitRoot "source\$sourceName"
    & git -C $repoRoot archive --format=zip --output=$sourcePath HEAD
    if ($LASTEXITCODE -ne 0) { throw "Release-kit fixture source archive failed." }

    foreach ($scriptName in @(
        "install-bundle.ps1", "uninstall-bundle.ps1", "verify-bundle.ps1",
        "verify-bundle-release.ps1", "verify-release-kit.ps1",
        "install-python.ps1", "verify-python-install.ps1", "uninstall-python.ps1",
        "install-release-kit.ps1", "verify-release-install.ps1", "uninstall-release-kit.ps1",
        "check-autocad-api-series.ps1", "new-local-pilot.ps1",
        "collect-pilot-run.py", "assemble-pilot-evidence.py",
        "validate-pilot-evidence.py", "release-acceptance.py"
    )) {
        Copy-Item -LiteralPath (Join-Path $PSScriptRoot $scriptName) `
            -Destination (Join-Path $kitRoot "scripts\$scriptName")
    }
    foreach ($docName in @(
        "monday-pilot.md", "pazartesi-demo-tr.md", "release-checklist.md",
        "release-kit-install.md", "pilot-evidence.md", "deployment-modes.md",
        "release-acceptance.md", "chatgpt-connection.md", "loopback-http.md",
        "secure-tunnel-handoff.md", "chatgpt-evaluation.md",
        "autodesk-sdk-prerequisites.md", "software-bill-of-materials.md",
        "completion-audit.md"
    )) {
        Copy-Item -LiteralPath (Join-Path $repoRoot "docs\$docName") `
            -Destination (Join-Path $kitRoot "docs\$docName")
    }
    Copy-Item -LiteralPath (Join-Path $repoRoot "examples\config.inventory.example.yaml") `
        -Destination (Join-Path $kitRoot "config\config.inventory.example.yaml")
    foreach ($fileName in @(
        "LICENSE", "README.md", "README.tr.md", "CHANGELOG.md", "THIRD_PARTY_NOTICES.md"
    )) {
        Copy-Item -LiteralPath (Join-Path $repoRoot $fileName) -Destination $kitRoot
    }

    $wheelName = $wheels[0].Name
    $wheelPath = Join-Path $kitRoot "python\$wheelName"
    Invoke-SmokeNativeQuiet -FilePath "uv" -Arguments @(
        "export", "--frozen", "--no-dev", "--no-emit-project", "--no-header",
        "--project", (Join-Path $kitRoot "python"),
        "--output-file", $requirementsAuditPath
    ) -FailureMessage "Release-kit smoke dependency export failed."
    $requirementsAuditHash = (
        Get-FileHash -LiteralPath $requirementsAuditPath -Algorithm SHA256
    ).Hash.ToLowerInvariant()
    $dependencyAudit = [ordered]@{
        schema_version = 1
        generated_utc = [DateTime]::UtcNow.ToString("o")
        passed = $true
        lock = [ordered]@{
            file = "uv.lock"
            sha256 = (Get-FileHash -LiteralPath (Join-Path $kitRoot "python\uv.lock") -Algorithm SHA256).Hash.ToLowerInvariant()
            requirements_sha256 = $requirementsAuditHash
        }
        python = [ordered]@{
            tool = "pip-audit fixture"
            package_count = 1
            vulnerability_count = 0
            database = "fixture"
        }
        python_license_inventory = [ordered]@{
            package_count = 1
            unknown_count = 0
            packages = @(
                [ordered]@{ name = "fixture"; version = "1.0"; license = "MIT" }
            )
        }
        dotnet = [ordered]@{
            tool = "dotnet fixture"
            project_count = 4
            vulnerability_count = 0
            source_count = 1
            database = "fixture"
        }
        network_database_check = $true
        autocad_launched = $false
        live_publish_proven = $false
    }
    $wheelSmoke = [ordered]@{
        passed = $true
        version = $bundleEvidence.PackageVersion
        wheel_sha256 = (Get-FileHash -LiteralPath $wheelPath -Algorithm SHA256).Hash.ToLowerInvariant()
        protocol_version = "2025-11-25"
        tool_count = 20
        http_transport_tool_count = 20
        http_transport_loopback_only = $true
        http_transport_header_guards = $true
        tunnel_preflight_redacted = $true
        tunnel_preflight_target_probed = $true
        tunnel_preflight_tool_surface_sha256 = "a" * 64
        chatgpt_eval_plan_prepared = $true
        chatgpt_eval_case_count = 13
        sbom_cli_verified = $true
        isolated_install = $true
        locked_dependencies = $true
        dependency_hashes_required = $true
        autocad_launched = $false
        live_tunnel_proven = $false
        live_publish_proven = $false
    }
    $durableQueue = [ordered]@{
        passed = $true
        exact_test_count = 15
        pending_intent_recovered = $true
        exact_request_identity_preserved = $true
        interrupted_job_not_replayed = $true
        terminal_receipt_status_recovered = $true
        tampered_intent_blocked = $true
        completed_job_requeue_blocked = $true
        authentication_scheme = "windows-dpapi-current-user+hmac-sha256-v1"
        signed_intent_required = $true
        foreign_key_intent_blocked = $true
        started_marker_authentication_required = $true
        pending_cancellation_durable = $true
        cancelled_job_not_replayed = $true
        cancelled_marker_authentication_required = $true
        running_job_not_cancelled = $true
        protected_key_outside_workspace = $true
        workspace_key_rejected = $true
        corrupt_key_blocked = $true
        net45_dpapi_runtime_proven = $true
        net45_core_image_runtime = "v4.0.30319"
        autocad_launched = $false
        live_publish_proven = $false
        evidence_scope = "production-core-net45+net8-with-synthetic-files"
    }
    Write-SmokeJson -Path $dependencyAuditInputPath -Value $dependencyAudit
    $sbomPath = Join-Path $kitRoot "cadplot-mcp.cdx.json"
    Invoke-SmokeNativeQuiet -FilePath "uv" -Arguments @(
        "run", "cadplot-sbom", "generate",
        "--dependency-audit", $dependencyAuditInputPath,
        "--commit", $bundleEvidence.ExactCommit,
        "--version", $bundleEvidence.PackageVersion,
        "--artifact", "python-wheel=$wheelPath",
        "--artifact", "source-archive=$sourcePath",
        "--artifact", "autocad-bundle=$(Join-Path $kitRoot 'autocad\CadPlotMcp.bundle.zip')",
        "--artifact", "autocad-2016-adapter=$(Join-Path $kitRoot 'autocad\CadPlotMcp.bundle\Contents\Windows\2016\CadPlotMcp.AutoCAD2016.dll')",
        "--artifact", "autocad-2016-core=$(Join-Path $kitRoot 'autocad\CadPlotMcp.bundle\Contents\Windows\2016\CadPlotMcp.Core.dll')",
        "--artifact", "autocad-2025-adapter=$(Join-Path $kitRoot 'autocad\CadPlotMcp.bundle\Contents\Windows\2025\CadPlotMcp.AutoCAD2025.dll')",
        "--artifact", "autocad-2025-core=$(Join-Path $kitRoot 'autocad\CadPlotMcp.bundle\Contents\Windows\2025\CadPlotMcp.Core.dll')",
        "--output", $sbomPath
    ) -FailureMessage "Release-kit smoke SBOM generation failed."
    $sbomManifestEvidence = [ordered]@{
        file = "cadplot-mcp.cdx.json"
        sha256 = (Get-FileHash -LiteralPath $sbomPath -Algorithm SHA256).Hash.ToLowerInvariant()
        spec_version = "1.7"
        component_count = 8
        runtime_dependency_count = 1
        artifact_count = 7
    }
    $files = @(Get-ChildItem -LiteralPath $kitRoot -File -Recurse | Sort-Object FullName | ForEach-Object {
        [ordered]@{
            path = $_.FullName.Substring($kitRoot.Length + 1).Replace('\', '/')
            sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        }
    })
    $manifestPath = Join-Path $kitRoot "release-kit.json"
    Write-SmokeJson -Path $manifestPath -Value ([ordered]@{
        schema_version = 1
        exact_commit = $bundleEvidence.ExactCommit
        package_version = $bundleEvidence.PackageVersion
        created_utc = [DateTime]::UtcNow.ToString("o")
        wheel = [ordered]@{
            file = "python/$wheelName"
            sha256 = (Get-FileHash -LiteralPath $wheelPath -Algorithm SHA256).Hash.ToLowerInvariant()
        }
        source_archive = "source/$sourceName"
        sbom = $sbomManifestEvidence
        files = $files
        dependency_audit_ran = $true
        dependency_audit = $dependencyAudit
        wheel_install_smoke = $wheelSmoke
        durable_queue_recovery = $durableQueue
        synthetic_batch_rehearsal = [ordered]@{
            target_drawings = 300
            planning_pages = 15
            ready = 300
            staging_batches = 15
            staged = 300
            queue_capacity = 7
            queue_waves = 45
            queue_approvals = 300
            queue_simulated_acceptances = 300
            queue_deferred_results = 285
            queue_pipe_attempts = 330
            queue_exact_retry_identity_preserved = $true
            queue_status_batches = 15
            queue_status_items = 300
            queue_status_pending = 300
            queue_status_identity_preserved = $true
            queue_plugin_contacted = $false
            restart_pages_before_outputs = 6
            restart_pages_after_outputs = 6
            outputs_complete = 300
            execution_verified = 0
            publish_verified = 0
            manual_review_without_receipts = 300
            source_unchanged = $true
            evidence_digest = "sha256:$('c' * 64)"
            synthetic = $true
        }
        matching_sdk_bundle_built = $false
        protocol_only_fixture = $true
        local_demo_ready = $true
        licensed_live_pilot_ready = $false
        public_release_ready = $false
        company_assets_copied = $false
        autodesk_binaries_included = $false
        autocad_launched = $false
        live_publish_proven = $false
    })
    $archivePath = Join-Path $resolvedSmokeRoot "CadPlotMcp.release.zip"
    Compress-Archive -LiteralPath $kitRoot -DestinationPath $archivePath -CompressionLevel Optimal
    $outerPath = Join-Path $resolvedSmokeRoot "release-kit-build.json"
    Write-SmokeJson -Path $outerPath -Value ([ordered]@{
        schema_version = 1
        exact_commit = $bundleEvidence.ExactCommit
        package_version = $bundleEvidence.PackageVersion
        created_utc = [DateTime]::UtcNow.ToString("o")
        kit_directory = "CadPlotMcp.release"
        kit_archive = "CadPlotMcp.release.zip"
        kit_manifest_sha256 = (Get-FileHash -LiteralPath $manifestPath -Algorithm SHA256).Hash.ToLowerInvariant()
        kit_archive_sha256 = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant()
        dependency_audit_ran = $true
        dependency_audit = $dependencyAudit
        sbom = $sbomManifestEvidence
        wheel_install_smoke = $wheelSmoke
        durable_queue_recovery = $durableQueue
        matching_sdk_bundle_built = $false
        protocol_only_fixture = $true
        local_demo_ready = $true
        licensed_live_pilot_ready = $false
        public_release_ready = $false
        company_assets_copied = $false
        autodesk_binaries_included = $false
        autocad_launched = $false
        live_publish_proven = $false
    })

    $verifier = Join-Path $PSScriptRoot "verify-release-kit.ps1"
    $rejectedAsReal = $false
    try { & $verifier -ReleaseRoot $resolvedSmokeRoot -PassThru }
    catch {
        if ($_.Exception.Message -notlike "*not a matching-SDK build*") { throw }
        $rejectedAsReal = $true
    }
    if (-not $rejectedAsReal) { throw "Release-kit verifier accepted a protocol fixture as real." }
    $embeddedVerifier = Join-Path $kitRoot "scripts\verify-release-kit.ps1"
    $embeddedResult = & $embeddedVerifier `
        -ReleaseRoot $resolvedSmokeRoot `
        -PassThru `
        -AllowProtocolOnlyFixture
    if ($embeddedResult.Passed -ne $true -or $embeddedResult.DependencyAuditPassed -ne $true) {
        throw "Embedded release-kit verifier did not pass its own exact package."
    }

    $pythonInstaller = Join-Path $kitRoot "scripts\install-python.ps1"
    $whatIfInstall = & $pythonInstaller `
        -ReleaseRoot $resolvedSmokeRoot `
        -DestinationRoot $installRoot `
        -AllowProtocolOnlyFixture `
        -WhatIf `
        -PassThru
    if ($whatIfInstall.WhatIf -ne $true -or (Test-Path -LiteralPath $installRoot)) {
        throw "Python installer -WhatIf changed the destination."
    }
    $pythonInstall = & $pythonInstaller `
        -ReleaseRoot $resolvedSmokeRoot `
        -DestinationRoot $installRoot `
        -AllowProtocolOnlyFixture `
        -PassThru
    if (
        $pythonInstall.Installed -ne $true -or
        $pythonInstall.LockedDependencies -ne $true -or
        $pythonInstall.SourceTreeImported -ne $false
    ) {
        throw "Hash-locked Python installation smoke failed."
    }
    $installedVerifier = Join-Path $kitRoot "scripts\verify-python-install.ps1"
    $installedEvidence = & $installedVerifier -InstallRoot $pythonInstall.Target -PassThru
    if ($installedEvidence.Passed -ne $true -or $installedEvidence.DistributionCount -lt 2) {
        throw "Installed Python environment did not verify."
    }
    $overwriteBlocked = $false
    try {
        & $pythonInstaller `
            -ReleaseRoot $resolvedSmokeRoot `
            -DestinationRoot $installRoot `
            -AllowProtocolOnlyFixture `
            -PassThru | Out-Null
    }
    catch {
        if ($_.Exception.Message -notlike "*never overwrites*") { throw }
        $overwriteBlocked = $true
    }
    if (-not $overwriteBlocked) { throw "Python installer overwrote an existing target." }
    $pythonUninstaller = Join-Path $kitRoot "scripts\uninstall-python.ps1"
    $whatIfUninstall = & $pythonUninstaller `
        -InstallRoot $pythonInstall.Target `
        -WhatIf `
        -PassThru
    if (
        $whatIfUninstall.WhatIf -ne $true -or
        -not (Test-Path -LiteralPath $pythonInstall.Target -PathType Container)
    ) {
        throw "Python uninstaller -WhatIf changed the verified installation."
    }
    $installedWheel = @(Get-ChildItem -LiteralPath (Join-Path $pythonInstall.Target "evidence") `
        -File -Filter "*.whl")
    $originalInstalledWheelBytes = [System.IO.File]::ReadAllBytes($installedWheel[0].FullName)
    $tamperedInstalledWheelBytes = [byte[]]$originalInstalledWheelBytes.Clone()
    $tamperedInstalledWheelBytes[0] = $tamperedInstalledWheelBytes[0] -bxor 1
    [System.IO.File]::WriteAllBytes($installedWheel[0].FullName, $tamperedInstalledWheelBytes)
    $installTamperBlocked = $false
    try {
        & $pythonUninstaller `
            -InstallRoot $pythonInstall.Target `
            -Confirm:$false `
            -PassThru | Out-Null
    }
    catch {
        if ($_.Exception.Message -notlike "*evidence hash mismatch*") { throw }
        $installTamperBlocked = $true
    }
    if (
        -not $installTamperBlocked -or
        -not (Test-Path -LiteralPath $pythonInstall.Target -PathType Container)
    ) {
        throw "Python uninstaller did not preserve modified wheel evidence."
    }
    [System.IO.File]::WriteAllBytes($installedWheel[0].FullName, $originalInstalledWheelBytes)
    $pythonUninstall = & $pythonUninstaller `
        -InstallRoot $pythonInstall.Target `
        -Confirm:$false `
        -PassThru
    if (
        $pythonUninstall.Removed -ne $true -or
        (Test-Path -LiteralPath $pythonInstall.Target)
    ) {
        throw "Verified Python installation remained after uninstall smoke."
    }

    $releaseInstaller = Join-Path $kitRoot "scripts\install-release-kit.ps1"
    $releaseUninstaller = Join-Path $kitRoot "scripts\uninstall-release-kit.ps1"
    $missingToolBlocked = $false
    try {
        & $releaseInstaller `
            -ReleaseRoot $resolvedSmokeRoot `
            -PilotRoot $orchestratedPilotRoot `
            -PythonDestinationRoot $orchestratedPythonRoot `
            -BundleDestinationRoot $orchestratedBundleRoot `
            -Uv (Join-Path $orchestratedRoot "missing-uv.exe") `
            -AllowProtocolOnlyFixture `
            -WhatIf `
            -PassThru | Out-Null
    }
    catch {
        if ($_.Exception.Message -notlike "*Install tool is missing*") { throw }
        $missingToolBlocked = $true
    }
    if (-not $missingToolBlocked -or (Test-Path -LiteralPath $orchestratedRoot)) {
        throw "Release installer did not reject a missing tool before mutation."
    }
    $overlapBlocked = $false
    try {
        & $releaseInstaller `
            -ReleaseRoot $resolvedSmokeRoot `
            -PilotRoot (Join-Path $orchestratedPythonRoot "pilot") `
            -PythonDestinationRoot $orchestratedPythonRoot `
            -BundleDestinationRoot $orchestratedBundleRoot `
            -AllowProtocolOnlyFixture `
            -WhatIf `
            -PassThru | Out-Null
    }
    catch {
        if ($_.Exception.Message -notlike "*must not overlap*") { throw }
        $overlapBlocked = $true
    }
    if (-not $overlapBlocked -or (Test-Path -LiteralPath $orchestratedRoot)) {
        throw "Release installer did not reject overlapping destinations before mutation."
    }
    $orchestratedPreview = & $releaseInstaller `
        -ReleaseRoot $resolvedSmokeRoot `
        -PilotRoot $orchestratedPilotRoot `
        -PythonDestinationRoot $orchestratedPythonRoot `
        -BundleDestinationRoot $orchestratedBundleRoot `
        -AllowProtocolOnlyFixture `
        -WhatIf `
        -PassThru
    if (
        $orchestratedPreview.WhatIf -ne $true -or
        $orchestratedPreview.PilotAction -cne "create" -or
        $orchestratedPreview.PythonAction -cne "install" -or
        $orchestratedPreview.BundleAction -cne "install" -or
        (Test-Path -LiteralPath $orchestratedRoot)
    ) {
        throw "Release installer -WhatIf did not safely preview all three components."
    }
    $orchestratedInstall = & $releaseInstaller `
        -ReleaseRoot $resolvedSmokeRoot `
        -PilotRoot $orchestratedPilotRoot `
        -PythonDestinationRoot $orchestratedPythonRoot `
        -BundleDestinationRoot $orchestratedBundleRoot `
        -AllowProtocolOnlyFixture `
        -Confirm:$false `
        -PassThru
    if (
        $orchestratedInstall.Installed -ne $true -or
        $orchestratedInstall.PilotAction -cne "create" -or
        $orchestratedInstall.PythonAction -cne "install" -or
        $orchestratedInstall.BundleAction -cne "install" -or
        $orchestratedInstall.AutoCADLaunched -ne $false -or
        $orchestratedInstall.PublishEnabled -ne $false
    ) {
        throw "Single-command release installation did not complete safely."
    }
    if (-not (Test-Path -LiteralPath $orchestratedInstall.InstallReceipt -PathType Leaf)) {
        throw "Single-command release installation did not retain its local receipt."
    }
    $installReceiptBytes = [System.IO.File]::ReadAllBytes($orchestratedInstall.InstallReceipt)
    $installReceiptHash = (
        Get-FileHash -LiteralPath $orchestratedInstall.InstallReceipt -Algorithm SHA256
    ).Hash.ToLowerInvariant()
    $installReceipt = Get-Content -LiteralPath $orchestratedInstall.InstallReceipt -Raw -Encoding UTF8 |
        ConvertFrom-Json
    if (
        $orchestratedInstall.InstallReceiptSha256 -cne $installReceiptHash -or
        $orchestratedInstall.InstallVerified -ne $true -or
        $orchestratedInstall.ConfigChangedSinceInstall -ne $false -or
        $installReceipt.payload.exact_commit -cne $orchestratedInstall.ExactCommit -or
        [string]$installReceipt.payload_sha256 -notmatch '^[0-9a-f]{64}$' -or
        $installReceipt.payload.autocad_running_at_install -ne $false -or
        $installReceipt.payload.autocad_launched -ne $false -or
        $installReceipt.payload.publish_enabled -ne $false -or
        $installReceipt.payload.live_publish_proven -ne $false
    ) {
        throw "Release installation receipt identity or safety evidence is invalid."
    }
    $releaseInstallVerifier = Join-Path $kitRoot "scripts\verify-release-install.ps1"
    $independentInstallEvidence = & $releaseInstallVerifier `
        -ReleaseRoot $resolvedSmokeRoot `
        -ReceiptPath $orchestratedInstall.InstallReceipt `
        -AllowProtocolOnlyFixture `
        -PassThru
    if (
        $independentInstallEvidence.Passed -ne $true -or
        $independentInstallEvidence.InstallationComplete -ne $true -or
        $independentInstallEvidence.BundleVerified -ne $true -or
        $independentInstallEvidence.PythonVerified -ne $true -or
        $independentInstallEvidence.ReceiptSha256 -cne $installReceiptHash -or
        $independentInstallEvidence.BundlePath -cne $orchestratedInstall.BundleTarget -or
        $independentInstallEvidence.PythonPath -cne $orchestratedInstall.PythonTarget -or
        $independentInstallEvidence.PilotRoot -cne $orchestratedInstall.PilotRoot -or
        $independentInstallEvidence.ConfigChangedSinceInstall -ne $false -or
        $independentInstallEvidence.AutoCADLaunched -ne $false -or
        $independentInstallEvidence.PublishEnabled -ne $false -or
        $independentInstallEvidence.LivePublishProven -ne $false
    ) {
        throw "Independent installed-release verification did not confirm the exact fixture."
    }
    $orchestratedResume = & $releaseInstaller `
        -ReleaseRoot $resolvedSmokeRoot `
        -PilotRoot $orchestratedPilotRoot `
        -PythonDestinationRoot $orchestratedPythonRoot `
        -BundleDestinationRoot $orchestratedBundleRoot `
        -AllowProtocolOnlyFixture `
        -Confirm:$false `
        -PassThru
    if (
        $orchestratedResume.PilotAction -cne "reuse_verified_structure" -or
        $orchestratedResume.PythonAction -cne "reuse_verified" -or
        $orchestratedResume.BundleAction -cne "reuse_verified"
    ) {
        throw "Release installer did not safely resume exact existing components."
    }
    $resumeReceiptHash = (
        Get-FileHash -LiteralPath $orchestratedResume.InstallReceipt -Algorithm SHA256
    ).Hash.ToLowerInvariant()
    if (
        $orchestratedResume.InstallReceipt -cne $orchestratedInstall.InstallReceipt -or
        $orchestratedResume.InstallReceiptSha256 -cne $installReceiptHash -or
        $resumeReceiptHash -cne $installReceiptHash
    ) {
        throw "Release installer overwrote or changed the exact existing install receipt."
    }
    $tamperedReceipt = Get-Content -LiteralPath $orchestratedInstall.InstallReceipt -Raw -Encoding UTF8 |
        ConvertFrom-Json
    $tamperedReceipt.payload.live_publish_proven = $true
    Write-SmokeJson -Path $orchestratedInstall.InstallReceipt -Value $tamperedReceipt
    $writtenTamperedReceipt = Get-Content -LiteralPath $orchestratedInstall.InstallReceipt `
        -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($writtenTamperedReceipt.payload.live_publish_proven -ne $true) {
        throw "Release installer smoke did not write its intended receipt tamper fixture."
    }
    $tamperedReceiptHash = (
        Get-FileHash -LiteralPath $orchestratedInstall.InstallReceipt -Algorithm SHA256
    ).Hash.ToLowerInvariant()
    $independentReceiptTamperBlocked = $false
    try {
        & $releaseInstallVerifier `
            -ReleaseRoot $resolvedSmokeRoot `
            -ReceiptPath $orchestratedInstall.InstallReceipt `
            -AllowProtocolOnlyFixture `
            -PassThru | Out-Null
    }
    catch {
        if ($_.Exception.Message -notlike "*receipt payload digest is invalid*") { throw }
        $independentReceiptTamperBlocked = $true
    }
    $receiptTamperBlocked = $false
    try {
        & $releaseInstaller `
            -ReleaseRoot $resolvedSmokeRoot `
            -PilotRoot $orchestratedPilotRoot `
            -PythonDestinationRoot $orchestratedPythonRoot `
            -BundleDestinationRoot $orchestratedBundleRoot `
            -AllowProtocolOnlyFixture `
            -Confirm:$false `
            -PassThru | Out-Null
    }
    catch {
        if ($_.Exception.Message -notlike "*receipt payload digest is invalid*") { throw }
        $receiptTamperBlocked = $true
    }
    $receiptHashAfterRejection = (
        Get-FileHash -LiteralPath $orchestratedInstall.InstallReceipt -Algorithm SHA256
    ).Hash.ToLowerInvariant()
    if (
        -not $independentReceiptTamperBlocked -or
        -not $receiptTamperBlocked -or
        $receiptHashAfterRejection -cne $tamperedReceiptHash
    ) {
        throw (
            "Release installer accepted or overwrote a modified local install receipt. " +
            "independent=$independentReceiptTamperBlocked installer=$receiptTamperBlocked " +
            "before=$tamperedReceiptHash after=$receiptHashAfterRejection"
        )
    }
    [System.IO.File]::WriteAllBytes($orchestratedInstall.InstallReceipt, $installReceiptBytes)
    $configBytes = [System.IO.File]::ReadAllBytes($orchestratedInstall.Config)
    $configChangeReported = $false
    try {
        [System.IO.File]::AppendAllText(
            $orchestratedInstall.Config,
            "`n# expected post-install inventory change`n",
            [System.Text.UTF8Encoding]::new($false)
        )
        $changedConfigEvidence = & $releaseInstallVerifier `
            -ReleaseRoot $resolvedSmokeRoot `
            -ReceiptPath $orchestratedInstall.InstallReceipt `
            -AllowProtocolOnlyFixture `
            -PassThru
        if (
            $changedConfigEvidence.Passed -ne $true -or
            $changedConfigEvidence.ConfigChangedSinceInstall -ne $true
        ) {
            throw "Independent installed-release verifier did not report an expected config change."
        }
        $configChangeReported = $true
    }
    finally {
        [System.IO.File]::WriteAllBytes($orchestratedInstall.Config, $configBytes)
    }

    $releaseUninstallProcessBlocked = $false
    function Get-Process {
        [CmdletBinding()]
        param([string]$Name)
        if ($Name -ceq "acad") { return [pscustomobject]@{ Id = 99117; ProcessName = "acad" } }
        Microsoft.PowerShell.Management\Get-Process @PSBoundParameters
    }
    try {
        & $releaseUninstaller `
            -ReleaseRoot $resolvedSmokeRoot `
            -ReceiptPath $orchestratedInstall.InstallReceipt `
            -AllowProtocolOnlyFixture `
            -WhatIf `
            -PassThru | Out-Null
    }
    catch {
        if ($_.Exception.Message -notlike "*Close every AutoCAD process*") { throw }
        $releaseUninstallProcessBlocked = $true
    }
    finally { Remove-Item Function:\Get-Process -Force }
    if (
        -not $releaseUninstallProcessBlocked -or
        -not (Test-Path -LiteralPath $orchestratedInstall.BundleTarget -PathType Container) -or
        -not (Test-Path -LiteralPath $orchestratedInstall.PythonTarget -PathType Container)
    ) {
        throw "Release uninstaller mutated a target while AutoCAD was reported as running."
    }

    $releaseUninstallPreview = & $releaseUninstaller `
        -ReleaseRoot $resolvedSmokeRoot `
        -ReceiptPath $orchestratedInstall.InstallReceipt `
        -AllowProtocolOnlyFixture `
        -WhatIf `
        -PassThru
    if (
        $releaseUninstallPreview.WhatIf -ne $true -or
        $releaseUninstallPreview.BundleAction -cne "remove" -or
        $releaseUninstallPreview.PythonAction -cne "remove" -or
        $releaseUninstallPreview.PilotAction -cne "preserve" -or
        $releaseUninstallPreview.ReceiptAction -cne "preserve" -or
        -not (Test-Path -LiteralPath $orchestratedInstall.BundleTarget -PathType Container) -or
        -not (Test-Path -LiteralPath $orchestratedInstall.PythonTarget -PathType Container)
    ) {
        throw "Release uninstaller -WhatIf did not safely preview both component removals."
    }

    # Simulate a prior run that stopped safely after removing the host-loadable bundle.
    $partialBundleUninstall = & (Join-Path $kitRoot "scripts\uninstall-bundle.ps1") `
        -DestinationRoot $orchestratedBundleRoot `
        -Confirm:$false `
        -PassThru
    if (
        $partialBundleUninstall.Removed -ne $true -or
        (Test-Path -LiteralPath $orchestratedInstall.BundleTarget) -or
        -not (Test-Path -LiteralPath $orchestratedInstall.PythonTarget -PathType Container)
    ) {
        throw "Release uninstall partial-run fixture did not retain only the Python component."
    }
    $releaseUninstall = & $releaseUninstaller `
        -ReleaseRoot $resolvedSmokeRoot `
        -ReceiptPath $orchestratedInstall.InstallReceipt `
        -AllowProtocolOnlyFixture `
        -Confirm:$false `
        -PassThru
    if (
        $releaseUninstall.Uninstalled -ne $true -or
        $releaseUninstall.BundleAction -cne "already_absent" -or
        $releaseUninstall.BundleRemovedThisRun -ne $false -or
        $releaseUninstall.PythonAction -cne "remove" -or
        $releaseUninstall.PythonRemovedThisRun -ne $true -or
        $releaseUninstall.ComponentsRemovedThisRun -ne 1 -or
        $releaseUninstall.PilotPreserved -ne $true -or
        $releaseUninstall.ReceiptPreserved -ne $true -or
        $releaseUninstall.InstallReceiptSha256 -cne $installReceiptHash -or
        (Test-Path -LiteralPath $orchestratedInstall.BundleTarget) -or
        (Test-Path -LiteralPath $orchestratedInstall.PythonTarget) -or
        -not (Test-Path -LiteralPath $orchestratedInstall.PilotRoot -PathType Container) -or
        -not (Test-Path -LiteralPath $orchestratedInstall.InstallReceipt -PathType Leaf)
    ) {
        throw "Release uninstaller did not resume safely or preserve pilot/receipt evidence."
    }
    $releaseUninstallResume = & $releaseUninstaller `
        -ReleaseRoot $resolvedSmokeRoot `
        -ReceiptPath $orchestratedInstall.InstallReceipt `
        -AllowProtocolOnlyFixture `
        -Confirm:$false `
        -PassThru
    if (
        $releaseUninstallResume.Uninstalled -ne $true -or
        $releaseUninstallResume.BundleAction -cne "already_absent" -or
        $releaseUninstallResume.PythonAction -cne "already_absent" -or
        $releaseUninstallResume.ComponentsRemovedThisRun -ne 0 -or
        $releaseUninstallResume.InstallReceiptSha256 -cne $installReceiptHash
    ) {
        throw "Release uninstaller was not idempotent after complete removal."
    }

    $outerBytes = [System.IO.File]::ReadAllBytes($outerPath)
    $tamperedOuter = Get-Content -LiteralPath $outerPath -Raw | ConvertFrom-Json
    $tamperedOuter.dependency_audit.python_license_inventory.packages[0].license = "UNKNOWN"
    Write-SmokeJson -Path $outerPath -Value $tamperedOuter
    $licenseTamperBlocked = $false
    try { & $verifier -ReleaseRoot $resolvedSmokeRoot -PassThru -AllowProtocolOnlyFixture }
    catch {
        if ($_.Exception.Message -notlike "*license inventory*") { throw }
        $licenseTamperBlocked = $true
    }
    if (-not $licenseTamperBlocked) {
        throw "Release-kit verifier accepted an unknown dependency license."
    }
    [System.IO.File]::WriteAllBytes($outerPath, $outerBytes)

    $manifestBytes = [System.IO.File]::ReadAllBytes($manifestPath)
    $sbomBytes = [System.IO.File]::ReadAllBytes($sbomPath)
    $sbomTamper = Get-Content -LiteralPath $sbomPath -Raw | ConvertFrom-Json
    @($sbomTamper.metadata.component.properties | Where-Object {
        $_.name -ceq "cadplot:live-publish-proven"
    })[0].value = "true"
    [System.IO.File]::WriteAllText(
        $sbomPath,
        ($sbomTamper | ConvertTo-Json -Depth 10),
        [System.Text.UTF8Encoding]::new($false)
    )
    $semanticHash = (Get-FileHash -LiteralPath $sbomPath -Algorithm SHA256).Hash.ToLowerInvariant()
    $semanticManifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    $semanticManifest.sbom.sha256 = $semanticHash
    @($semanticManifest.files | Where-Object { $_.path -ceq "cadplot-mcp.cdx.json" })[0].sha256 = $semanticHash
    Write-SmokeJson -Path $manifestPath -Value $semanticManifest
    $semanticOuter = [System.Text.Encoding]::UTF8.GetString($outerBytes) | ConvertFrom-Json
    $semanticOuter.sbom.sha256 = $semanticHash
    $semanticOuter.kit_manifest_sha256 = (
        Get-FileHash -LiteralPath $manifestPath -Algorithm SHA256
    ).Hash.ToLowerInvariant()
    Write-SmokeJson -Path $outerPath -Value $semanticOuter
    $sbomSemanticTamperBlocked = $false
    try { & $verifier -ReleaseRoot $resolvedSmokeRoot -PassThru -AllowProtocolOnlyFixture }
    catch {
        if ($_.Exception.Message -notlike "*SBOM release binding or evidence boundaries*") { throw }
        $sbomSemanticTamperBlocked = $true
    }
    if (-not $sbomSemanticTamperBlocked) {
        throw "Release-kit verifier accepted altered SBOM evidence boundaries."
    }
    [System.IO.File]::WriteAllBytes($sbomPath, $sbomBytes)
    [System.IO.File]::WriteAllBytes($manifestPath, $manifestBytes)
    [System.IO.File]::WriteAllBytes($outerPath, $outerBytes)

    $durableTamper = Get-Content -LiteralPath $outerPath -Raw | ConvertFrom-Json
    $durableTamper.durable_queue_recovery.authentication_scheme = "unsigned"
    Write-SmokeJson -Path $outerPath -Value $durableTamper
    $durableTamperBlocked = $false
    try { & $verifier -ReleaseRoot $resolvedSmokeRoot -PassThru -AllowProtocolOnlyFixture }
    catch {
        if ($_.Exception.Message -notlike "*durable queue recovery evidence*") { throw }
        $durableTamperBlocked = $true
    }
    if (-not $durableTamperBlocked) {
        throw "Release-kit verifier accepted altered durable queue recovery evidence."
    }
    [System.IO.File]::WriteAllBytes($outerPath, $outerBytes)

    $targetProbeTamper = Get-Content -LiteralPath $outerPath -Raw | ConvertFrom-Json
    $targetProbeTamper.wheel_install_smoke.tunnel_preflight_target_probed = $false
    Write-SmokeJson -Path $outerPath -Value $targetProbeTamper
    $targetProbeTamperBlocked = $false
    try { & $verifier -ReleaseRoot $resolvedSmokeRoot -PassThru -AllowProtocolOnlyFixture }
    catch {
        if ($_.Exception.Message -notlike "*local-target probe evidence*") { throw }
        $targetProbeTamperBlocked = $true
    }
    if (-not $targetProbeTamperBlocked) {
        throw "Release-kit verifier accepted altered local-target probe evidence."
    }
    [System.IO.File]::WriteAllBytes($outerPath, $outerBytes)

    $archiveBytes = [System.IO.File]::ReadAllBytes($archivePath)
    $archiveBytes[0] = $archiveBytes[0] -bxor 1
    [System.IO.File]::WriteAllBytes($archivePath, $archiveBytes)
    $tamperBlocked = $false
    try { & $verifier -ReleaseRoot $resolvedSmokeRoot -PassThru -AllowProtocolOnlyFixture }
    catch {
        if ($_.Exception.Message -notlike "*outer hash evidence does not match*") { throw }
        $tamperBlocked = $true
    }
    if (-not $tamperBlocked) { throw "Release-kit verifier accepted a modified outer archive." }

    [ordered]@{
        passed = $true
        protocol_only_fixture = $true
        protocol_only_rejected_as_real = $rejectedAsReal
        exact_tree_and_hashes_verified = $true
        embedded_self_verification_passed = $true
        dependency_audit_verified = $true
        sbom_verified = $true
        sbom_semantic_tamper_blocked = $sbomSemanticTamperBlocked
        dependency_license_tamper_blocked = $licenseTamperBlocked
        durable_queue_tamper_blocked = $durableTamperBlocked
        tunnel_target_probe_tamper_blocked = $targetProbeTamperBlocked
        archive_tamper_blocked = $tamperBlocked
        python_install_what_if_safe = $true
        python_install_locked_dependencies = $true
        python_install_overwrite_blocked = $overwriteBlocked
        python_install_tamper_blocked = $installTamperBlocked
        python_uninstall_what_if_safe = $true
        python_uninstall_verified = $true
        release_install_what_if_safe = $true
        release_install_tool_preflight_blocked = $missingToolBlocked
        release_install_overlap_blocked = $overlapBlocked
        release_install_completed = $true
        release_install_resume_verified = $true
        release_install_bundle_last = $true
        release_install_receipt_verified = $true
        release_install_receipt_tamper_blocked = $receiptTamperBlocked
        release_install_receipt_independent_verified = $true
        release_install_receipt_independent_tamper_blocked = $independentReceiptTamperBlocked
        release_install_config_change_reported = $configChangeReported
        release_uninstall_autocad_process_blocked = $releaseUninstallProcessBlocked
        release_uninstall_what_if_safe = $true
        release_uninstall_partial_resume_verified = $true
        release_uninstall_completed = $true
        release_uninstall_idempotent = $true
        release_uninstall_pilot_preserved = $true
        release_uninstall_receipt_preserved = $true
        matching_sdk_bundle_built = $false
        autocad_launched = $false
        live_publish_proven = $false
    } | ConvertTo-Json
}
finally {
    if (Test-Path -LiteralPath $resolvedSmokeRoot) {
        Remove-SmokeDirectory -Path $resolvedSmokeRoot
    }
    if (Test-Path -LiteralPath $installRoot) {
        Remove-SmokeDirectory -Path $installRoot
    }
    if (Test-Path -LiteralPath $orchestratedRoot) {
        Remove-SmokeDirectory -Path $orchestratedRoot
    }
    if (Test-Path -LiteralPath $requirementsAuditPath -PathType Leaf) {
        Remove-Item -LiteralPath $requirementsAuditPath -Force
    }
    if (Test-Path -LiteralPath $dependencyAuditInputPath -PathType Leaf) {
        Remove-Item -LiteralPath $dependencyAuditInputPath -Force
    }
}
