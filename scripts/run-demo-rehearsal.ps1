[CmdletBinding()]
param(
    [string]$DotNet = "",
    [string]$AutoCADApiDir = "",
    [switch]$SkipSync,
    [switch]$WriteReport
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
    $initialCommitLines = @(Invoke-GitReadOnly -Arguments @("rev-parse", "HEAD"))
    $initialCommit = $initialCommitLines[0].Trim()
    if ($initialCommit -notmatch "^[0-9a-f]{40}$") {
        throw "Could not resolve an exact 40-character Git commit."
    }
    $initialWorktreeChanges = @(Invoke-GitReadOnly -Arguments @(
        "status",
        "--porcelain=v1",
        "--untracked-files=all"
    ))
    if ($initialWorktreeChanges.Count -ne 0) {
        throw "Demo rehearsal requires a clean worktree before checks begin."
    }

    $preflightParameters = @{
        DotNet = $DotNet
        AutoCADApiDir = $AutoCADApiDir
        SkipSync = $SkipSync
    }
    & $preflight @preflightParameters

    $commitLines = @(Invoke-GitReadOnly -Arguments @("rev-parse", "HEAD"))
    $commit = $commitLines[0].Trim()
    if ($commit -ne $initialCommit) {
        throw "Git HEAD changed while the demo rehearsal was running."
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

    $report = [ordered]@{
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
    }

    if ($WriteReport) {
        $reportPath = Join-Path `
            ([System.IO.Path]::GetTempPath()) `
            ("cadplot-demo-readiness-{0}.json" -f [Guid]::NewGuid().ToString("N"))
        $report["report_path"] = $reportPath
    }
    $reportJson = $report | ConvertTo-Json -Depth 4

    if ($WriteReport) {
        $bytes = [System.Text.UTF8Encoding]::new($false).GetBytes($reportJson)
        $stream = [System.IO.File]::Open(
            $reportPath,
            [System.IO.FileMode]::CreateNew,
            [System.IO.FileAccess]::Write,
            [System.IO.FileShare]::Read
        )
        try {
            $stream.Write($bytes, 0, $bytes.Length)
        }
        finally {
            $stream.Dispose()
        }
    }

    Write-Output $reportJson
}
finally {
    Pop-Location
}
