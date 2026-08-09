[CmdletBinding()]
param(
    [string]$DotNet = "",
    [string]$AutoCADApiDir = "",
    [switch]$SkipSync
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$preflight = Join-Path $PSScriptRoot "run-local-preflight.ps1"

function Invoke-GitReadOnly {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)

    $output = @(& git @Arguments 2>&1)
    if ($LASTEXITCODE -ne 0) {
        throw "Git command failed: git $($Arguments -join ' ')`n$($output -join [Environment]::NewLine)"
    }
    return $output
}

Push-Location $repoRoot
try {
    $preflightParameters = @{
        DotNet = $DotNet
        AutoCADApiDir = $AutoCADApiDir
        SkipSync = $SkipSync
    }
    & $preflight @preflightParameters

    $commitLines = @(Invoke-GitReadOnly -Arguments @("rev-parse", "HEAD"))
    $commit = $commitLines[0].Trim()
    if ($commit -notmatch "^[0-9a-f]{40}$") {
        throw "Could not resolve an exact 40-character Git commit."
    }

    $worktreeChanges = @(Invoke-GitReadOnly -Arguments @(
        "status",
        "--porcelain=v1",
        "--untracked-files=all"
    ))
    if ($worktreeChanges.Count -ne 0) {
        throw "Demo rehearsal requires a clean worktree. Commit or intentionally remove local changes first."
    }

    $wheels = @(Get-ChildItem -LiteralPath (Join-Path $repoRoot "dist") -Filter "cadplot_mcp-*.whl" -File)
    if ($wheels.Count -ne 1) {
        throw "Expected exactly one cadplot-mcp wheel in dist; found $($wheels.Count)."
    }
    $wheel = $wheels[0]
    $wheelHash = (Get-FileHash -LiteralPath $wheel.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    $apiProbeRan = -not [string]::IsNullOrWhiteSpace($AutoCADApiDir)

    [ordered]@{
        passed = $true
        generated_utc = [DateTime]::UtcNow.ToString("o")
        exact_commit = $commit
        worktree_clean = $true
        wheel = $wheel.Name
        wheel_sha256 = $wheelHash
        local_demo_ready = $true
        licensed_live_pilot_ready = $false
        public_release_ready = $false
        api_probe_ran = $apiProbeRan
        autocad_launched = $false
        live_publish_proven = $false
        company_assets_copied = $false
        demo_runbook = "docs/pazartesi-demo-tr.md"
        live_blockers = @(
            "Licensed target AutoCAD workstation (2016 and/or 2025)",
            "Authorized representative DWG plus exact PC3/PMP/CTB/STB resources",
            "One-sheet visual comparison and retained receipt/audit evidence"
        )
    } | ConvertTo-Json -Depth 4
}
finally {
    Pop-Location
}
