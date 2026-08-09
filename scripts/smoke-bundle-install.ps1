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

    $null = & $verifier -BundlePath $fixtureBundle -PassThru

    & $installer -SourceBundle $fixtureBundle -DestinationRoot $destinationRoot -WhatIf
    if (Test-Path -LiteralPath $installedBundle) {
        throw "Install -WhatIf created a destination bundle."
    }
    & $installer -SourceBundle $fixtureBundle -DestinationRoot $destinationRoot -Confirm:$false
    $null = & $verifier -BundlePath $installedBundle -PassThru

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

    & $uninstaller -DestinationRoot $destinationRoot -WhatIf
    if (-not (Test-Path -LiteralPath $installedBundle -PathType Container)) {
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

    & $uninstaller -DestinationRoot $destinationRoot -Confirm:$false
    if (Test-Path -LiteralPath $installedBundle) {
        throw "Verified bundle remained after smoke uninstall."
    }

    [ordered]@{
        passed = $true
        protocol_only_fixture = $true
        what_if_install_mutated = $false
        copied_hashes_verified = $true
        existing_install_blocked = $overwriteBlocked
        what_if_uninstall_mutated = $false
        unexpected_file_uninstall_blocked = $unexpectedFileBlocked
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
