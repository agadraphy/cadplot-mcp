[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$AutoCADApiDir,

    [string]$DotNet = "dotnet",
    [string]$Configuration = "Release",

    [switch]$PassThru
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$project = Join-Path $repoRoot "src\dotnet\CadPlotMcp.AutoCADApiProbe\CadPlotMcp.AutoCADApiProbe.csproj"
$resolvedApiDir = [System.IO.Path]::GetFullPath($AutoCADApiDir)
$checker = Join-Path $PSScriptRoot "check-autocad-api-series.ps1"

$identity = & $checker -AutoCADApiDir $resolvedApiDir -PassThru
$targetFrameworks = @{
    "R20.1" = "net45"
    "R21.0" = "net48"
    "R22.0" = "net48"
    "R23.0" = "net48"
    "R23.1" = "net48"
    "R24.0" = "net48"
    "R24.1" = "net48"
    "R24.2" = "net48"
    "R24.3" = "net48"
    "R25.0" = "net8.0-windows"
}
$targetFramework = $targetFrameworks[[string]$identity.DetectedSeries]
if ([string]::IsNullOrWhiteSpace($targetFramework)) {
    throw "No compile-probe framework is approved for AutoCAD API series $($identity.DetectedSeries)."
}

$buildOutput = @(& $DotNet build $project --configuration $Configuration `
    --framework $targetFramework `
    "-p:AutoCADProbeDir=$resolvedApiDir" 2>&1)
if ($LASTEXITCODE -ne 0) { throw "AutoCAD API compile probe failed." }
foreach ($line in $buildOutput) { Write-Host $line }

$result = [pscustomobject][ordered]@{
    passed = $true
    api_directory = $identity.ApiDirectory
    detected_series = $identity.DetectedSeries
    target_framework = $targetFramework
    assemblies = $identity.Assemblies
    autocad_launched = $false
    live_publish_proven = $false
    evidence_scope = "compile-only"
}
if ($PassThru) { $result }
else { $result | ConvertTo-Json -Depth 5 }
