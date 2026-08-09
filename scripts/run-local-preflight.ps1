[CmdletBinding()]
param(
    [string]$DotNet = "",
    [string]$AutoCADApiDir = "",
    [string]$SummaryPath = "",
    [switch]$SkipSync,
    [switch]$AuditDependencies
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
    $dependencyAuditRan = $false
    $dependencyAuditEvidence = $null
    if ($AuditDependencies) {
        Write-Output "PRECHECK: locked Python and .NET dependency vulnerability audit"
        $dependencyAuditOutput = @(
            & uv run python scripts\audit-dependencies.py --dotnet $resolvedDotNet 2>&1
        )
        if ($LASTEXITCODE -ne 0) {
            throw "Dependency vulnerability audit failed.`n$($dependencyAuditOutput -join [Environment]::NewLine)"
        }
        $dependencyAuditJson = $dependencyAuditOutput -join [Environment]::NewLine
        try { $dependencyAuditEvidence = $dependencyAuditJson | ConvertFrom-Json }
        catch { throw "Dependency vulnerability audit did not return valid JSON evidence." }
        $lockHash = (Get-FileHash -LiteralPath (Join-Path $repoRoot "uv.lock") -Algorithm SHA256).Hash.ToLowerInvariant()
        if (
            $dependencyAuditEvidence.passed -ne $true -or
            $dependencyAuditEvidence.lock.file -cne "uv.lock" -or
            $dependencyAuditEvidence.lock.sha256 -cne $lockHash -or
            $dependencyAuditEvidence.python.package_count -lt 1 -or
            $dependencyAuditEvidence.python.vulnerability_count -ne 0 -or
            $dependencyAuditEvidence.python_license_inventory.package_count -ne
                $dependencyAuditEvidence.python.package_count -or
            $dependencyAuditEvidence.python_license_inventory.unknown_count -ne 0 -or
            @($dependencyAuditEvidence.python_license_inventory.packages).Count -ne
                $dependencyAuditEvidence.python.package_count -or
            $dependencyAuditEvidence.dotnet.project_count -lt 4 -or
            $dependencyAuditEvidence.dotnet.vulnerability_count -ne 0 -or
            $dependencyAuditEvidence.dotnet.source_count -lt 1 -or
            $dependencyAuditEvidence.network_database_check -ne $true -or
            $dependencyAuditEvidence.autocad_launched -ne $false -or
            $dependencyAuditEvidence.live_publish_proven -ne $false
        ) {
            throw "Dependency vulnerability audit crossed a required evidence boundary."
        }
        $licensePackages = @($dependencyAuditEvidence.python_license_inventory.packages)
        if (@($licensePackages | Group-Object -Property name | Where-Object Count -ne 1).Count -ne 0) {
            throw "Dependency license inventory contains duplicate package names."
        }
        foreach ($package in $licensePackages) {
            if (
                [string]::IsNullOrWhiteSpace([string]$package.name) -or
                [string]::IsNullOrWhiteSpace([string]$package.version) -or
                [string]::IsNullOrWhiteSpace([string]$package.license) -or
                [string]$package.license -ceq "UNKNOWN"
            ) {
                throw "Dependency license inventory contains an incomplete declaration."
            }
        }
        Write-Output $dependencyAuditJson
        $dependencyAuditRan = $true
    }
    Invoke-CheckedStep "Python lint" { uv run ruff check . }
    Invoke-CheckedStep "Python tests" { uv run pytest -q }
    Invoke-CheckedStep "real MCP stdio protocol smoke" {
        uv run python scripts\smoke-mcp-stdio.py
    }
    Invoke-CheckedStep "synthetic non-AutoCAD workflow" {
        uv run python scripts\run-synthetic-demo.py
    }
    Write-Output "PRECHECK: 300-drawing bounded synthetic batch workflow"
    $batchOutput = @(& uv run python scripts\run-synthetic-batch-demo.py --drawings 300 2>&1)
    if ($LASTEXITCODE -ne 0) {
        throw "Preflight step failed: 300-drawing bounded synthetic batch workflow (exit $LASTEXITCODE)"
    }
    $batchJson = $batchOutput -join [Environment]::NewLine
    Write-Output $batchJson
    try { $batchResult = $batchJson | ConvertFrom-Json }
    catch { throw "Synthetic batch workflow did not return valid JSON evidence." }
    if (
        $batchResult.synthetic -ne $true -or
        $batchResult.target_drawings -ne 300 -or
        $batchResult.planning.ready -ne 300 -or
        $batchResult.staging.staged -ne 300 -or
        $batchResult.queue_protocol_rehearsal.queue_capacity -ne 7 -or
        $batchResult.queue_protocol_rehearsal.waves -ne 45 -or
        $batchResult.queue_protocol_rehearsal.approvals -ne 300 -or
        $batchResult.queue_protocol_rehearsal.simulated_acceptances -ne 300 -or
        $batchResult.queue_protocol_rehearsal.deferred_results -ne 285 -or
        $batchResult.queue_protocol_rehearsal.pipe_attempts -ne 330 -or
        $batchResult.queue_protocol_rehearsal.exact_retry_identity_preserved -ne $true -or
        $batchResult.queue_protocol_rehearsal.status_batches -ne 15 -or
        $batchResult.queue_protocol_rehearsal.status_items -ne 300 -or
        $batchResult.queue_protocol_rehearsal.status_pending -ne 300 -or
        $batchResult.queue_protocol_rehearsal.status_identity_preserved -ne $true -or
        $batchResult.queue_protocol_rehearsal.plugin_contacted -ne $false -or
        $batchResult.output_audit.outputs_complete -ne 300 -or
        $batchResult.output_audit.execution_verified -ne 0 -or
        $batchResult.output_audit.publish_verified -ne 0 -or
        $batchResult.restart_report_after_outputs.manual_review -ne 300 -or
        $batchResult.restart_report_after_outputs.complete -ne 0 -or
        $batchResult.source_unchanged -ne $true -or
        $batchResult.autocad_launched -ne $false -or
        $batchResult.live_publish_proven -ne $false -or
        [string]$batchResult.evidence_digest -notmatch '^sha256:[0-9a-f]{64}$'
    ) {
        throw "Synthetic batch workflow evidence crossed a required safety or scale boundary."
    }
    Invoke-CheckedStep "Python release build" { uv build }
    Invoke-CheckedStep "release artifact audit" {
        uv run python scripts\audit-release-artifacts.py dist
    }
    Write-Output "PRECHECK: isolated wheel install and MCP smoke"
    $wheelSmokeOutput = @(& uv run python scripts\smoke-wheel-install.py dist 2>&1)
    if ($LASTEXITCODE -ne 0) {
        throw "Isolated wheel smoke failed.`n$($wheelSmokeOutput -join [Environment]::NewLine)"
    }
    try { $wheelSmoke = ($wheelSmokeOutput -join [Environment]::NewLine) | ConvertFrom-Json }
    catch { throw "Isolated wheel smoke did not return valid JSON evidence." }
    if (
        $wheelSmoke.passed -ne $true -or
        $wheelSmoke.tool_count -ne 19 -or
        $wheelSmoke.http_transport_tool_count -ne 19 -or
        $wheelSmoke.http_transport_loopback_only -ne $true -or
        $wheelSmoke.http_transport_header_guards -ne $true -or
        $wheelSmoke.tunnel_preflight_redacted -ne $true -or
        $wheelSmoke.tunnel_preflight_target_probed -ne $true -or
        [string]$wheelSmoke.tunnel_preflight_tool_surface_sha256 -notmatch '^[0-9a-f]{64}$' -or
        $wheelSmoke.isolated_install -ne $true -or
        $wheelSmoke.locked_dependencies -ne $true -or
        $wheelSmoke.dependency_hashes_required -ne $true
    ) {
        throw "Isolated wheel smoke crossed a required MCP or tunnel-preflight boundary."
    }
    $wheelSmoke | ConvertTo-Json -Depth 4
    Invoke-CheckedStep "self-verifying path-redacted demo-kit smoke" {
        & (Join-Path $PSScriptRoot "smoke-demo-kit.ps1")
    }
    Invoke-CheckedStep ".NET protocol build" {
        & $resolvedDotNet build src\dotnet\CadPlotMcp.sln --configuration Release --nologo
    }
    Invoke-CheckedStep ".NET protocol tests" {
        & $resolvedDotNet test `
            src\dotnet\CadPlotMcp.Core.Tests\CadPlotMcp.Core.Tests.csproj `
            --configuration Release --no-build --nologo
    }
    Invoke-CheckedStep "transactional bundle install/uninstall smoke" {
        & (Join-Path $PSScriptRoot "smoke-bundle-install.ps1")
    }

    $apiProbeRan = $false
    $apiProbeEvidence = $null
    if (-not [string]::IsNullOrWhiteSpace($AutoCADApiDir)) {
        $resolvedApiDir = [System.IO.Path]::GetFullPath($AutoCADApiDir)
        Invoke-CheckedStep "installed AutoCAD API series identity" {
            & (Join-Path $PSScriptRoot "check-autocad-api-series.ps1") `
                -AutoCADApiDir $resolvedApiDir
        }
        Write-Output "PRECHECK: compile-only installed AutoCAD API probe"
        $apiProbeEvidence = & (Join-Path $PSScriptRoot "probe-autocad-api.ps1") `
            -AutoCADApiDir $resolvedApiDir `
            -DotNet $resolvedDotNet `
            -PassThru
        if (
            $apiProbeEvidence.passed -ne $true -or
            $apiProbeEvidence.autocad_launched -ne $false -or
            $apiProbeEvidence.live_publish_proven -ne $false -or
            $apiProbeEvidence.evidence_scope -cne "compile-only" -or
            [string]$apiProbeEvidence.target_framework -notmatch '^net(45|48|8\.0-windows)$'
        ) {
            throw "Compile-only AutoCAD API probe returned invalid evidence."
        }
        $apiProbeEvidence | ConvertTo-Json -Depth 5
        $apiProbeRan = $true
    }

    $summary = [ordered]@{
        passed = $true
        repository = $repoRoot
        dotnet = $resolvedDotNet
        api_probe_ran = $apiProbeRan
        api_probe = if ($apiProbeRan) {
            [ordered]@{
                passed = $apiProbeEvidence.passed
                api_directory = $apiProbeEvidence.api_directory
                detected_series = $apiProbeEvidence.detected_series
                target_framework = $apiProbeEvidence.target_framework
                assemblies = $apiProbeEvidence.assemblies
                autocad_launched = $false
                live_publish_proven = $false
                evidence_scope = "compile-only"
            }
        }
        else { $null }
        dependency_audit_ran = $dependencyAuditRan
        dependency_audit = if ($dependencyAuditRan) { $dependencyAuditEvidence } else { $null }
        wheel_install_smoke = [ordered]@{
            passed = $true
            version = $wheelSmoke.version
            wheel_sha256 = $wheelSmoke.wheel_sha256
            protocol_version = $wheelSmoke.protocol_version
            tool_count = $wheelSmoke.tool_count
            http_transport_tool_count = $wheelSmoke.http_transport_tool_count
            http_transport_loopback_only = $true
            http_transport_header_guards = $true
            tunnel_preflight_redacted = $true
            tunnel_preflight_target_probed = $true
            tunnel_preflight_tool_surface_sha256 = $wheelSmoke.tunnel_preflight_tool_surface_sha256
            isolated_install = $true
            locked_dependencies = $true
            dependency_hashes_required = $true
            autocad_launched = $false
            live_tunnel_proven = $false
            live_publish_proven = $false
        }
        synthetic_batch_rehearsal = [ordered]@{
            target_drawings = $batchResult.target_drawings
            planning_pages = $batchResult.planning.pages
            ready = $batchResult.planning.ready
            staging_batches = $batchResult.staging.batches
            staged = $batchResult.staging.staged
            queue_capacity = $batchResult.queue_protocol_rehearsal.queue_capacity
            queue_waves = $batchResult.queue_protocol_rehearsal.waves
            queue_approvals = $batchResult.queue_protocol_rehearsal.approvals
            queue_simulated_acceptances = $batchResult.queue_protocol_rehearsal.simulated_acceptances
            queue_deferred_results = $batchResult.queue_protocol_rehearsal.deferred_results
            queue_pipe_attempts = $batchResult.queue_protocol_rehearsal.pipe_attempts
            queue_exact_retry_identity_preserved = $batchResult.queue_protocol_rehearsal.exact_retry_identity_preserved
            queue_status_batches = $batchResult.queue_protocol_rehearsal.status_batches
            queue_status_items = $batchResult.queue_protocol_rehearsal.status_items
            queue_status_pending = $batchResult.queue_protocol_rehearsal.status_pending
            queue_status_identity_preserved = $batchResult.queue_protocol_rehearsal.status_identity_preserved
            queue_plugin_contacted = $batchResult.queue_protocol_rehearsal.plugin_contacted
            restart_pages_before_outputs = $batchResult.restart_report_before_outputs.pages
            restart_pages_after_outputs = $batchResult.restart_report_after_outputs.pages
            outputs_complete = $batchResult.output_audit.outputs_complete
            execution_verified = $batchResult.output_audit.execution_verified
            publish_verified = $batchResult.output_audit.publish_verified
            manual_review_without_receipts = $batchResult.restart_report_after_outputs.manual_review
            source_unchanged = $batchResult.source_unchanged
            evidence_digest = $batchResult.evidence_digest
            synthetic = $true
        }
        autocad_launched = $false
        live_publish_proven = $false
    }
    if (-not [string]::IsNullOrWhiteSpace($SummaryPath)) {
        $resolvedSummary = [System.IO.Path]::GetFullPath($SummaryPath)
        if (Test-Path -LiteralPath $resolvedSummary) {
            throw "Preflight summary target already exists; evidence is never overwritten."
        }
        $parent = Split-Path -Parent $resolvedSummary
        if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
            throw "Preflight summary parent does not exist: $parent"
        }
        $summaryBytes = [System.Text.UTF8Encoding]::new($false).GetBytes(
            ($summary | ConvertTo-Json -Depth 5)
        )
        $stream = [System.IO.File]::Open(
            $resolvedSummary,
            [System.IO.FileMode]::CreateNew,
            [System.IO.FileAccess]::Write,
            [System.IO.FileShare]::Read
        )
        try { $stream.Write($summaryBytes, 0, $summaryBytes.Length) }
        finally { $stream.Dispose() }
    }
    $summary | ConvertTo-Json -Depth 5
}
finally {
    Pop-Location
}
