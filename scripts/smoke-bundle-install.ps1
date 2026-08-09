[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$smokeRoot = Join-Path `
    ([System.IO.Path]::GetTempPath()) `
    ("cadplot-bundle-smoke-{0}" -f [Guid]::NewGuid().ToString("N"))
$resolvedSmokeRoot = [System.IO.Path]::GetFullPath($smokeRoot)
$resolvedTempRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath()).TrimEnd('\') + '\'
if (-not $resolvedSmokeRoot.StartsWith(
    $resolvedTempRoot,
    [System.StringComparison]::OrdinalIgnoreCase
)) {
    throw "Bundle smoke root escaped the system temporary directory."
}

$fixtureBundle = Join-Path $resolvedSmokeRoot "fixture\CadPlotMcp.bundle"
$destinationRoot = Join-Path $resolvedSmokeRoot "plugins"
$installedBundle = Join-Path $destinationRoot "CadPlotMcp.bundle"
$verifier = Join-Path $PSScriptRoot "verify-bundle.ps1"
$releaseVerifier = Join-Path $PSScriptRoot "verify-bundle-release.ps1"
$installer = Join-Path $PSScriptRoot "install-bundle.ps1"
$uninstaller = Join-Path $PSScriptRoot "uninstall-bundle.ps1"

try {
    $null = New-Item -ItemType Directory -Path $fixtureBundle
    Copy-Item `
        -LiteralPath (Join-Path $repoRoot "bundle\CadPlotMcp.bundle\PackageContents.xml") `
        -Destination $fixtureBundle
    Copy-Item -LiteralPath (Join-Path $repoRoot "LICENSE") -Destination $fixtureBundle

    $fixture2016 = Join-Path $fixtureBundle "Contents\Windows\2016"
    $fixture2025 = Join-Path $fixtureBundle "Contents\Windows\2025"
    $null = New-Item -ItemType Directory -Path $fixture2016,$fixture2025
    Copy-Item -LiteralPath (
        Join-Path $repoRoot "src\dotnet\CadPlotMcp.AutoCAD2016\bin\Release\net45\CadPlotMcp.AutoCAD2016.dll"
    ) -Destination $fixture2016
    Copy-Item -LiteralPath (
        Join-Path $repoRoot "src\dotnet\CadPlotMcp.Core\bin\Release\net45\CadPlotMcp.Core.dll"
    ) -Destination $fixture2016
    Copy-Item -LiteralPath (
        Join-Path $repoRoot "src\dotnet\CadPlotMcp.AutoCAD2025\bin\Release\net8.0-windows\CadPlotMcp.AutoCAD2025.dll"
    ) -Destination $fixture2025
    Copy-Item -LiteralPath (
        Join-Path $repoRoot "src\dotnet\CadPlotMcp.Core\bin\Release\net8.0\CadPlotMcp.Core.dll"
    ) -Destination $fixture2025

    $bundleVerification = & $verifier -BundlePath $fixtureBundle -PassThru

    $fixtureRelease = Split-Path -Parent $fixtureBundle
    $fixtureArchive = Join-Path $fixtureRelease "CadPlotMcp.bundle.zip"
    Compress-Archive `
        -LiteralPath $fixtureBundle `
        -DestinationPath $fixtureArchive `
        -CompressionLevel Optimal
    & uv run python (Join-Path $PSScriptRoot "audit-release-artifacts.py") $fixtureRelease
    if ($LASTEXITCODE -ne 0) { throw "Protocol-only bundle archive audit failed." }

    $fixtureApiNames = @("AcMgd.dll", "AcDbMgd.dll", "AcCoreMgd.dll")
    $fixtureManifest = [ordered]@{
        schema_version = 1
        exact_commit = ("0" * 40) -join ""
        package_version = "0.1.0"
        created_utc = [DateTime]::UtcNow.ToString("o")
        api_identity = [ordered]@{
            autocad_2016 = [ordered]@{
                detected_series = "R20.1"
                assemblies = @($fixtureApiNames | ForEach-Object {
                    [ordered]@{
                        name = $_
                        assembly_version = "20.1.0.0"
                        sha256 = ("a" * 64) -join ""
                    }
                })
            }
            autocad_2025 = [ordered]@{
                detected_series = "R25.0"
                assemblies = @($fixtureApiNames | ForEach-Object {
                    [ordered]@{
                        name = $_
                        assembly_version = "25.0.0.0"
                        sha256 = ("b" * 64) -join ""
                    }
                })
            }
        }
        bundle = [ordered]@{
            directory = "CadPlotMcp.bundle"
            archive = "CadPlotMcp.bundle.zip"
            archive_sha256 = (
                Get-FileHash -LiteralPath $fixtureArchive -Algorithm SHA256
            ).Hash.ToLowerInvariant()
            files = @($bundleVerification.Hashes | ForEach-Object {
                [ordered]@{ path = $_.Path; sha256 = $_.Sha256 }
            })
        }
        source_tree_audit_passed = $true
        bundle_verification_passed = $true
        archive_audit_passed = $true
        matching_sdk_bundle_built = $false
        protocol_only_fixture = $true
        company_assets_copied = $false
        autodesk_binaries_included = $false
        autocad_launched = $false
        live_publish_proven = $false
    }
    $fixtureManifestPath = Join-Path $fixtureRelease "bundle-build.json"
    [System.IO.File]::WriteAllText(
        $fixtureManifestPath,
        ($fixtureManifest | ConvertTo-Json -Depth 7),
        [System.Text.UTF8Encoding]::new($false)
    )

    $protocolOnlyRejected = $false
    try {
        & $releaseVerifier -ReleaseRoot $fixtureRelease -PassThru
    }
    catch {
        if ($_.Exception.Message -notlike "*not a matching-SDK build*") { throw }
        $protocolOnlyRejected = $true
    }
    if (-not $protocolOnlyRejected) {
        throw "Release verifier accepted a protocol-only fixture as a real SDK build."
    }
    $null = & $releaseVerifier `
        -ReleaseRoot $fixtureRelease `
        -PassThru `
        -AllowProtocolOnlyFixture

    $releaseKitSmoke = & (Join-Path $PSScriptRoot "smoke-release-kit.ps1") `
        -BundleReleaseRoot $fixtureRelease | ConvertFrom-Json
    if (
        $releaseKitSmoke.passed -ne $true -or
        $releaseKitSmoke.dependency_audit_verified -ne $true -or
        $releaseKitSmoke.dependency_license_tamper_blocked -ne $true -or
        $releaseKitSmoke.python_install_what_if_safe -ne $true -or
        $releaseKitSmoke.python_install_locked_dependencies -ne $true -or
        $releaseKitSmoke.python_install_overwrite_blocked -ne $true -or
        $releaseKitSmoke.python_install_tamper_blocked -ne $true -or
        $releaseKitSmoke.python_uninstall_what_if_safe -ne $true -or
        $releaseKitSmoke.python_uninstall_verified -ne $true -or
        $releaseKitSmoke.release_install_what_if_safe -ne $true -or
        $releaseKitSmoke.release_install_tool_preflight_blocked -ne $true -or
        $releaseKitSmoke.release_install_overlap_blocked -ne $true -or
        $releaseKitSmoke.release_install_completed -ne $true -or
        $releaseKitSmoke.release_install_resume_verified -ne $true -or
        $releaseKitSmoke.release_install_bundle_last -ne $true -or
        $releaseKitSmoke.release_install_receipt_verified -ne $true -or
        $releaseKitSmoke.release_install_receipt_tamper_blocked -ne $true -or
        $releaseKitSmoke.release_install_receipt_independent_verified -ne $true -or
        $releaseKitSmoke.release_install_receipt_independent_tamper_blocked -ne $true -or
        $releaseKitSmoke.release_install_config_change_reported -ne $true -or
        $releaseKitSmoke.release_uninstall_autocad_process_blocked -ne $true -or
        $releaseKitSmoke.release_uninstall_what_if_safe -ne $true -or
        $releaseKitSmoke.release_uninstall_partial_resume_verified -ne $true -or
        $releaseKitSmoke.release_uninstall_completed -ne $true -or
        $releaseKitSmoke.release_uninstall_idempotent -ne $true -or
        $releaseKitSmoke.release_uninstall_pilot_preserved -ne $true -or
        $releaseKitSmoke.release_uninstall_receipt_preserved -ne $true
    ) {
        throw "Protocol-only combined release-kit smoke failed."
    }

    $archiveBytes = [System.IO.File]::ReadAllBytes($fixtureArchive)
    $archiveBytes[0] = $archiveBytes[0] -bxor 1
    [System.IO.File]::WriteAllBytes($fixtureArchive, $archiveBytes)
    $archiveTamperBlocked = $false
    try {
        & $releaseVerifier `
            -ReleaseRoot $fixtureRelease `
            -PassThru `
            -AllowProtocolOnlyFixture
    }
    catch {
        if ($_.Exception.Message -notlike "*archive hash does not match*") { throw }
        $archiveTamperBlocked = $true
    }
    if (-not $archiveTamperBlocked) {
        throw "Release verifier accepted a modified bundle archive."
    }

    $installProcessGuardBlocked = $false
    function Get-Process {
        [CmdletBinding()]
        param([string]$Name)
        [pscustomobject]@{ ProcessName = $Name; Id = 4242 }
    }
    try {
        & $installer -SourceBundle $fixtureBundle -DestinationRoot $destinationRoot -WhatIf
    }
    catch {
        if ($_.Exception.Message -notlike "*Close every AutoCAD process*") { throw }
        $installProcessGuardBlocked = $true
    }
    finally { Remove-Item Function:\Get-Process -Force }
    if (-not $installProcessGuardBlocked -or (Test-Path -LiteralPath $installedBundle)) {
        throw "Bundle installer did not fail closed for a simulated running acad.exe."
    }

    & $installer -SourceBundle $fixtureBundle -DestinationRoot $destinationRoot -WhatIf
    if (Test-Path -LiteralPath $installedBundle) {
        throw "Install -WhatIf created a destination bundle."
    }
    & $installer -SourceBundle $fixtureBundle -DestinationRoot $destinationRoot -Confirm:$false
    $null = & $verifier -BundlePath $installedBundle -PassThru

    $uninstallProcessGuardBlocked = $false
    function Get-Process {
        [CmdletBinding()]
        param([string]$Name)
        [pscustomobject]@{ ProcessName = $Name; Id = 4242 }
    }
    try {
        & $uninstaller -DestinationRoot $destinationRoot -WhatIf
    }
    catch {
        if ($_.Exception.Message -notlike "*Close every AutoCAD process*") { throw }
        $uninstallProcessGuardBlocked = $true
    }
    finally { Remove-Item Function:\Get-Process -Force }
    if (-not $uninstallProcessGuardBlocked -or -not (Test-Path -LiteralPath $installedBundle)) {
        throw "Bundle uninstaller did not preserve the bundle for a simulated running acad.exe."
    }

    $overwriteBlocked = $false
    try {
        & $installer -SourceBundle $fixtureBundle -DestinationRoot $destinationRoot -Confirm:$false
    }
    catch {
        if ($_.Exception.Message -notlike "*will not overwrite*") { throw }
        $overwriteBlocked = $true
    }
    if (-not $overwriteBlocked) {
        throw "Installer accepted an existing destination bundle."
    }

    $driveRootBlocked = $false
    try {
        & $uninstaller `
            -DestinationRoot ([System.IO.Path]::GetPathRoot($destinationRoot)) `
            -WhatIf `
            -PassThru | Out-Null
    }
    catch {
        if ($_.Exception.Message -notlike "*drive root*") { throw }
        $driveRootBlocked = $true
    }
    if (-not $driveRootBlocked) {
        throw "Bundle uninstaller accepted a drive root destination."
    }

    $whatIfBundleUninstall = & $uninstaller `
        -DestinationRoot $destinationRoot `
        -WhatIf `
        -PassThru
    $whatIfBundleQuarantines = @(
        Get-ChildItem -LiteralPath $destinationRoot -Force -Directory `
            -Filter ".cadplot-bundle-removing-*"
    )
    if (
        $whatIfBundleUninstall.WhatIf -ne $true -or
        -not (Test-Path -LiteralPath $installedBundle -PathType Container) -or
        $whatIfBundleQuarantines.Count -ne 0
    ) {
        throw "Uninstall -WhatIf removed the installed bundle."
    }

    $unexpectedFile = Join-Path $installedBundle "unexpected.txt"
    [System.IO.File]::WriteAllText($unexpectedFile, "smoke-only")
    $unexpectedFileBlocked = $false
    try {
        & $uninstaller -DestinationRoot $destinationRoot -Confirm:$false
    }
    catch {
        if ($_.Exception.Message -notlike "*unexpected files*") { throw }
        $unexpectedFileBlocked = $true
    }
    if (-not $unexpectedFileBlocked -or -not (Test-Path -LiteralPath $installedBundle)) {
        throw "Uninstaller did not preserve a bundle containing an unexpected file."
    }
    [System.IO.File]::Delete($unexpectedFile)

    $bundleUninstall = & $uninstaller `
        -DestinationRoot $destinationRoot `
        -Confirm:$false `
        -PassThru
    $bundleQuarantines = @(
        Get-ChildItem -LiteralPath $destinationRoot -Force -Directory `
            -Filter ".cadplot-bundle-removing-*"
    )
    if (
        $bundleUninstall.Removed -ne $true -or
        $bundleUninstall.QuarantineRemoved -ne $true -or
        (Test-Path -LiteralPath $installedBundle) -or
        $bundleQuarantines.Count -ne 0
    ) {
        throw "Verified bundle remained after smoke uninstall."
    }

    [ordered]@{
        passed = $true
        protocol_only_fixture = $true
        protocol_only_rejected_as_real = $protocolOnlyRejected
        bundle_release_verified = $true
        bundle_release_archive_tamper_blocked = $archiveTamperBlocked
        release_kit_verified = $releaseKitSmoke.exact_tree_and_hashes_verified
        release_kit_self_verification_passed = $releaseKitSmoke.embedded_self_verification_passed
        release_kit_protocol_only_rejected_as_real = $releaseKitSmoke.protocol_only_rejected_as_real
        release_kit_dependency_audit_verified = $releaseKitSmoke.dependency_audit_verified
        release_kit_dependency_license_tamper_blocked = $releaseKitSmoke.dependency_license_tamper_blocked
        release_kit_archive_tamper_blocked = $releaseKitSmoke.archive_tamper_blocked
        python_install_what_if_safe = $releaseKitSmoke.python_install_what_if_safe
        python_install_locked_dependencies = $releaseKitSmoke.python_install_locked_dependencies
        python_install_overwrite_blocked = $releaseKitSmoke.python_install_overwrite_blocked
        python_install_tamper_blocked = $releaseKitSmoke.python_install_tamper_blocked
        python_uninstall_what_if_safe = $releaseKitSmoke.python_uninstall_what_if_safe
        python_uninstall_verified = $releaseKitSmoke.python_uninstall_verified
        release_install_what_if_safe = $releaseKitSmoke.release_install_what_if_safe
        release_install_tool_preflight_blocked = $releaseKitSmoke.release_install_tool_preflight_blocked
        release_install_overlap_blocked = $releaseKitSmoke.release_install_overlap_blocked
        release_install_completed = $releaseKitSmoke.release_install_completed
        release_install_resume_verified = $releaseKitSmoke.release_install_resume_verified
        release_install_bundle_last = $releaseKitSmoke.release_install_bundle_last
        release_install_receipt_verified = $releaseKitSmoke.release_install_receipt_verified
        release_install_receipt_tamper_blocked = $releaseKitSmoke.release_install_receipt_tamper_blocked
        release_install_receipt_independent_verified = $releaseKitSmoke.release_install_receipt_independent_verified
        release_install_receipt_independent_tamper_blocked = $releaseKitSmoke.release_install_receipt_independent_tamper_blocked
        release_install_config_change_reported = $releaseKitSmoke.release_install_config_change_reported
        release_uninstall_autocad_process_blocked = $releaseKitSmoke.release_uninstall_autocad_process_blocked
        release_uninstall_what_if_safe = $releaseKitSmoke.release_uninstall_what_if_safe
        release_uninstall_partial_resume_verified = $releaseKitSmoke.release_uninstall_partial_resume_verified
        release_uninstall_completed = $releaseKitSmoke.release_uninstall_completed
        release_uninstall_idempotent = $releaseKitSmoke.release_uninstall_idempotent
        release_uninstall_pilot_preserved = $releaseKitSmoke.release_uninstall_pilot_preserved
        release_uninstall_receipt_preserved = $releaseKitSmoke.release_uninstall_receipt_preserved
        what_if_install_mutated = $false
        copied_hashes_verified = $true
        bundle_install_autocad_process_blocked = $installProcessGuardBlocked
        existing_install_blocked = $overwriteBlocked
        what_if_uninstall_mutated = $false
        unexpected_file_uninstall_blocked = $unexpectedFileBlocked
        bundle_uninstall_what_if_safe = $true
        bundle_uninstall_drive_root_blocked = $driveRootBlocked
        bundle_uninstall_autocad_process_blocked = $uninstallProcessGuardBlocked
        bundle_uninstall_quarantine_removed = $true
        uninstall_verified = $true
        autocad_launched = $false
        live_publish_proven = $false
    } | ConvertTo-Json
}
finally {
    if (Test-Path -LiteralPath $resolvedSmokeRoot) {
        Remove-Item -LiteralPath $resolvedSmokeRoot -Recurse -Force
    }
}
