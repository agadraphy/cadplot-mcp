[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = "High")]
param(
    [Parameter(Mandatory = $true)]
    [string]$InstallRoot,

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
                throw "Python uninstall target must not pass through a symlink or junction: $current"
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

if ([string]::IsNullOrWhiteSpace($InstallRoot)) {
    throw "InstallRoot must be an explicit CadPlot Python installation path."
}
$root = [System.IO.Path]::GetFullPath($InstallRoot).TrimEnd('\')
$volumeRoot = [System.IO.Path]::GetPathRoot($root).TrimEnd('\')
$parent = Split-Path -Parent $root
if (
    [string]::IsNullOrWhiteSpace($root) -or
    [string]::IsNullOrWhiteSpace($parent) -or
    $root -ieq $volumeRoot -or
    $root -ieq $parent
) {
    throw "Refusing to remove a drive root or broad Python install path."
}
if (-not (Test-Path -LiteralPath $root -PathType Container)) {
    throw "Python installation does not exist: $root"
}
Assert-NoRedirectedPath -Path $root

$manifestPath = Join-Path $root "python-install.json"
if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
    throw "Refusing to remove an unrecognized directory without python-install.json."
}
$manifestHashBefore = (Get-FileHash -LiteralPath $manifestPath -Algorithm SHA256).Hash
$verifier = Join-Path $PSScriptRoot "verify-python-install.ps1"
$verification = & $verifier -InstallRoot $root -PassThru
if ($verification.Passed -ne $true) {
    throw "Refusing to remove a Python installation that did not verify."
}
$manifestHashAfter = (Get-FileHash -LiteralPath $manifestPath -Algorithm SHA256).Hash
if ($manifestHashAfter -cne $manifestHashBefore) {
    throw "Python install manifest changed during verification."
}

$expectedName = "{0}-{1}" -f $verification.PackageVersion,$verification.ExactCommit.Substring(0, 7)
if ([System.IO.Path]::GetFileName($root) -cne $expectedName) {
    throw "Python install directory does not match its verified version/commit identity."
}

if (-not $PSCmdlet.ShouldProcess($root, "Remove verified CadPlot MCP Python environment")) {
    $whatIfResult = [pscustomobject]@{
        Removed = $false
        WhatIf = $true
        InstallRoot = $root
        ExactCommit = $verification.ExactCommit
        PackageVersion = $verification.PackageVersion
        AutoCADLaunched = $false
        LivePublishProven = $false
    }
    if ($PassThru) { $whatIfResult } else { $whatIfResult | ConvertTo-Json -Depth 3 }
    return
}

# Verify again immediately before the atomic rename so a modified install is never removed.
$finalVerification = & $verifier -InstallRoot $root -PassThru
if ($finalVerification.Passed -ne $true) {
    throw "Python installation no longer verifies immediately before removal."
}
$manifestHashFinal = (Get-FileHash -LiteralPath $manifestPath -Algorithm SHA256).Hash
if ($manifestHashFinal -cne $manifestHashBefore) {
    throw "Python install manifest changed before removal."
}
Assert-NoRedirectedPath -Path $root

$quarantineName = ".cadplot-python-removing-{0}" -f [Guid]::NewGuid().ToString("N")
$quarantine = [System.IO.Path]::GetFullPath((Join-Path $parent $quarantineName))
if (
    (Split-Path -Parent $quarantine) -cne $parent -or
    [System.IO.Path]::GetFileName($quarantine) -notmatch '^\.cadplot-python-removing-[0-9a-f]{32}$' -or
    (Test-Path -LiteralPath $quarantine)
) {
    throw "Python uninstall quarantine path is not safe and unique."
}

Move-Item -LiteralPath $root -Destination $quarantine

# From this point on, failure deliberately retains the non-loadable quarantine for inspection.
$quarantineItem = Get-Item -LiteralPath $quarantine -Force
if (
    $quarantineItem.FullName -cne $quarantine -or
    ($quarantineItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0 -or
    (Split-Path -Parent $quarantineItem.FullName) -cne $parent -or
    $quarantineItem.Name -notmatch '^\.cadplot-python-removing-[0-9a-f]{32}$'
) {
    throw "Renamed Python uninstall quarantine failed its final path check."
}
$deletePath = Get-ExtendedLengthPath -Path $quarantine
[System.IO.Directory]::Delete($deletePath, $true)
if (Test-Path -LiteralPath $quarantine) {
    throw "Verified Python uninstall quarantine remained after removal."
}

$result = [pscustomobject]@{
    Removed = $true
    WhatIf = $false
    InstallRoot = $root
    ExactCommit = $finalVerification.ExactCommit
    PackageVersion = $finalVerification.PackageVersion
    AutoCADLaunched = $false
    LivePublishProven = $false
}
if ($PassThru) { $result } else { $result | ConvertTo-Json -Depth 3 }
