[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$AutoCADApiDir,

    [string]$DotNet = "dotnet",
    [string]$Configuration = "Release"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$project = Join-Path $repoRoot "src\dotnet\CadPlotMcp.AutoCADApiProbe\CadPlotMcp.AutoCADApiProbe.csproj"
$resolvedApiDir = [System.IO.Path]::GetFullPath($AutoCADApiDir)

foreach ($assembly in @("AcMgd.dll", "AcDbMgd.dll", "AcCoreMgd.dll")) {
    if (-not (Test-Path -LiteralPath (Join-Path $resolvedApiDir $assembly) -PathType Leaf)) {
        throw "AutoCAD API folder is missing $assembly in $resolvedApiDir"
    }
}

& $DotNet build $project --configuration $Configuration `
    "-p:AutoCADProbeDir=$resolvedApiDir"
if ($LASTEXITCODE -ne 0) { throw "AutoCAD API compile probe failed." }

Write-Output "Compile-only probe passed. AutoCAD was not launched."
