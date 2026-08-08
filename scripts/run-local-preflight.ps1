[CmdletBinding()]
param(
    [string]$DotNet = "",
    [string]$AutoCADApiDir = "",
    [switch]$SkipSync
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot

function Invoke-CheckedStep {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][scriptblock]$Action
    )
    Write-Output "PRECHECK: $Name"
    & $Action
    if ($LASTEXITCODE -ne 0) {
        throw "Preflight step failed: $Name (exit $LASTEXITCODE)"
    }
}

if ([string]::IsNullOrWhiteSpace($DotNet)) {
    $userDotNet = Join-Path $env:USERPROFILE ".dotnet\dotnet.exe"
    if (Test-Path -LiteralPath $userDotNet -PathType Leaf) {
        $DotNet = $userDotNet
    }
    else {
        $command = Get-Command dotnet -ErrorAction SilentlyContinue
        if ($null -eq $command) {
            throw "No .NET SDK command found. Pass -DotNet with an explicit dotnet.exe path."
        }
        $DotNet = $command.Source
    }
}

$resolvedDotNet = [System.IO.Path]::GetFullPath($DotNet)
if (-not (Test-Path -LiteralPath $resolvedDotNet -PathType Leaf)) {
    throw "DotNet executable does not exist: $resolvedDotNet"
}

Push-Location $repoRoot
try {
    if (-not $SkipSync) {
        Invoke-CheckedStep "locked Python environment" {
            uv sync --frozen --extra dev --extra autocad
        }
    }
    Invoke-CheckedStep "source tree proprietary asset and secret audit" {
        uv run python scripts\audit-source-tree.py
    }
    Invoke-CheckedStep "Python lint" { uv run ruff check . }
    Invoke-CheckedStep "Python tests" { uv run pytest -q }
    Invoke-CheckedStep "real MCP stdio protocol smoke" {
        uv run python scripts\smoke-mcp-stdio.py
    }
    Invoke-CheckedStep "synthetic non-AutoCAD workflow" {
        uv run python scripts\run-synthetic-demo.py
    }
    Invoke-CheckedStep "Python release build" { uv build }
    Invoke-CheckedStep "release artifact audit" {
        uv run python scripts\audit-release-artifacts.py dist
    }
    Invoke-CheckedStep "isolated wheel install and MCP smoke" {
        uv run python scripts\smoke-wheel-install.py dist
    }
    Invoke-CheckedStep ".NET protocol build" {
        & $resolvedDotNet build src\dotnet\CadPlotMcp.sln --configuration Release --nologo
    }
    Invoke-CheckedStep ".NET protocol tests" {
        & $resolvedDotNet test `
            src\dotnet\CadPlotMcp.Core.Tests\CadPlotMcp.Core.Tests.csproj `
            --configuration Release --no-build --nologo
    }

    $apiProbeRan = $false
    if (-not [string]::IsNullOrWhiteSpace($AutoCADApiDir)) {
        $resolvedApiDir = [System.IO.Path]::GetFullPath($AutoCADApiDir)
        Invoke-CheckedStep "compile-only installed AutoCAD API probe" {
            & (Join-Path $PSScriptRoot "probe-autocad-api.ps1") `
                -AutoCADApiDir $resolvedApiDir `
                -DotNet $resolvedDotNet
        }
        $apiProbeRan = $true
    }

    [ordered]@{
        passed = $true
        repository = $repoRoot
        dotnet = $resolvedDotNet
        api_probe_ran = $apiProbeRan
        autocad_launched = $false
        live_publish_proven = $false
    } | ConvertTo-Json
}
finally {
    Pop-Location
}
