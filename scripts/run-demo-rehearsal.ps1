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
        $preflightSummary.synthetic_batch_rehearsal.orientation_mismatch_rejected -ne $true -or
        $preflightSummary.synthetic_batch_rehearsal.marking_content_verified -ne 300 -or
        $preflightSummary.synthetic_batch_rehearsal.blank_pdf_rejected -ne $true -or
        $preflightSummary.durable_queue_recovery.passed -ne $true -or
        $preflightSummary.durable_queue_recovery.exact_test_count -ne 15 -or
        $preflightSummary.durable_queue_recovery.pending_intent_recovered -ne $true -or
        $preflightSummary.durable_queue_recovery.exact_request_identity_preserved -ne $true -or
        $preflightSummary.durable_queue_recovery.interrupted_job_not_replayed -ne $true -or
        $preflightSummary.durable_queue_recovery.terminal_receipt_status_recovered -ne $true -or
        $preflightSummary.durable_queue_recovery.tampered_intent_blocked -ne $true -or
        $preflightSummary.durable_queue_recovery.completed_job_requeue_blocked -ne $true -or
        $preflightSummary.durable_queue_recovery.authentication_scheme -cne "windows-dpapi-current-user+hmac-sha256-v1" -or
        $preflightSummary.durable_queue_recovery.signed_intent_required -ne $true -or
        $preflightSummary.durable_queue_recovery.foreign_key_intent_blocked -ne $true -or
        $preflightSummary.durable_queue_recovery.started_marker_authentication_required -ne $true -or
        $preflightSummary.durable_queue_recovery.pending_cancellation_durable -ne $true -or
        $preflightSummary.durable_queue_recovery.cancelled_job_not_replayed -ne $true -or
        $preflightSummary.durable_queue_recovery.cancelled_marker_authentication_required -ne $true -or
        $preflightSummary.durable_queue_recovery.running_job_not_cancelled -ne $true -or
        $preflightSummary.durable_queue_recovery.protected_key_outside_workspace -ne $true -or
        $preflightSummary.durable_queue_recovery.workspace_key_rejected -ne $true -or
        $preflightSummary.durable_queue_recovery.corrupt_key_blocked -ne $true -or
        $preflightSummary.durable_queue_recovery.net45_dpapi_runtime_proven -ne $true -or
        $preflightSummary.durable_queue_recovery.net45_core_image_runtime -cne "v4.0.30319" -or
        $preflightSummary.durable_queue_recovery.autocad_launched -ne $false -or
        $preflightSummary.durable_queue_recovery.live_publish_proven -ne $false -or
        $preflightSummary.durable_queue_recovery.evidence_scope `
            -cne "production-core-net45+net8-with-synthetic-files" -or
        $preflightSummary.licensed_workstation_preflight_smoke.passed -ne $true -or
        $preflightSummary.licensed_workstation_preflight_smoke.autocad_2016_preflight -ne $true -or
        $preflightSummary.licensed_workstation_preflight_smoke.autocad_2025_preflight -ne $true -or
        $preflightSummary.licensed_workstation_preflight_smoke.autocad_2016_publish_session -ne $true -or
        $preflightSummary.licensed_workstation_preflight_smoke.autocad_2025_publish_session -ne $true -or
        $preflightSummary.licensed_workstation_preflight_smoke.no_overwrite -ne $true -or
        $preflightSummary.licensed_workstation_preflight_smoke.mcp_config_created -ne $true -or
        $preflightSummary.licensed_workstation_preflight_smoke.mcp_config_overwrite_blocked -ne $true -or
        $preflightSummary.licensed_workstation_preflight_smoke.mcp_read_only_publish_flag_absent -ne $true -or
        $preflightSummary.licensed_workstation_preflight_smoke.mcp_publish_flag_exact -ne $true -or
        $preflightSummary.licensed_workstation_preflight_smoke.publish_enabled_blocked -ne $true -or
        $preflightSummary.licensed_workstation_preflight_smoke.wrong_adapter_blocked -ne $true -or
        $preflightSummary.licensed_workstation_preflight_smoke.publish_without_preflight_blocked -ne $true -or
        $preflightSummary.licensed_workstation_preflight_smoke.tampered_read_only_preflight_blocked -ne $true -or
        $preflightSummary.licensed_workstation_preflight_smoke.unauthenticated_publish_session_blocked -ne $true -or
        $preflightSummary.licensed_workstation_preflight_smoke.autocad_launched -ne $false -or
        $preflightSummary.licensed_workstation_preflight_smoke.live_publish_proven -ne $false -or
        $preflightSummary.wheel_install_smoke.passed -ne $true -or
        $preflightSummary.wheel_install_smoke.tool_count -ne 20 -or
        $preflightSummary.wheel_install_smoke.http_transport_tool_count -ne 20 -or
        $preflightSummary.wheel_install_smoke.http_transport_loopback_only -ne $true -or
        $preflightSummary.wheel_install_smoke.http_transport_header_guards -ne $true -or
        $preflightSummary.wheel_install_smoke.tunnel_preflight_redacted -ne $true -or
        $preflightSummary.wheel_install_smoke.tunnel_preflight_target_probed -ne $true -or
        [string]$preflightSummary.wheel_install_smoke.tunnel_preflight_tool_surface_sha256 `
            -notmatch '^[0-9a-f]{64}$' -or
        $preflightSummary.wheel_install_smoke.chatgpt_eval_plan_prepared -ne $true -or
        $preflightSummary.wheel_install_smoke.chatgpt_eval_case_count -ne 13 -or
        $preflightSummary.wheel_install_smoke.sbom_cli_verified -ne $true -or
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
        durable_queue_recovery = $preflightSummary.durable_queue_recovery
        licensed_workstation_preflight_smoke = `
            $preflightSummary.licensed_workstation_preflight_smoke
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
