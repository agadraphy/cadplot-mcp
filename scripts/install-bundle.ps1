[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = "Medium")]
param(
    [string]$SourceBundle = "",
    [string]$DestinationRoot = ""
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($SourceBundle)) {
    $SourceBundle = Join-Path $repoRoot "artifacts\CadPlotMcp.bundle"
}
if ([string]::IsNullOrWhiteSpace($DestinationRoot)) {
    $DestinationRoot = Join-Path ([Environment]::GetFolderPath("ApplicationData")) "Autodesk\ApplicationPlugins"
}

$resolvedSource = [System.IO.Path]::GetFullPath($SourceBundle)
$resolvedDestinationRoot = [System.IO.Path]::GetFullPath($DestinationRoot)
$destinationBundle = Join-Path $resolvedDestinationRoot "CadPlotMcp.bundle"

if (-not (Test-Path -LiteralPath $resolvedSource -PathType Container)) {
    throw "Bundle source does not exist: $resolvedSource"
}
if (-not (Test-Path -LiteralPath (Join-Path $resolvedSource "PackageContents.xml") -PathType Leaf)) {
    throw "Bundle source has no PackageContents.xml: $resolvedSource"
}
& (Join-Path $PSScriptRoot "verify-bundle.ps1") -BundlePath $resolvedSource
if (Test-Path -LiteralPath $destinationBundle) {
    throw "CadPlotMcp.bundle is already installed. This script will not overwrite it: $destinationBundle"
}

if ($PSCmdlet.ShouldProcess($destinationBundle, "Install CadPlot MCP bundle")) {
    New-Item -ItemType Directory -Path $resolvedDestinationRoot -Force | Out-Null
    Copy-Item -LiteralPath $resolvedSource -Destination $destinationBundle -Recurse
    Write-Output "Installed: $destinationBundle"
}
