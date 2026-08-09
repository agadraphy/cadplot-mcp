[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = "High")]
param(
    [string]$DestinationRoot = "",

    [switch]$PassThru
)

$ErrorActionPreference = "Stop"

function Assert-NoRedirectedPath {
    param([Parameter(Mandatory = $true)][string]$Path)

    $current = [System.IO.Path]::GetFullPath($Path)
    while (-not [string]::IsNullOrWhiteSpace($current)) {
        if (Test-Path -LiteralPath $current) {
            $item = Get-Item -LiteralPath $current -Force
            if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "Refusing to remove a bundle through a symlink or junction: $current"
            }
        }
        $parent = Split-Path -Parent $current
        if ([string]::IsNullOrWhiteSpace($parent) -or $parent -eq $current) { break }
        $current = $parent
    }
}

function Get-ExtendedLengthPath {
    param([Parameter(Mandatory = $true)][string]$Path)

    if ($Path.StartsWith('\\')) {
        return "\\?\UNC\$($Path.Substring(2))"
    }
    return "\\?\$Path"
}

function Assert-CadPlotBundleIdentity {
    param([Parameter(Mandatory = $true)][string]$BundlePath)

    $manifestPath = Join-Path $BundlePath "PackageContents.xml"
    if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
        throw "Refusing to remove an unrecognized directory without PackageContents.xml."
    }
    $manifestItem = Get-Item -LiteralPath $manifestPath -Force
    if ($manifestItem.Length -gt 1MB) {
        throw "Refusing to parse a bundle manifest larger than 1 MiB."
    }
    [xml]$manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8
    $package = $manifest.ApplicationPackage
    $expectedProductCode = "{C2E79B66-6076-40D4-AE45-E725A644B288}"
    if ($package.Name -ne "CadPlot MCP" -or $package.ProductCode -ne $expectedProductCode) {
        throw "Refusing to remove a directory that is not the expected CadPlot MCP package."
    }
}

function Get-BundleHashSignature {
    param([Parameter(Mandatory = $true)]$Verification)

    return (@($Verification.Hashes | Sort-Object Path | ForEach-Object {
        "{0}:{1}" -f $_.Path,$_.Sha256
    }) -join '|')
}

if ([string]::IsNullOrWhiteSpace($DestinationRoot)) {
    $DestinationRoot = Join-Path (
        [Environment]::GetFolderPath("ApplicationData")
    ) "Autodesk\ApplicationPlugins"
}

$resolvedDestinationRoot = [System.IO.Path]::GetFullPath($DestinationRoot).TrimEnd('\')
$destinationVolumeRoot = [System.IO.Path]::GetPathRoot($resolvedDestinationRoot).TrimEnd('\')
if (
    [string]::IsNullOrWhiteSpace($resolvedDestinationRoot) -or
    $resolvedDestinationRoot -ieq $destinationVolumeRoot
) {
    throw "Refusing to use a drive root as the bundle uninstall destination."
}
$destinationBundle = [System.IO.Path]::GetFullPath(
    (Join-Path $resolvedDestinationRoot "CadPlotMcp.bundle")
)
if ((Split-Path -Parent $destinationBundle) -cne $resolvedDestinationRoot) {
    throw "CadPlot MCP bundle target escaped its explicit destination root."
}
if (-not (Test-Path -LiteralPath $destinationBundle -PathType Container)) {
    throw "CadPlot MCP bundle is not installed at: $destinationBundle"
}

Assert-NoRedirectedPath -Path $destinationBundle
Assert-CadPlotBundleIdentity -BundlePath $destinationBundle
$verifier = Join-Path $PSScriptRoot "verify-bundle.ps1"
$verification = & $verifier -BundlePath $destinationBundle -PassThru
if ($verification.Passed -ne $true) {
    throw "Refusing to remove a CadPlot MCP bundle that did not verify."
}
$initialSignature = Get-BundleHashSignature -Verification $verification

if (-not $PSCmdlet.ShouldProcess($destinationBundle, "Remove verified CadPlot MCP bundle")) {
    $whatIfResult = [pscustomobject]@{
        Removed = $false
        WhatIf = $true
        BundlePath = $destinationBundle
        VerifiedFileCount = @($verification.Hashes).Count
        AutoCADLaunched = $false
        LivePublishProven = $false
    }
    if ($PassThru) { $whatIfResult }
    return
}

# Revalidate immediately before rename; a detected identity, file-set, or hash change aborts.
Assert-NoRedirectedPath -Path $destinationBundle
Assert-CadPlotBundleIdentity -BundlePath $destinationBundle
$finalVerification = & $verifier -BundlePath $destinationBundle -PassThru
$finalSignature = Get-BundleHashSignature -Verification $finalVerification
if ($finalVerification.Passed -ne $true -or $finalSignature -cne $initialSignature) {
    throw "CadPlot MCP bundle changed between verification and removal."
}

$quarantineName = ".cadplot-bundle-removing-{0}" -f [Guid]::NewGuid().ToString("N")
$quarantine = [System.IO.Path]::GetFullPath(
    (Join-Path $resolvedDestinationRoot $quarantineName)
)
if (
    (Split-Path -Parent $quarantine) -cne $resolvedDestinationRoot -or
    [System.IO.Path]::GetFileName($quarantine) -notmatch '^\.cadplot-bundle-removing-[0-9a-f]{32}$' -or
    (Test-Path -LiteralPath $quarantine)
) {
    throw "Bundle uninstall quarantine path is not safe and unique."
}

Move-Item -LiteralPath $destinationBundle -Destination $quarantine

# A failure after rename deliberately retains the non-.bundle quarantine for inspection.
$quarantineItem = Get-Item -LiteralPath $quarantine -Force
if (
    $quarantineItem.FullName -cne $quarantine -or
    ($quarantineItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0 -or
    (Split-Path -Parent $quarantineItem.FullName) -cne $resolvedDestinationRoot -or
    $quarantineItem.Name -notmatch '^\.cadplot-bundle-removing-[0-9a-f]{32}$'
) {
    throw "Renamed bundle uninstall quarantine failed its final path check."
}
$deletePath = Get-ExtendedLengthPath -Path $quarantine
[System.IO.Directory]::Delete($deletePath, $true)
if (Test-Path -LiteralPath $quarantine) {
    throw "Verified bundle uninstall quarantine remained after removal."
}

$result = [pscustomobject]@{
    Removed = $true
    WhatIf = $false
    BundlePath = $destinationBundle
    VerifiedFileCount = @($finalVerification.Hashes).Count
    QuarantineRemoved = $true
    AutoCADLaunched = $false
    LivePublishProven = $false
}
if ($PassThru) { $result }
else { Write-Output "Removed verified bundle: $destinationBundle" }
