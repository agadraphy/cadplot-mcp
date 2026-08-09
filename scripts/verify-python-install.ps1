[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$InstallRoot,

    [switch]$PassThru
)

$ErrorActionPreference = "Stop"
$root = [System.IO.Path]::GetFullPath($InstallRoot).TrimEnd('\')
if (-not (Test-Path -LiteralPath $root -PathType Container)) {
    throw "Python installation does not exist: $root"
}

$allItems = @((Get-Item -LiteralPath $root -Force)) + @(
    Get-ChildItem -LiteralPath $root -Force -Recurse
)
foreach ($item in $allItems) {
    if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Python installation must not contain a symlink, junction, or redirected file: $($item.FullName)"
    }
}

$manifestPath = Join-Path $root "python-install.json"
$requirementsPath = Join-Path $root "requirements.locked.txt"
$evidenceRoot = Join-Path $root "evidence"
$venvRoot = Join-Path $root "venv"
$expectedTopLevel = @("bin", "evidence", "venv", "requirements.locked.txt", "python-install.json")
$actualTopLevel = @(Get-ChildItem -LiteralPath $root -Force | Select-Object -ExpandProperty Name)
if (
    $actualTopLevel.Count -ne $expectedTopLevel.Count -or
    @($expectedTopLevel | Where-Object { $_ -notin $actualTopLevel }).Count -ne 0
) {
    throw "Python installation top-level contents are not exact."
}
foreach ($path in @($manifestPath, $requirementsPath, $evidenceRoot, $venvRoot)) {
    if (-not (Test-Path -LiteralPath $path)) { throw "Python installation is incomplete: $path" }
}
$manifestItem = Get-Item -LiteralPath $manifestPath -Force
if ($manifestItem.Length -gt 1MB) { throw "Python install manifest exceeds 1 MiB." }
try { $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json }
catch { throw "Python install manifest is not valid UTF-8 JSON." }

$shaPattern = '^[0-9a-f]{64}$'
if (
    $manifest.schema_version -ne 1 -or
    [string]$manifest.exact_commit -notmatch '^[0-9a-f]{40}$' -or
    [string]$manifest.package_version -notmatch '^[0-9A-Za-z.]+$' -or
    [string]$manifest.wheel_sha256 -notmatch $shaPattern -or
    [string]$manifest.lock_sha256 -notmatch $shaPattern -or
    [string]$manifest.requirements_sha256 -notmatch $shaPattern -or
    $manifest.locked_dependencies -ne $true -or
    $manifest.source_tree_imported -ne $false -or
    $manifest.autocad_launched -ne $false -or
    $manifest.live_publish_proven -ne $false
) {
    throw "Python install manifest identity or safety fields are invalid."
}
$expectedRootName = "{0}-{1}" -f $manifest.package_version,$manifest.exact_commit.Substring(0, 7)
if ([System.IO.Path]::GetFileName($root) -cne $expectedRootName) {
    throw "Python install directory does not match its version/commit identity."
}

$evidenceFiles = @(Get-ChildItem -LiteralPath $evidenceRoot -File -Force)
$wheelFiles = @($evidenceFiles | Where-Object Name -like "cadplot_mcp-*-py3-none-any.whl")
if (
    $evidenceFiles.Count -ne 3 -or
    $wheelFiles.Count -ne 1 -or
    "uv.lock" -notin $evidenceFiles.Name -or
    "pyproject.toml" -notin $evidenceFiles.Name
) {
    throw "Python install evidence file set is not exact."
}
$wheelHash = (Get-FileHash -LiteralPath $wheelFiles[0].FullName -Algorithm SHA256).Hash.ToLowerInvariant()
$lockHash = (Get-FileHash -LiteralPath (Join-Path $evidenceRoot "uv.lock") -Algorithm SHA256).Hash.ToLowerInvariant()
$requirementsHash = (Get-FileHash -LiteralPath $requirementsPath -Algorithm SHA256).Hash.ToLowerInvariant()
if (
    $wheelHash -cne $manifest.wheel_sha256 -or
    $lockHash -cne $manifest.lock_sha256 -or
    $requirementsHash -cne $manifest.requirements_sha256
) {
    throw "Python installation evidence hash mismatch."
}

$pythonPath = Join-Path $venvRoot "Scripts\python.exe"
if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw "Python installation interpreter is missing."
}
$expectedCommands = @(
    "cadplot-assemble-pilot.cmd", "cadplot-collect-pilot.cmd", "cadplot-doctor.cmd",
    "cadplot-mcp-http.cmd", "cadplot-mcp.cmd", "cadplot-validate-pilot.cmd"
)
$commandRoot = Join-Path $root "bin"
foreach ($command in $expectedCommands) {
    if (-not (Test-Path -LiteralPath (Join-Path $commandRoot $command) -PathType Leaf)) {
        throw "Python installation command is missing: $command"
    }
}
if (
    @($manifest.commands).Count -ne $expectedCommands.Count -or
    @($expectedCommands | Where-Object { $_ -notin @($manifest.commands) }).Count -ne 0
) {
    throw "Python install command evidence is invalid."
}

$inventoryCode = @'
import importlib.metadata as metadata
import json
from pathlib import Path
import cadplot_mcp
items = sorted(
    ({"name": dist.metadata["Name"], "version": dist.version} for dist in metadata.distributions()),
    key=lambda item: item["name"].casefold(),
)
print(json.dumps({
    "version": metadata.version("cadplot-mcp"),
    "module": str(Path(cadplot_mcp.__file__).resolve()),
    "distributions": items,
}, separators=(",", ":")))
'@
$previousNoBytecode = $env:PYTHONDONTWRITEBYTECODE
$env:PYTHONDONTWRITEBYTECODE = "1"
$inventoryScript = Join-Path (
    [System.IO.Path]::GetTempPath()
) ("cadplot-installed-inventory-{0}.py" -f [Guid]::NewGuid().ToString("N"))
try {
    [System.IO.File]::WriteAllText(
        $inventoryScript,
        $inventoryCode,
        [System.Text.UTF8Encoding]::new($false)
    )
    $inventoryText = @(& $pythonPath -I $inventoryScript 2>&1) -join [Environment]::NewLine
    if ($LASTEXITCODE -ne 0) { throw "Installed CadPlot package could not be imported." }
}
finally {
    if (Test-Path -LiteralPath $inventoryScript -PathType Leaf) {
        Remove-Item -LiteralPath $inventoryScript -Force
    }
    if ($null -eq $previousNoBytecode) { Remove-Item Env:PYTHONDONTWRITEBYTECODE -ErrorAction SilentlyContinue }
    else { $env:PYTHONDONTWRITEBYTECODE = $previousNoBytecode }
}
try { $inventory = $inventoryText | ConvertFrom-Json }
catch { throw "Installed package inventory is not valid JSON." }
$modulePath = [System.IO.Path]::GetFullPath([string]$inventory.module)
$venvBoundary = [System.IO.Path]::GetFullPath($venvRoot).TrimEnd('\') + '\'
if (
    -not $modulePath.StartsWith($venvBoundary, [System.StringComparison]::OrdinalIgnoreCase) -or
    [string]$inventory.version -cne [string]$manifest.package_version -or
    (@($inventory.distributions) | ConvertTo-Json -Compress) -cne
        (@($manifest.distributions) | ConvertTo-Json -Compress)
) {
    throw "Installed package inventory no longer matches its manifest."
}

$previousNoBytecode = $env:PYTHONDONTWRITEBYTECODE
$env:PYTHONDONTWRITEBYTECODE = "1"
try {
    & (Join-Path $commandRoot "cadplot-doctor.cmd") --help 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Installed doctor launcher smoke failed." }
    & (Join-Path $commandRoot "cadplot-mcp-http.cmd") --help 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Installed HTTP launcher smoke failed." }
}
finally {
    if ($null -eq $previousNoBytecode) { Remove-Item Env:PYTHONDONTWRITEBYTECODE -ErrorAction SilentlyContinue }
    else { $env:PYTHONDONTWRITEBYTECODE = $previousNoBytecode }
}

$result = [pscustomobject]@{
    Passed = $true
    InstallRoot = $root
    ExactCommit = $manifest.exact_commit
    PackageVersion = $manifest.package_version
    DistributionCount = @($inventory.distributions).Count
    LockedDependencies = $true
    SourceTreeImported = $false
    AutoCADLaunched = $false
    LivePublishProven = $false
}
if ($PassThru) { $result }
else { $result | ConvertTo-Json -Depth 3 }
