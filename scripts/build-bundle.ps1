[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$AutoCAD2016SdkDir,

    [Parameter(Mandatory = $true)]
    [string]$AutoCAD2025SdkDir,

    [string]$DotNet = "dotnet",
    [string]$Configuration = "Release"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$dotnetRoot = Join-Path $repoRoot "src\dotnet"
$templateBundle = Join-Path $repoRoot "bundle\CadPlotMcp.bundle"
$artifactsRoot = Join-Path $repoRoot "artifacts"
$outputBundle = Join-Path $artifactsRoot "CadPlotMcp.bundle"
$resolvedRepoRoot = [System.IO.Path]::GetFullPath($repoRoot).TrimEnd('\') + '\'
$resolvedArtifactsRoot = [System.IO.Path]::GetFullPath($artifactsRoot).TrimEnd('\') + '\'
if (-not $resolvedArtifactsRoot.StartsWith($resolvedRepoRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Artifacts directory escaped the repository root."
}

foreach ($sdk in @(
    @{ Name = "AutoCAD 2016"; Path = $AutoCAD2016SdkDir },
    @{ Name = "AutoCAD 2025"; Path = $AutoCAD2025SdkDir }
)) {
    $resolved = [System.IO.Path]::GetFullPath($sdk.Path)
    foreach ($assembly in @("AcMgd.dll", "AcDbMgd.dll", "AcCoreMgd.dll")) {
        if (-not (Test-Path -LiteralPath (Join-Path $resolved $assembly) -PathType Leaf)) {
            throw "$($sdk.Name) SDK is missing $assembly in $resolved"
        }
    }
}

$project2016 = Join-Path $dotnetRoot "CadPlotMcp.AutoCAD2016\CadPlotMcp.AutoCAD2016.csproj"
$project2025 = Join-Path $dotnetRoot "CadPlotMcp.AutoCAD2025\CadPlotMcp.AutoCAD2025.csproj"

& $DotNet build $project2016 --configuration $Configuration `
    "-p:BuildAutoCADPlugin=true" "-p:AutoCAD2016SdkDir=$AutoCAD2016SdkDir"
if ($LASTEXITCODE -ne 0) { throw "AutoCAD 2016 plug-in build failed." }

& $DotNet build $project2025 --configuration $Configuration `
    "-p:BuildAutoCADPlugin=true" "-p:AutoCAD2025SdkDir=$AutoCAD2025SdkDir"
if ($LASTEXITCODE -ne 0) { throw "AutoCAD 2025 plug-in build failed." }

if (Test-Path -LiteralPath $outputBundle) {
    Remove-Item -LiteralPath $outputBundle -Recurse -Force
}
New-Item -ItemType Directory -Path $outputBundle | Out-Null
Copy-Item -LiteralPath (Join-Path $templateBundle "PackageContents.xml") -Destination $outputBundle
Copy-Item -LiteralPath (Join-Path $repoRoot "LICENSE") -Destination $outputBundle

$dest2016 = Join-Path $outputBundle "Contents\Windows\2016"
$dest2025 = Join-Path $outputBundle "Contents\Windows\2025"
New-Item -ItemType Directory -Path $dest2016,$dest2025 | Out-Null

$bin2016 = Join-Path $dotnetRoot "CadPlotMcp.AutoCAD2016\bin\$Configuration\net45"
$bin2025 = Join-Path $dotnetRoot "CadPlotMcp.AutoCAD2025\bin\$Configuration\net8.0-windows"
Copy-Item -LiteralPath (Join-Path $bin2016 "CadPlotMcp.AutoCAD2016.dll") -Destination $dest2016
Copy-Item -LiteralPath (Join-Path $bin2016 "CadPlotMcp.Core.dll") -Destination $dest2016
Copy-Item -LiteralPath (Join-Path $bin2025 "CadPlotMcp.AutoCAD2025.dll") -Destination $dest2025
Copy-Item -LiteralPath (Join-Path $bin2025 "CadPlotMcp.Core.dll") -Destination $dest2025

$zipPath = Join-Path $artifactsRoot "CadPlotMcp.bundle.zip"
if (Test-Path -LiteralPath $zipPath) {
    Remove-Item -LiteralPath $zipPath -Force
}
Compress-Archive -LiteralPath $outputBundle -DestinationPath $zipPath -CompressionLevel Optimal
Write-Output "Bundle: $outputBundle"
Write-Output "Archive: $zipPath"
