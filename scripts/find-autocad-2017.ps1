[CmdletBinding()]
param([string]$ApiDirectory = "")

$ErrorActionPreference = "Stop"
$checker = Join-Path $PSScriptRoot "check-autocad-api-series.ps1"
if (-not [string]::IsNullOrWhiteSpace($ApiDirectory)) {
    $identity = & $checker -AutoCADApiDir $ApiDirectory -ExpectedSeries "R21.0" -PassThru
    return $identity.ApiDirectory
}

# Only installation metadata and the standard product directory, never user drawings.
$candidates = @()
if (-not [string]::IsNullOrWhiteSpace($env:ProgramFiles)) {
    $candidates += Join-Path $env:ProgramFiles "Autodesk\AutoCAD 2017"
}
$registryRoot = "HKLM:\SOFTWARE\Autodesk\AutoCAD\R21.0"
if (Test-Path -LiteralPath $registryRoot) {
    foreach ($product in Get-ChildItem -LiteralPath $registryRoot) {
        $properties = Get-ItemProperty -LiteralPath $product.PSPath
        if (-not [string]::IsNullOrWhiteSpace([string]$properties.AcadLocation)) {
            $candidates += [string]$properties.AcadLocation
        }
    }
}
$matches = @()
foreach ($candidate in @($candidates | Select-Object -Unique)) {
    if (-not (Test-Path -LiteralPath $candidate -PathType Container)) { continue }
    try {
        $identity = & $checker -AutoCADApiDir $candidate -ExpectedSeries "R21.0" -PassThru
        $matches += $identity.ApiDirectory
    }
    catch {
        Write-Verbose "An installation candidate did not contain matching R21.0 managed references."
    }
}
$matches = @($matches | Select-Object -Unique)
if ($matches.Count -eq 1) { return $matches[0] }
if ($matches.Count -gt 1) {
    throw "Multiple AutoCAD 2017 installations found; select one with -AutoCAD2017SdkDir."
}
throw "AutoCAD 2017 managed references were not found on this computer. Run on the 2017 workstation, or supply -AutoCAD2017SdkDir with your authorized R21.0 SDK folder. No SDK is downloaded."
