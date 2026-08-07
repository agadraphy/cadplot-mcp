[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$BundlePath
)

$ErrorActionPreference = "Stop"
$root = [System.IO.Path]::GetFullPath($BundlePath).TrimEnd('\')
if (-not (Test-Path -LiteralPath $root -PathType Container)) {
    throw "Bundle directory does not exist: $root"
}

$directories = @((Get-Item -LiteralPath $root)) + @(
    Get-ChildItem -LiteralPath $root -Directory -Recurse
)
foreach ($directory in $directories) {
    if (($directory.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Bundle must not contain a symlink or junction: $($directory.FullName)"
    }
}

$expected = @(
    "LICENSE",
    "PackageContents.xml",
    "Contents/Windows/2016/CadPlotMcp.AutoCAD2016.dll",
    "Contents/Windows/2016/CadPlotMcp.Core.dll",
    "Contents/Windows/2025/CadPlotMcp.AutoCAD2025.dll",
    "Contents/Windows/2025/CadPlotMcp.Core.dll"
)
$rootPrefix = $root + '\'
$actual = @(
    Get-ChildItem -LiteralPath $root -File -Recurse | ForEach-Object {
        if (-not $_.FullName.StartsWith($rootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Bundle file escaped its root: $($_.FullName)"
        }
        $_.FullName.Substring($rootPrefix.Length).Replace('\', '/')
    }
)
$missing = @($expected | Where-Object { $_ -notin $actual })
$unexpected = @($actual | Where-Object { $_ -notin $expected })
if ($missing.Count -gt 0) {
    throw "Bundle is missing required files: $($missing -join ', ')"
}
if ($unexpected.Count -gt 0) {
    throw "Bundle contains unexpected files: $($unexpected -join ', ')"
}

$manifestPath = Join-Path $root "PackageContents.xml"
[xml]$manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8
$package = $manifest.ApplicationPackage
if ($package.AutodeskProduct -ne "AutoCAD" -or $package.Name -ne "CadPlot MCP") {
    throw "PackageContents.xml does not identify the expected CadPlot AutoCAD package."
}
$expectedModules = @(
    "Contents/Windows/2016/CadPlotMcp.AutoCAD2016.dll",
    "Contents/Windows/2025/CadPlotMcp.AutoCAD2025.dll"
)
$modules = @(
    $package.Components.ComponentEntry | ForEach-Object {
        ([string]$_.ModuleName).TrimStart('.', '/', '\').Replace('\', '/')
    }
)
if (@($expectedModules | Where-Object { $_ -notin $modules }).Count -gt 0) {
    throw "PackageContents.xml does not route both supported adapter modules."
}

$dllPaths = @($expected | Where-Object { $_.EndsWith('.dll') })
foreach ($relative in $dllPaths) {
    $path = Join-Path $root $relative.Replace('/', '\')
    try {
        $assemblyName = [System.Reflection.AssemblyName]::GetAssemblyName($path).Name
    }
    catch {
        throw "Bundle DLL is not a readable managed assembly: $relative"
    }
    $expectedAssembly = [System.IO.Path]::GetFileNameWithoutExtension($relative)
    if ($assemblyName -ne $expectedAssembly) {
        throw "Bundle assembly identity mismatch for ${relative}: $assemblyName"
    }
}

$hashes = foreach ($relative in $expected) {
    $path = Join-Path $root $relative.Replace('/', '\')
    [pscustomobject]@{
        Path = $relative
        Sha256 = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
    }
}
Write-Output "Verified CadPlot MCP bundle: $root"
$hashes
