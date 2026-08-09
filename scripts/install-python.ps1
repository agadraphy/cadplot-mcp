[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = "Medium")]
param(
    [Parameter(Mandatory = $true)]
    [string]$ReleaseRoot,

    [string]$DestinationRoot = "",

    [string]$Uv = "",

    [string]$Python = "",

    [switch]$AllowProtocolOnlyFixture,

    [switch]$PassThru
)

$ErrorActionPreference = "Stop"

function Assert-NoRedirectedAncestor {
    param([Parameter(Mandatory = $true)][string]$Path)
    $current = [System.IO.Path]::GetFullPath($Path)
    while (-not (Test-Path -LiteralPath $current)) {
        $parent = Split-Path -Parent $current
        if ([string]::IsNullOrWhiteSpace($parent) -or $parent -eq $current) {
            throw "No existing ancestor was found for Python install target: $Path"
        }
        $current = $parent
    }
    while (-not [string]::IsNullOrWhiteSpace($current)) {
        $item = Get-Item -LiteralPath $current -Force
        if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Python install target must not pass through a symlink or junction: $current"
        }
        $parent = Split-Path -Parent $current
        if ([string]::IsNullOrWhiteSpace($parent) -or $parent -eq $current) { break }
        $current = $parent
    }
}

function Write-NewUtf8Json {
    param([string]$Path, $Value)
    $bytes = [System.Text.UTF8Encoding]::new($false).GetBytes(
        ($Value | ConvertTo-Json -Depth 8)
    )
    $stream = [System.IO.File]::Open(
        $Path,
        [System.IO.FileMode]::CreateNew,
        [System.IO.FileAccess]::Write,
        [System.IO.FileShare]::Read
    )
    try { $stream.Write($bytes, 0, $bytes.Length) }
    finally { $stream.Dispose() }
}

function Invoke-NativeQuiet {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$FailureMessage
    )
    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $FilePath @Arguments 2>&1 | Out-Null
        $exitCode = $LASTEXITCODE
    }
    finally { $ErrorActionPreference = $previousPreference }
    if ($exitCode -ne 0) { throw "$FailureMessage (exit $exitCode)" }
}

$resolvedRelease = [System.IO.Path]::GetFullPath($ReleaseRoot).TrimEnd('\')
$kitRoot = Join-Path $resolvedRelease "CadPlotMcp.release"
$releaseVerifier = Join-Path $kitRoot "scripts\verify-release-kit.ps1"
if (-not (Test-Path -LiteralPath $releaseVerifier -PathType Leaf)) {
    throw "Embedded release-kit verifier is missing."
}
$verifyArguments = @{ ReleaseRoot = $resolvedRelease; PassThru = $true }
if ($AllowProtocolOnlyFixture) { $verifyArguments.AllowProtocolOnlyFixture = $true }
$releaseEvidence = & $releaseVerifier @verifyArguments

$kitManifestPath = Join-Path $kitRoot "release-kit.json"
$kitManifest = Get-Content -LiteralPath $kitManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
$wheelPath = Join-Path $kitRoot ([string]$kitManifest.wheel.file).Replace('/', '\')
$kitPythonRoot = Join-Path $kitRoot "python"
$lockPath = Join-Path $kitPythonRoot "uv.lock"
$projectPath = Join-Path $kitPythonRoot "pyproject.toml"
if (-not (Test-Path -LiteralPath $wheelPath -PathType Leaf)) { throw "Verified wheel is missing." }

if ([string]::IsNullOrWhiteSpace($Uv)) {
    $uvCommand = Get-Command uv -ErrorAction SilentlyContinue
    if ($null -eq $uvCommand) { throw "uv is required; pass -Uv with its exact executable path." }
    $Uv = $uvCommand.Source
}
if ([string]::IsNullOrWhiteSpace($Python)) {
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($null -eq $pythonCommand) {
        throw "Python 3.11+ is required; pass -Python with its exact executable path."
    }
    $Python = $pythonCommand.Source
}
$resolvedUv = [System.IO.Path]::GetFullPath($Uv)
$resolvedPython = [System.IO.Path]::GetFullPath($Python)
foreach ($tool in @($resolvedUv, $resolvedPython)) {
    if (-not (Test-Path -LiteralPath $tool -PathType Leaf)) { throw "Install tool is missing: $tool" }
}

if ([string]::IsNullOrWhiteSpace($DestinationRoot)) {
    if ([string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
        throw "LOCALAPPDATA is unavailable; pass -DestinationRoot explicitly."
    }
    $DestinationRoot = Join-Path $env:LOCALAPPDATA "CadPlotMcp\python"
}
$resolvedDestination = [System.IO.Path]::GetFullPath($DestinationRoot).TrimEnd('\')
Assert-NoRedirectedAncestor -Path $resolvedDestination
$targetName = "{0}-{1}" -f $releaseEvidence.PackageVersion,$releaseEvidence.ExactCommit.Substring(0, 7)
$target = Join-Path $resolvedDestination $targetName
if (Test-Path -LiteralPath $target) {
    throw "Python install target already exists; installer never overwrites: $target"
}

if (-not $PSCmdlet.ShouldProcess($target, "Install verified CadPlot MCP Python environment")) {
    $whatIfResult = [pscustomobject]@{
        Installed = $false
        WhatIf = $true
        Target = $target
        ExactCommit = $releaseEvidence.ExactCommit
        PackageVersion = $releaseEvidence.PackageVersion
        AutoCADLaunched = $false
        LivePublishProven = $false
    }
    if ($PassThru) { $whatIfResult } else { $whatIfResult | ConvertTo-Json -Depth 3 }
    return
}

$null = New-Item -ItemType Directory -Path $resolvedDestination -Force
$staging = Join-Path $resolvedDestination (".cadplot-python-installing-{0}" -f [Guid]::NewGuid().ToString("N"))
$previousNoBytecode = $env:PYTHONDONTWRITEBYTECODE
$env:PYTHONDONTWRITEBYTECODE = "1"
try {
    $null = New-Item -ItemType Directory -Path $staging
    $evidenceRoot = Join-Path $staging "evidence"
    $null = New-Item -ItemType Directory -Path $evidenceRoot
    Copy-Item -LiteralPath $wheelPath -Destination $evidenceRoot
    Copy-Item -LiteralPath $lockPath -Destination $evidenceRoot
    Copy-Item -LiteralPath $projectPath -Destination $evidenceRoot

    $requirementsPath = Join-Path $staging "requirements.locked.txt"
    Invoke-NativeQuiet -FilePath $resolvedUv -Arguments @(
        "export", "--frozen", "--no-dev", "--no-emit-project", "--no-header",
        "--project", $kitPythonRoot, "--output-file", $requirementsPath
    ) -FailureMessage "Frozen dependency export failed."
    $requirementsHash = (Get-FileHash -LiteralPath $requirementsPath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($requirementsHash -cne [string]$kitManifest.dependency_audit.lock.requirements_sha256) {
        throw "Frozen dependency export does not match release audit evidence."
    }

    $venvRoot = Join-Path $staging "venv"
    Invoke-NativeQuiet -FilePath $resolvedUv -Arguments @(
        "venv", "--python", $resolvedPython, $venvRoot
    ) -FailureMessage "Isolated Python environment creation failed."
    $venvPython = Join-Path $venvRoot "Scripts\python.exe"
    Invoke-NativeQuiet -FilePath $resolvedUv -Arguments @(
        "pip", "install", "--python", $venvPython, "--require-hashes",
        "--requirements", $requirementsPath
    ) -FailureMessage "Hash-locked dependency installation failed."
    Invoke-NativeQuiet -FilePath $resolvedUv -Arguments @(
        "pip", "install", "--python", $venvPython, "--no-deps", $wheelPath
    ) -FailureMessage "Verified CadPlot wheel installation failed."
    Invoke-NativeQuiet -FilePath $resolvedUv -Arguments @(
        "pip", "check", "--python", $venvPython
    ) -FailureMessage "Installed Python dependency check failed."

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
    $inventoryScript = Join-Path $staging ".cadplot-inventory.py"
    [System.IO.File]::WriteAllText(
        $inventoryScript,
        $inventoryCode,
        [System.Text.UTF8Encoding]::new($false)
    )
    try {
        $inventoryText = @(& $venvPython -I $inventoryScript 2>&1) -join [Environment]::NewLine
        if ($LASTEXITCODE -ne 0) { throw "Installed CadPlot package could not be imported." }
    }
    finally {
        if (Test-Path -LiteralPath $inventoryScript -PathType Leaf) {
            Remove-Item -LiteralPath $inventoryScript -Force
        }
    }
    $inventory = $inventoryText | ConvertFrom-Json
    if ([string]$inventory.version -cne [string]$releaseEvidence.PackageVersion) {
        throw "Installed CadPlot package version is incorrect."
    }

    $commandRoot = Join-Path $staging "bin"
    $null = New-Item -ItemType Directory -Path $commandRoot
    $launchers = [ordered]@{
        "cadplot-mcp.cmd" = '-m cadplot_mcp'
        "cadplot-mcp-http.cmd" = '-m cadplot_mcp.http_server'
        "cadplot-doctor.cmd" = '-m cadplot_mcp.doctor'
        "cadplot-collect-pilot.cmd" = '-c "from cadplot_mcp.pilot_cli import collect_main; raise SystemExit(collect_main())"'
        "cadplot-assemble-pilot.cmd" = '-c "from cadplot_mcp.pilot_cli import assemble_main; raise SystemExit(assemble_main())"'
        "cadplot-validate-pilot.cmd" = '-c "from cadplot_mcp.pilot_cli import validate_main; raise SystemExit(validate_main())"'
        "cadplot-acceptance.cmd" = '-m cadplot_mcp.acceptance_cli'
        "cadplot-tunnel-preflight.cmd" = '-m cadplot_mcp.tunnel_preflight'
        "cadplot-chatgpt-eval.cmd" = '-m cadplot_mcp.chatgpt_eval_cli'
    }
    foreach ($launcher in $launchers.GetEnumerator()) {
        $content = '@"%~dp0..\venv\Scripts\python.exe" ' + $launcher.Value + ' %*' + "`r`n"
        [System.IO.File]::WriteAllText(
            (Join-Path $commandRoot $launcher.Key),
            $content,
            [System.Text.UTF8Encoding]::new($false)
        )
    }
    $commands = @($launchers.Keys | Sort-Object)

    $manifestPath = Join-Path $staging "python-install.json"
    Write-NewUtf8Json -Path $manifestPath -Value ([ordered]@{
        schema_version = 1
        exact_commit = $releaseEvidence.ExactCommit
        package_version = $releaseEvidence.PackageVersion
        installed_utc = [DateTime]::UtcNow.ToString("o")
        wheel_sha256 = (Get-FileHash -LiteralPath $wheelPath -Algorithm SHA256).Hash.ToLowerInvariant()
        lock_sha256 = (Get-FileHash -LiteralPath $lockPath -Algorithm SHA256).Hash.ToLowerInvariant()
        requirements_sha256 = $requirementsHash
        distributions = @($inventory.distributions)
        commands = $commands
        locked_dependencies = $true
        source_tree_imported = $false
        autocad_launched = $false
        live_publish_proven = $false
    })

    if (Test-Path -LiteralPath $target) {
        throw "Python install target appeared during staging; installer will not overwrite it."
    }
    Move-Item -LiteralPath $staging -Destination $target
    $verification = & (Join-Path $kitRoot "scripts\verify-python-install.ps1") `
        -InstallRoot $target -PassThru
    if ($verification.Passed -ne $true) { throw "Installed Python environment verification failed." }

    $result = [pscustomobject]@{
        Installed = $true
        WhatIf = $false
        Target = $target
        ExactCommit = $verification.ExactCommit
        PackageVersion = $verification.PackageVersion
        DistributionCount = $verification.DistributionCount
        LockedDependencies = $true
        SourceTreeImported = $false
        CommandRoot = (Join-Path $target "bin")
        AutoCADLaunched = $false
        LivePublishProven = $false
    }
    if ($PassThru) { $result } else { $result | ConvertTo-Json -Depth 3 }
}
finally {
    if ($null -eq $previousNoBytecode) { Remove-Item Env:PYTHONDONTWRITEBYTECODE -ErrorAction SilentlyContinue }
    else { $env:PYTHONDONTWRITEBYTECODE = $previousNoBytecode }
    # A failed staging directory is intentionally retained for explicit inspection.
}
