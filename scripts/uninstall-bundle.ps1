[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = "High")]
param(
    [string]$DestinationRoot = ""
)

$ErrorActionPreference = "Stop"
if ([string]::IsNullOrWhiteSpace($DestinationRoot)) {
    $DestinationRoot = Join-Path ([Environment]::GetFolderPath("ApplicationData")) "Autodesk\ApplicationPlugins"
}

$resolvedDestinationRoot = [System.IO.Path]::GetFullPath($DestinationRoot)
$destinationBundle = Join-Path $resolvedDestinationRoot "CadPlotMcp.bundle"
if (-not (Test-Path -LiteralPath $destinationBundle -PathType Container)) {
    throw "CadPlot MCP bundle is not installed at: $destinationBundle"
}

$current = Get-Item -LiteralPath $destinationBundle
while ($null -ne $current) {
    if (($current.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Refusing to remove a bundle through a symlink or junction: $($current.FullName)"
    }
    $current = $current.Parent
}

$manifestPath = Join-Path $destinationBundle "PackageContents.xml"
if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
    throw "Refusing to remove an unrecognized directory without PackageContents.xml."
}
[xml]$manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8
$package = $manifest.ApplicationPackage
$expectedProductCode = "{C2E79B66-6076-40D4-AE45-E725A644B288}"
if ($package.Name -ne "CadPlot MCP" -or $package.ProductCode -ne $expectedProductCode) {
    throw "Refusing to remove a directory that is not the expected CadPlot MCP package."
}
$null = & (Join-Path $PSScriptRoot "verify-bundle.ps1") `
    -BundlePath $destinationBundle `
    -PassThru

if ($PSCmdlet.ShouldProcess($destinationBundle, "Remove verified CadPlot MCP bundle")) {
    Remove-Item -LiteralPath $destinationBundle -Recurse -Force
    Write-Output "Removed: $destinationBundle"
}
