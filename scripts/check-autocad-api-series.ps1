[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$AutoCADApiDir,

    [ValidatePattern('^R[0-9]{2}\.[0-9]$')]
    [string]$ExpectedSeries = "",

    [switch]$PassThru
)

$ErrorActionPreference = "Stop"
$resolvedApiDir = [System.IO.Path]::GetFullPath($AutoCADApiDir)
if (-not (Test-Path -LiteralPath $resolvedApiDir -PathType Container)) {
    throw "AutoCAD managed API directory does not exist: $resolvedApiDir"
}
$directoryItem = Get-Item -LiteralPath $resolvedApiDir -Force
if (($directoryItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw "AutoCAD managed API directory must not be a symlink or junction: $resolvedApiDir"
}

$assemblies = @()
foreach ($name in @("AcMgd.dll", "AcDbMgd.dll", "AcCoreMgd.dll")) {
    $path = Join-Path $resolvedApiDir $name
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "AutoCAD managed API directory is missing ${name}: $resolvedApiDir"
    }
    $item = Get-Item -LiteralPath $path -Force
    if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "AutoCAD managed API assembly must not be redirected: $path"
    }
    try {
        $assemblyName = [System.Reflection.AssemblyName]::GetAssemblyName($path)
    }
    catch {
        throw "AutoCAD managed API assembly identity is unreadable: $path"
    }
    $expectedName = [System.IO.Path]::GetFileNameWithoutExtension($name)
    if ($assemblyName.Name -ne $expectedName) {
        throw "AutoCAD managed API assembly identity mismatch for ${name}: $($assemblyName.Name)"
    }
    $assemblies += [pscustomobject]@{
        Name = $name
        AssemblyVersion = $assemblyName.Version.ToString()
        Series = "R$($assemblyName.Version.Major).$($assemblyName.Version.Minor)"
        Sha256 = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
    }
}

$seriesValues = @($assemblies | Select-Object -ExpandProperty Series -Unique)
if ($seriesValues.Count -ne 1) {
    throw "AutoCAD managed API assemblies do not share one release series: $($seriesValues -join ', ')"
}
$detectedSeries = $seriesValues[0]
if (
    -not [string]::IsNullOrWhiteSpace($ExpectedSeries) -and
    $detectedSeries -cne $ExpectedSeries
) {
    throw "AutoCAD managed API series mismatch: expected $ExpectedSeries, found $detectedSeries."
}

$result = [pscustomobject]@{
    Passed = $true
    ApiDirectory = $resolvedApiDir
    DetectedSeries = $detectedSeries
    ExpectedSeries = if ([string]::IsNullOrWhiteSpace($ExpectedSeries)) { $null } else { $ExpectedSeries }
    Assemblies = $assemblies
    AutoCADLaunched = $false
    LivePublishProven = $false
}
if ($PassThru) {
    $result
}
else {
    $result | ConvertTo-Json -Depth 4
}
