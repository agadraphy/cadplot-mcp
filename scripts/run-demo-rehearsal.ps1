[CmdletBinding()]
param(
    [string]$DotNet = "",
    [string]$AutoCADApiDir = "",
    [string]$ReportPath = "",
    [switch]$SkipSync,
    [switch]$AuditDependencies,
    [switch]$WriteReport
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$preflight = Join-Path $PSScriptRoot "run-local-preflight.ps1"
$preflightSummaryPath = Join-Path `
    ([System.IO.Path]::GetTempPath()) `
    ("cadplot-preflight-summary-{0}.json" -f [Guid]::NewGuid().ToString("N"))

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
        SummaryPath = $preflightSummaryPath
        SkipSync = $SkipSync
        AuditDependencies = $AuditDependencies
    }
    & $preflight @preflightParameters
    if (-not (Test-Path -LiteralPath $preflightSummaryPath -PathType Leaf)) {
        throw "Preflight did not produce its machine-readable summary."
    }
    try {
        $preflightSummary = Get-Content `
            -LiteralPath $preflightSummaryPath `
            -Raw `
            -Encoding UTF8 | ConvertFrom-Json
    }
    catch { throw "Preflight summary is not valid UTF-8 JSON." }
    if (
        $preflightSummary.passed -ne $true -or
        $preflightSummary.synthetic_batch_rehearsal.target_drawings -ne 300 -or
        $preflightSummary.synthetic_batch_rehearsal.staged -ne 300 -or
        $preflightSummary.synthetic_batch_rehearsal.queue_capacity -ne 7 -or
        $preflightSummary.synthetic_batch_rehearsal.queue_waves -ne 45 -or
        $preflightSummary.synthetic_batch_rehearsal.queue_simulated_acceptances -ne 300 -or
        $preflightSummary.synthetic_batch_rehearsal.queue_deferred_results -ne 285 -or
        $preflightSummary.synthetic_batch_rehearsal.queue_exact_retry_identity_preserved -ne $true -or
        $preflightSummary.synthetic_batch_rehearsal.queue_status_batches -ne 15 -or
        $preflightSummary.synthetic_batch_rehearsal.queue_status_items -ne 300 -or
        $preflightSummary.synthetic_batch_rehearsal.queue_status_identity_preserved -ne $true -or
        $preflightSummary.synthetic_batch_rehearsal.queue_plugin_contacted -ne $false -or
        $preflightSummary.synthetic_batch_rehearsal.publish_verified -ne 0 -or
        $preflightSummary.wheel_install_smoke.passed -ne $true -or
        $preflightSummary.wheel_install_smoke.tool_count -ne 19 -or
        $preflightSummary.wheel_install_smoke.http_transport_tool_count -ne 19 -or
        $preflightSummary.wheel_install_smoke.http_transport_loopback_only -ne $true -or
        $preflightSummary.wheel_install_smoke.http_transport_header_guards -ne $true -or
        $preflightSummary.wheel_install_smoke.tunnel_preflight_redacted -ne $true -or
        $preflightSummary.wheel_install_smoke.tunnel_preflight_target_probed -ne $true -or
        [string]$preflightSummary.wheel_install_smoke.tunnel_preflight_tool_surface_sha256 `
            -notmatch '^[0-9a-f]{64}$' -or
        $preflightSummary.wheel_install_smoke.isolated_install -ne $true -or
        $preflightSummary.wheel_install_smoke.autocad_launched -ne $false -or
        $preflightSummary.wheel_install_smoke.live_tunnel_proven -ne $false -or
        $preflightSummary.wheel_install_smoke.live_publish_proven -ne $false
    ) {
        throw "Preflight summary lacks required local rehearsal or target-probe evidence."
    }
    if (
        -not [string]::IsNullOrWhiteSpace($AutoCADApiDir) -and (
            $preflightSummary.api_probe_ran -ne $true -or
            $preflightSummary.api_probe.passed -ne $true -or
            $preflightSummary.api_probe.evidence_scope -cne "compile-only" -or
            $preflightSummary.api_probe.autocad_launched -ne $false -or
            $preflightSummary.api_probe.live_publish_proven -ne $false
        )
    ) {
        throw "Preflight summary does not contain valid compile-only API evidence."
    }
    if (
        [string]::IsNullOrWhiteSpace($AutoCADApiDir) -and (
            $preflightSummary.api_probe_ran -ne $false -or
            $null -ne $preflightSummary.api_probe
        )
    ) {
        throw "Preflight summary unexpectedly contains API evidence."
    }
    if (
        $AuditDependencies -and (
            $preflightSummary.dependency_audit_ran -ne $true -or
            $preflightSummary.dependency_audit.passed -ne $true -or
            $preflightSummary.dependency_audit.python.vulnerability_count -ne 0 -or
            $preflightSummary.dependency_audit.python_license_inventory.package_count -ne
                $preflightSummary.dependency_audit.python.package_count -or
            $preflightSummary.dependency_audit.python_license_inventory.unknown_count -ne 0 -or
            @($preflightSummary.dependency_audit.python_license_inventory.packages).Count -ne
                $preflightSummary.dependency_audit.python.package_count -or
            $preflightSummary.dependency_audit.dotnet.vulnerability_count -ne 0 -or
            $preflightSummary.dependency_audit.dotnet.source_count -lt 1
        )
    ) {
        throw "Preflight summary does not contain valid dependency-audit evidence."
    }
    if (
        -not $AuditDependencies -and (
            $preflightSummary.dependency_audit_ran -ne $false -or
            $null -ne $preflightSummary.dependency_audit
        )
    ) {
        throw "Preflight summary unexpectedly contains dependency-audit evidence."
    }

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
    if ([string]$preflightSummary.wheel_install_smoke.wheel_sha256 -cne $wheelHash) {
        throw "Installed wheel smoke hash no longer matches the demo wheel."
    }
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
        api_probe = $preflightSummary.api_probe
        dependency_audit_ran = $preflightSummary.dependency_audit_ran
        dependency_audit = $preflightSummary.dependency_audit
        wheel_install_smoke = $preflightSummary.wheel_install_smoke
        autocad_launched = $false
        live_publish_proven = $false
        company_assets_copied = $false
        synthetic_batch_rehearsal = $preflightSummary.synthetic_batch_rehearsal
        demo_runbook = "docs/pazartesi-demo-tr.md"
        live_blockers = @(
            "Licensed target AutoCAD workstation (2016 and/or 2025)",
            "Authorized representative DWG plus exact PC3/PMP/CTB/STB resources",
            "One-sheet visual comparison and retained receipt/audit evidence"
        )
    }

    $writeReportRequested = $WriteReport -or -not [string]::IsNullOrWhiteSpace($ReportPath)
    if ($writeReportRequested) {
        if ([string]::IsNullOrWhiteSpace($ReportPath)) {
            $ReportPath = Join-Path `
                ([System.IO.Path]::GetTempPath()) `
                ("cadplot-demo-readiness-{0}.json" -f [Guid]::NewGuid().ToString("N"))
        }
        $resolvedReportPath = [System.IO.Path]::GetFullPath($ReportPath)
        if (Test-Path -LiteralPath $resolvedReportPath) {
            throw "Readiness report target already exists; evidence is never overwritten: $resolvedReportPath"
        }
        $reportParent = Split-Path -Parent $resolvedReportPath
        if (-not (Test-Path -LiteralPath $reportParent -PathType Container)) {
            throw "Readiness report parent does not exist: $reportParent"
        }
        $reportParentItem = Get-Item -LiteralPath $reportParent -Force
        if (($reportParentItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Readiness report parent must not be a symlink or junction: $reportParent"
        }
        $report["report_path"] = $resolvedReportPath
    }
    $reportJson = $report | ConvertTo-Json -Depth 6

    if ($writeReportRequested) {
        $bytes = [System.Text.UTF8Encoding]::new($false).GetBytes($reportJson)
        $stream = [System.IO.File]::Open(
            $resolvedReportPath,
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
    if (Test-Path -LiteralPath $preflightSummaryPath -PathType Leaf) {
        [System.IO.File]::Delete($preflightSummaryPath)
    }
}
