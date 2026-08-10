[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ReadinessReport,

    [string]$OutputRoot = ""
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot

function Invoke-GitReadOnly {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)

    $output = @(& git @Arguments 2>&1)
    if ($LASTEXITCODE -ne 0) {
        throw "Git command failed: git $($Arguments -join ' ')`n$($output -join [Environment]::NewLine)"
    }
    return $output
}

function Assert-NoRedirectedAncestor {
    param([Parameter(Mandatory = $true)][string]$Path)

    $current = [System.IO.Path]::GetFullPath($Path)
    while (-not (Test-Path -LiteralPath $current)) {
        $parent = Split-Path -Parent $current
        if ([string]::IsNullOrWhiteSpace($parent) -or $parent -eq $current) {
            throw "No existing ancestor was found for demo-kit output: $Path"
        }
        $current = $parent
    }
    while (-not [string]::IsNullOrWhiteSpace($current)) {
        $item = Get-Item -LiteralPath $current -Force
        if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Demo-kit output must not pass through a symlink or junction: $current"
        }
        $parent = Split-Path -Parent $current
        if ([string]::IsNullOrWhiteSpace($parent) -or $parent -eq $current) { break }
        $current = $parent
    }
}

function Write-NewUtf8Json {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)]$Value,
        [int]$Depth = 5
    )

    $bytes = [System.Text.UTF8Encoding]::new($false).GetBytes(
        ($Value | ConvertTo-Json -Depth $Depth)
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

function Get-PublicApiProbeEvidence {
    param($Probe)

    if ($null -eq $Probe) { return $null }
    [ordered]@{
        passed = $Probe.passed
        detected_series = $Probe.detected_series
        target_framework = $Probe.target_framework
        assemblies = @($Probe.assemblies | ForEach-Object {
            [ordered]@{
                Name = $_.Name
                AssemblyVersion = $_.AssemblyVersion
                Series = $_.Series
                Sha256 = $_.Sha256
            }
        })
        autocad_launched = $false
        live_publish_proven = $false
        evidence_scope = "compile-only"
    }
}

$resolvedReport = [System.IO.Path]::GetFullPath($ReadinessReport)
if (-not (Test-Path -LiteralPath $resolvedReport -PathType Leaf)) {
    throw "Readiness report does not exist: $resolvedReport"
}
$reportItem = Get-Item -LiteralPath $resolvedReport -Force
if (($reportItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw "Readiness report must not be a symlink or reparse point."
}
if ($reportItem.Length -gt 1MB) { throw "Readiness report exceeds the 1 MiB safety limit." }
try {
    $readiness = Get-Content -LiteralPath $resolvedReport -Raw -Encoding UTF8 | ConvertFrom-Json
}
catch { throw "Readiness report is not valid UTF-8 JSON." }

Push-Location $repoRoot
try {
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
        throw "Demo kit requires a clean worktree."
    }

    if (
        $readiness.passed -ne $true -or
        $readiness.local_demo_ready -ne $true -or
        $readiness.worktree_clean -ne $true -or
        $readiness.live_publish_proven -ne $false -or
        $readiness.licensed_live_pilot_ready -ne $false -or
        $readiness.exact_commit -ne $commit
    ) {
        throw "Readiness report is not a valid local-only result for the current commit."
    }
    if (
        $readiness.api_probe_ran -eq $true -and (
            $readiness.api_probe.passed -ne $true -or
            $readiness.api_probe.evidence_scope -cne "compile-only" -or
            $readiness.api_probe.autocad_launched -ne $false -or
            $readiness.api_probe.live_publish_proven -ne $false
        )
    ) {
        throw "Readiness report has invalid compile-only API evidence."
    }
    if ($readiness.api_probe_ran -ne $true -and $null -ne $readiness.api_probe) {
        throw "Readiness report contains API evidence without a completed probe."
    }
    $currentLockHash = (Get-FileHash -LiteralPath (Join-Path $repoRoot "uv.lock") -Algorithm SHA256).Hash.ToLowerInvariant()
    if (
        $readiness.dependency_audit_ran -eq $true -and (
            $readiness.dependency_audit.passed -ne $true -or
            $readiness.dependency_audit.lock.sha256 -cne $currentLockHash -or
            $readiness.dependency_audit.python.package_count -lt 1 -or
            $readiness.dependency_audit.python.vulnerability_count -ne 0 -or
            $readiness.dependency_audit.python_license_inventory.package_count -ne
                $readiness.dependency_audit.python.package_count -or
            $readiness.dependency_audit.python_license_inventory.unknown_count -ne 0 -or
            @($readiness.dependency_audit.python_license_inventory.packages).Count -ne
                $readiness.dependency_audit.python.package_count -or
            $readiness.dependency_audit.dotnet.project_count -lt 4 -or
            $readiness.dependency_audit.dotnet.vulnerability_count -ne 0 -or
            $readiness.dependency_audit.dotnet.source_count -lt 1 -or
            $readiness.dependency_audit.network_database_check -ne $true -or
            $readiness.dependency_audit.autocad_launched -ne $false -or
            $readiness.dependency_audit.live_publish_proven -ne $false
        )
    ) {
        throw "Readiness report has invalid dependency-audit evidence."
    }
    if ($readiness.dependency_audit_ran -ne $true -or $null -eq $readiness.dependency_audit) {
        throw "Demo kit requires completed dependency-audit evidence for its SBOM."
    }
    if ($readiness.dependency_audit_ran -eq $true) {
        $licensePackages = @($readiness.dependency_audit.python_license_inventory.packages)
        if (@($licensePackages | Group-Object -Property name | Where-Object Count -ne 1).Count -ne 0) {
            throw "Readiness dependency license inventory contains duplicate package names."
        }
        foreach ($package in $licensePackages) {
            if (
                [string]::IsNullOrWhiteSpace([string]$package.name) -or
                [string]::IsNullOrWhiteSpace([string]$package.version) -or
                [string]::IsNullOrWhiteSpace([string]$package.license) -or
                [string]$package.license -ceq "UNKNOWN"
            ) {
                throw "Readiness dependency license inventory is incomplete."
            }
        }
    }
    $batch = $readiness.synthetic_batch_rehearsal
    if (
        $batch.target_drawings -ne 300 -or
        $batch.ready -ne 300 -or
        $batch.staged -ne 300 -or
        $batch.queue_capacity -ne 7 -or
        $batch.queue_waves -ne 45 -or
        $batch.queue_approvals -ne 300 -or
        $batch.queue_simulated_acceptances -ne 300 -or
        $batch.queue_deferred_results -ne 285 -or
        $batch.queue_pipe_attempts -ne 330 -or
        $batch.queue_exact_retry_identity_preserved -ne $true -or
        $batch.queue_status_batches -ne 15 -or
        $batch.queue_status_items -ne 300 -or
        $batch.queue_status_pending -ne 300 -or
        $batch.queue_status_identity_preserved -ne $true -or
        $batch.queue_plugin_contacted -ne $false -or
        $batch.outputs_complete -ne 300 -or
        $batch.marking_content_verified -ne 300 -or
        $batch.execution_verified -ne 0 -or
        $batch.publish_verified -ne 0 -or
        $batch.orientation_mismatch_rejected -ne $true -or
        $batch.blank_pdf_rejected -ne $true -or
        $batch.manual_review_without_receipts -ne 300 -or
        $batch.source_unchanged -ne $true -or
        $batch.synthetic -ne $true -or
        [string]$batch.evidence_digest -notmatch '^sha256:[0-9a-f]{64}$'
    ) {
        throw "Readiness report has no valid 300-drawing synthetic batch evidence."
    }
    $durableQueue = $readiness.durable_queue_recovery
    if (
        $durableQueue.passed -ne $true -or
        $durableQueue.exact_test_count -ne 15 -or
        $durableQueue.pending_intent_recovered -ne $true -or
        $durableQueue.exact_request_identity_preserved -ne $true -or
        $durableQueue.interrupted_job_not_replayed -ne $true -or
        $durableQueue.terminal_receipt_status_recovered -ne $true -or
        $durableQueue.tampered_intent_blocked -ne $true -or
        $durableQueue.completed_job_requeue_blocked -ne $true -or
        $durableQueue.authentication_scheme -cne "windows-dpapi-current-user+hmac-sha256-v1" -or
        $durableQueue.signed_intent_required -ne $true -or
        $durableQueue.foreign_key_intent_blocked -ne $true -or
        $durableQueue.started_marker_authentication_required -ne $true -or
        $durableQueue.pending_cancellation_durable -ne $true -or
        $durableQueue.cancelled_job_not_replayed -ne $true -or
        $durableQueue.cancelled_marker_authentication_required -ne $true -or
        $durableQueue.running_job_not_cancelled -ne $true -or
        $durableQueue.protected_key_outside_workspace -ne $true -or
        $durableQueue.workspace_key_rejected -ne $true -or
        $durableQueue.corrupt_key_blocked -ne $true -or
        $durableQueue.net45_dpapi_runtime_proven -ne $true -or
        $durableQueue.net45_core_image_runtime -cne "v4.0.30319" -or
        $durableQueue.autocad_launched -ne $false -or
        $durableQueue.live_publish_proven -ne $false -or
        $durableQueue.evidence_scope -cne "production-core-net45+net8-with-synthetic-files"
    ) {
        throw "Readiness report has no valid durable queue recovery evidence."
    }
    $wheelSmoke = $readiness.wheel_install_smoke
    if (
        $wheelSmoke.passed -ne $true -or
        $wheelSmoke.tool_count -ne 20 -or
        $wheelSmoke.http_transport_tool_count -ne 20 -or
        $wheelSmoke.http_transport_loopback_only -ne $true -or
        $wheelSmoke.http_transport_header_guards -ne $true -or
        $wheelSmoke.tunnel_preflight_redacted -ne $true -or
        $wheelSmoke.tunnel_preflight_target_probed -ne $true -or
        [string]$wheelSmoke.tunnel_preflight_tool_surface_sha256 -notmatch '^[0-9a-f]{64}$' -or
        $wheelSmoke.chatgpt_eval_plan_prepared -ne $true -or
        $wheelSmoke.chatgpt_eval_case_count -ne 13 -or
        $wheelSmoke.sbom_cli_verified -ne $true -or
        $wheelSmoke.isolated_install -ne $true -or
        $wheelSmoke.locked_dependencies -ne $true -or
        $wheelSmoke.dependency_hashes_required -ne $true -or
        $wheelSmoke.autocad_launched -ne $false -or
        $wheelSmoke.live_tunnel_proven -ne $false -or
        $wheelSmoke.live_publish_proven -ne $false
    ) {
        throw "Readiness report has no valid installed local-target probe evidence."
    }

    $licensedPreflight = $readiness.licensed_workstation_preflight_smoke
    if (
        $licensedPreflight.passed -ne $true -or
        $licensedPreflight.positive_preflight -ne $true -or
        $licensedPreflight.autocad_2016_preflight -ne $true -or
        $licensedPreflight.autocad_2025_preflight -ne $true -or
        $licensedPreflight.autocad_2016_publish_session -ne $true -or
        $licensedPreflight.autocad_2025_publish_session -ne $true -or
        $licensedPreflight.no_overwrite -ne $true -or
        $licensedPreflight.mcp_config_created -ne $true -or
        $licensedPreflight.mcp_config_overwrite_blocked -ne $true -or
        $licensedPreflight.mcp_read_only_publish_flag_absent -ne $true -or
        $licensedPreflight.mcp_publish_flag_exact -ne $true -or
        $licensedPreflight.publish_enabled_blocked -ne $true -or
        $licensedPreflight.wrong_adapter_blocked -ne $true -or
        $licensedPreflight.publish_without_preflight_blocked -ne $true -or
        $licensedPreflight.tampered_read_only_preflight_blocked -ne $true -or
        $licensedPreflight.unauthenticated_publish_session_blocked -ne $true -or
        $licensedPreflight.autocad_launched -ne $false -or
        $licensedPreflight.live_publish_proven -ne $false
    ) {
        throw "Readiness report has no valid licensed-workstation preflight smoke evidence."
    }

    $wheelPath = Join-Path $repoRoot ("dist\{0}" -f $readiness.wheel)
    if (-not (Test-Path -LiteralPath $wheelPath -PathType Leaf)) {
        throw "Readiness-bound wheel is missing: $wheelPath"
    }
    $wheelHash = (Get-FileHash -LiteralPath $wheelPath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($wheelHash -ne $readiness.wheel_sha256) {
        throw "Wheel hash no longer matches the readiness report."
    }
    if ([string]$wheelSmoke.wheel_sha256 -cne $wheelHash) {
        throw "Installed local-target probe evidence does not match the demo wheel."
    }

    if ([string]::IsNullOrWhiteSpace($OutputRoot)) {
        $OutputRoot = Join-Path $repoRoot "artifacts"
    }
    $resolvedOutputRoot = [System.IO.Path]::GetFullPath($OutputRoot)
    $shortCommit = $commit.Substring(0, 7)
    $deliveryName = "cadplot-demo-delivery-$shortCommit"
    $kitName = "cadplot-demo-kit-$shortCommit"
    $deliveryRoot = Join-Path $resolvedOutputRoot $deliveryName
    $kitRoot = Join-Path $deliveryRoot $kitName
    $kitArchive = Join-Path $deliveryRoot "$kitName.zip"
    $outerManifestPath = Join-Path $deliveryRoot "demo-kit-build.json"
    if (Test-Path -LiteralPath $deliveryRoot) {
        throw "Demo delivery target already exists; packaging never overwrites: $deliveryRoot"
    }
    Assert-NoRedirectedAncestor -Path $deliveryRoot
    $null = New-Item -ItemType Directory -Path $deliveryRoot
    $null = New-Item -ItemType Directory -Path $kitRoot

    $sourceArchive = Join-Path $kitRoot ("cadplot-mcp-source-{0}.zip" -f $commit.Substring(0, 7))
    & git archive --format=zip --output=$sourceArchive $commit
    if ($LASTEXITCODE -ne 0) {
        throw "Git source archive failed. The partial kit was retained for inspection."
    }
    $kitWheel = Join-Path $kitRoot ([System.IO.Path]::GetFileName($wheelPath))
    Copy-Item -LiteralPath $wheelPath -Destination $kitWheel
    $kitVerifier = Join-Path $kitRoot "verify-demo-kit.ps1"
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot "verify-demo-kit.ps1") `
        -Destination $kitVerifier
    $archiveVerifier = Join-Path $kitRoot "verify-demo-archive.ps1"
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot "verify-demo-archive.ps1") `
        -Destination $archiveVerifier
    $demoRunbook = Join-Path $kitRoot "pazartesi-demo-tr.md"
    Copy-Item -LiteralPath (Join-Path $repoRoot "docs\pazartesi-demo-tr.md") `
        -Destination $demoRunbook
    $tunnelHandoff = Join-Path $kitRoot "secure-tunnel-handoff.md"
    Copy-Item -LiteralPath (Join-Path $repoRoot "docs\secure-tunnel-handoff.md") `
        -Destination $tunnelHandoff
    $chatgptEvaluation = Join-Path $kitRoot "chatgpt-evaluation.md"
    Copy-Item -LiteralPath (Join-Path $repoRoot "docs\chatgpt-evaluation.md") `
        -Destination $chatgptEvaluation
    $completionAudit = Join-Path $kitRoot "completion-audit.md"
    Copy-Item -LiteralPath (Join-Path $repoRoot "docs\completion-audit.md") `
        -Destination $completionAudit

    $sourceHash = (Get-FileHash -LiteralPath $sourceArchive -Algorithm SHA256).Hash.ToLowerInvariant()
    $kitWheelHash = (Get-FileHash -LiteralPath $kitWheel -Algorithm SHA256).Hash.ToLowerInvariant()
    $wheelMatch = [regex]::Match(
        [System.IO.Path]::GetFileName($kitWheel),
        '^cadplot_mcp-(?<version>[0-9]+(?:\.[0-9]+){2}(?:[A-Za-z0-9.+-]*))-py3-none-any\.whl$'
    )
    if (-not $wheelMatch.Success) { throw "Demo wheel version cannot be resolved." }
    $packageVersion = $wheelMatch.Groups['version'].Value
    $sbomPath = Join-Path $kitRoot "cadplot-mcp.cdx.json"
    $sbomOutput = @(& uv run cadplot-sbom generate `
        --dependency-audit $resolvedReport `
        --commit $commit `
        --version $packageVersion `
        --artifact "wheel=$kitWheel" `
        --artifact "source-archive=$sourceArchive" `
        --output $sbomPath 2>&1)
    if ($LASTEXITCODE -ne 0) {
        throw "CycloneDX SBOM generation failed.`n$($sbomOutput -join [Environment]::NewLine)"
    }
    try { $sbomEvidence = ($sbomOutput -join [Environment]::NewLine) | ConvertFrom-Json }
    catch { throw "CycloneDX SBOM generator did not return valid JSON evidence." }
    if (
        $sbomEvidence.passed -ne $true -or
        $sbomEvidence.spec_version -cne "1.7" -or
        $sbomEvidence.exact_commit -cne $commit -or
        $sbomEvidence.package_version -cne $packageVersion -or
        $sbomEvidence.runtime_dependency_count -ne $readiness.dependency_audit.python.package_count -or
        $sbomEvidence.artifact_count -ne 2 -or
        $sbomEvidence.machine_paths_included -ne $false -or
        $sbomEvidence.autocad_launched -ne $false -or
        $sbomEvidence.live_publish_proven -ne $false
    ) {
        throw "CycloneDX SBOM evidence crossed a required release boundary."
    }
    $sbomHash = (Get-FileHash -LiteralPath $sbomPath -Algorithm SHA256).Hash.ToLowerInvariant()
    $publicApiProbe = if ($readiness.api_probe_ran -eq $true) {
        Get-PublicApiProbeEvidence -Probe $readiness.api_probe
    }
    else { $null }
    $fileEvidence = @(@(
        $sourceArchive, $kitWheel, $kitVerifier, $archiveVerifier, $demoRunbook, $tunnelHandoff,
        $chatgptEvaluation, $sbomPath, $completionAudit
    ) | ForEach-Object {
        [ordered]@{
            path = [System.IO.Path]::GetFileName($_)
            sha256 = (Get-FileHash -LiteralPath $_ -Algorithm SHA256).Hash.ToLowerInvariant()
        }
    })
    $manifestPath = Join-Path $kitRoot "demo-kit.json"
    $manifest = [ordered]@{
        schema_version = 2
        exact_commit = $commit
        package_version = $packageVersion
        created_utc = [DateTime]::UtcNow.ToString("o")
        source_archive = [ordered]@{
            file = [System.IO.Path]::GetFileName($sourceArchive)
            sha256 = $sourceHash
        }
        wheel = [ordered]@{
            file = [System.IO.Path]::GetFileName($kitWheel)
            sha256 = $kitWheelHash
        }
        sbom = [ordered]@{
            file = [System.IO.Path]::GetFileName($sbomPath)
            sha256 = $sbomHash
            spec_version = "1.7"
            component_count = $sbomEvidence.component_count
            runtime_dependency_count = $sbomEvidence.runtime_dependency_count
            artifact_count = $sbomEvidence.artifact_count
        }
        files = $fileEvidence
        local_demo_ready = $true
        licensed_live_pilot_ready = $false
        public_release_ready = $false
        source_tree_audit_passed = $true
        api_probe_ran = $readiness.api_probe_ran -eq $true
        api_probe = $publicApiProbe
        dependency_audit_ran = $readiness.dependency_audit_ran -eq $true
        dependency_audit = if ($readiness.dependency_audit_ran -eq $true) {
            $readiness.dependency_audit
        }
        else { $null }
        wheel_install_smoke = $wheelSmoke
        licensed_workstation_preflight_smoke = $licensedPreflight
        durable_queue_recovery = $durableQueue
        autocad_launched = $false
        live_publish_proven = $false
        synthetic_batch_rehearsal = $batch
        company_assets_copied = $false
        autodesk_binaries_included = $false
        purpose = "Portable local/synthetic demo kit; not a live AutoCAD plug-in bundle"
    }
    Write-NewUtf8Json -Path $manifestPath -Value $manifest -Depth 6

    $verification = & $kitVerifier -KitRoot $kitRoot -PassThru
    if ($verification.Passed -ne $true -or $verification.MachinePathsIncluded -ne $false) {
        throw "Embedded demo-kit verification failed."
    }

    Compress-Archive -LiteralPath $kitRoot -DestinationPath $kitArchive -CompressionLevel Optimal
    $outerManifest = [ordered]@{
        schema_version = 1
        exact_commit = $commit
        package_version = $packageVersion
        created_utc = [DateTime]::UtcNow.ToString("o")
        kit_directory = $kitName
        kit_archive = [System.IO.Path]::GetFileName($kitArchive)
        kit_manifest = "demo-kit.json"
        kit_manifest_sha256 = (
            Get-FileHash -LiteralPath $manifestPath -Algorithm SHA256
        ).Hash.ToLowerInvariant()
        kit_archive_sha256 = (
            Get-FileHash -LiteralPath $kitArchive -Algorithm SHA256
        ).Hash.ToLowerInvariant()
        archive_file_count = 10
        sbom = $manifest.sbom
        local_demo_ready = $true
        licensed_live_pilot_ready = $false
        public_release_ready = $false
        company_assets_copied = $false
        autodesk_binaries_included = $false
        autocad_launched = $false
        live_publish_proven = $false
    }
    Write-NewUtf8Json -Path $outerManifestPath -Value $outerManifest -Depth 5
    $archiveVerification = & $archiveVerifier -DeliveryRoot $deliveryRoot -PassThru
    if (
        $archiveVerification.Passed -ne $true -or
        $archiveVerification.ExactCommit -cne $commit -or
        $archiveVerification.ArchiveFileCount -ne 10 -or
        $archiveVerification.MachinePathsIncluded -ne $false -or
        $archiveVerification.AutoCADLaunched -ne $false -or
        $archiveVerification.LivePublishProven -ne $false
    ) { throw "Embedded demo archive verification failed." }

    [ordered]@{
        passed = $true
        delivery_root = $deliveryRoot
        kit_root = $kitRoot
        kit_archive = $kitArchive
        outer_manifest = $outerManifestPath
        manifest = $manifestPath
        exact_commit = $commit
        source_sha256 = $sourceHash
        wheel_sha256 = $kitWheelHash
        sbom_sha256 = $sbomHash
        archive_sha256 = $archiveVerification.ArchiveSha256
        outer_manifest_sha256 = (
            Get-FileHash -LiteralPath $outerManifestPath -Algorithm SHA256
        ).Hash.ToLowerInvariant()
        archive_file_count = $archiveVerification.ArchiveFileCount
        synthetic_batch_evidence_digest = $batch.evidence_digest
        self_verification_passed = $true
        archive_verification_passed = $true
        machine_paths_included = $false
        dependency_audit_passed = $readiness.dependency_audit_ran -eq $true
        company_assets_copied = $false
        live_publish_proven = $false
    } | ConvertTo-Json
}
finally {
    Pop-Location
}
