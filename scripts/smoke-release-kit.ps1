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
        "install-release-kit.ps1",
        "check-autocad-api-series.ps1", "new-local-pilot.ps1",
        "collect-pilot-run.py", "assemble-pilot-evidence.py",
        "validate-pilot-evidence.py"
    )) {
        Copy-Item -LiteralPath (Join-Path $PSScriptRoot $scriptName) `
            -Destination (Join-Path $kitRoot "scripts\$scriptName")
    }
    foreach ($docName in @(
        "monday-pilot.md", "pazartesi-demo-tr.md", "release-checklist.md",
        "release-kit-install.md", "pilot-evidence.md", "deployment-modes.md",
        "chatgpt-connection.md", "loopback-http.md"
    )) {
        Copy-Item -LiteralPath (Join-Path $repoRoot "docs\$docName") `
            -Destination (Join-Path $kitRoot "docs\$docName")
    }
    Copy-Item -LiteralPath (Join-Path $repoRoot "examples\config.inventory.example.yaml") `
        -Destination (Join-Path $kitRoot "config\config.inventory.example.yaml")
    foreach ($fileName in @("LICENSE", "README.md", "README.tr.md", "THIRD_PARTY_NOTICES.md")) {
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
        files = $files
        dependency_audit_ran = $true
        dependency_audit = $dependencyAudit
        synthetic_batch_rehearsal = [ordered]@{
            target_drawings = 300
            planning_pages = 15
            ready = 300
            staging_batches = 15
            staged = 300
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
        $installReceipt.payload.exact_commit -cne $orchestratedInstall.ExactCommit -or
        [string]$installReceipt.payload_sha256 -notmatch '^[0-9a-f]{64}$' -or
        $installReceipt.payload.autocad_running_at_install -ne $false -or
        $installReceipt.payload.autocad_launched -ne $false -or
        $installReceipt.payload.publish_enabled -ne $false -or
        $installReceipt.payload.live_publish_proven -ne $false
    ) {
        throw "Release installation receipt identity or safety evidence is invalid."
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
    if (-not $receiptTamperBlocked -or $receiptHashAfterRejection -cne $tamperedReceiptHash) {
        throw (
            "Release installer accepted or overwrote a modified local install receipt. " +
            "blocked=$receiptTamperBlocked before=$tamperedReceiptHash after=$receiptHashAfterRejection"
        )
    }
    [System.IO.File]::WriteAllBytes($orchestratedInstall.InstallReceipt, $installReceiptBytes)
    $orchestratedBundleUninstall = & (Join-Path $kitRoot "scripts\uninstall-bundle.ps1") `
        -DestinationRoot $orchestratedBundleRoot `
        -Confirm:$false `
        -PassThru
    $orchestratedPythonUninstall = & $pythonUninstaller `
        -InstallRoot $orchestratedInstall.PythonTarget `
        -Confirm:$false `
        -PassThru
    if (
        $orchestratedBundleUninstall.Removed -ne $true -or
        $orchestratedPythonUninstall.Removed -ne $true
    ) {
        throw "Release installer smoke cleanup did not verify component removal."
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
        dependency_license_tamper_blocked = $licenseTamperBlocked
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
}
