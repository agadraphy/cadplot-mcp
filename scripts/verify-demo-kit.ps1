[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$KitRoot,

    [switch]$PassThru
)

$ErrorActionPreference = "Stop"
$root = [System.IO.Path]::GetFullPath($KitRoot).TrimEnd('\')
if (-not (Test-Path -LiteralPath $root -PathType Container)) {
    throw "Demo-kit directory does not exist: $root"
}

$allItems = @((Get-Item -LiteralPath $root -Force)) + @(
    Get-ChildItem -LiteralPath $root -Force -Recurse
)
foreach ($item in $allItems) {
    if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Demo kit must not contain a symlink, junction, or redirected file: $($item.FullName)"
    }
}
if (@(Get-ChildItem -LiteralPath $root -Directory -Force).Count -ne 0) {
    throw "Demo-kit directory must be flat and contain no subdirectories."
}

$manifestPath = Join-Path $root "demo-kit.json"
if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
    throw "Demo-kit manifest is missing."
}
$manifestItem = Get-Item -LiteralPath $manifestPath -Force
if ($manifestItem.Length -gt 1MB) { throw "Demo-kit manifest exceeds the 1 MiB safety limit." }
try {
    $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
}
catch { throw "Demo-kit manifest is not valid UTF-8 JSON." }

$wheelFiles = @(Get-ChildItem -LiteralPath $root -File -Filter "*.whl")
$sourceFiles = @(Get-ChildItem -LiteralPath $root -File -Filter "*.zip")
$sbomFiles = @(Get-ChildItem -LiteralPath $root -File -Filter "*.cdx.json")
if (
    $wheelFiles.Count -ne 1 -or
    $wheelFiles[0].Name -notmatch '^cadplot_mcp-[0-9A-Za-z.]+-py3-none-any\.whl$' -or
    $sourceFiles.Count -ne 1 -or
    $sourceFiles[0].Name -notmatch '^cadplot-mcp-source-[0-9a-f]{7}\.zip$' -or
    $sbomFiles.Count -ne 1 -or
    $sbomFiles[0].Name -cne "cadplot-mcp.cdx.json"
) {
    throw "Demo kit must contain exactly one conventionally named wheel and source archive."
}
$expectedFiles = @(
    "demo-kit.json",
    "pazartesi-demo-tr.md",
    "secure-tunnel-handoff.md",
    "chatgpt-evaluation.md",
    "completion-audit.md",
    "cadplot-mcp.cdx.json",
    "verify-demo-kit.ps1",
    "verify-demo-archive.ps1",
    $wheelFiles[0].Name,
    $sourceFiles[0].Name
)
$actualFiles = @(Get-ChildItem -LiteralPath $root -File -Force | Select-Object -ExpandProperty Name)
if (
    $actualFiles.Count -ne $expectedFiles.Count -or
    @($expectedFiles | Where-Object { $_ -notin $actualFiles }).Count -ne 0
) {
    throw "Demo-kit file set is not exact."
}

if (
    $manifest.schema_version -ne 2 -or
    $manifest.exact_commit -notmatch '^[0-9a-f]{40}$' -or
    $manifest.package_version -notmatch '^[0-9]+(?:\.[0-9]+){2}(?:[A-Za-z0-9.+-]*)?$' -or
    $manifest.local_demo_ready -ne $true -or
    $manifest.licensed_live_pilot_ready -ne $false -or
    $manifest.public_release_ready -ne $false -or
    $manifest.company_assets_copied -ne $false -or
    $manifest.autodesk_binaries_included -ne $false -or
    $manifest.autocad_launched -ne $false -or
    $manifest.live_publish_proven -ne $false -or
    $manifest.source_tree_audit_passed -ne $true
) {
    throw "Demo-kit identity or safety/evidence flags are invalid."
}

$batch = $manifest.synthetic_batch_rehearsal
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
    throw "Demo kit has no valid 300-drawing synthetic batch evidence."
}

$wheelSmoke = $manifest.wheel_install_smoke
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
    throw "Demo kit has no valid installed local-target probe evidence."
}

$licensedPreflight = $manifest.licensed_workstation_preflight_smoke
if (
    $licensedPreflight.passed -ne $true -or
    $licensedPreflight.positive_preflight -ne $true -or
    $licensedPreflight.autocad_2016_preflight -ne $true -or
    $licensedPreflight.autocad_2025_preflight -ne $true -or
    $licensedPreflight.autocad_2016_publish_session -ne $true -or
    $licensedPreflight.autocad_2025_publish_session -ne $true -or
    $licensedPreflight.no_overwrite -ne $true -or
    $licensedPreflight.publish_enabled_blocked -ne $true -or
    $licensedPreflight.wrong_adapter_blocked -ne $true -or
    $licensedPreflight.publish_without_preflight_blocked -ne $true -or
    $licensedPreflight.tampered_read_only_preflight_blocked -ne $true -or
    $licensedPreflight.unauthenticated_publish_session_blocked -ne $true -or
    $licensedPreflight.autocad_launched -ne $false -or
    $licensedPreflight.live_publish_proven -ne $false
) {
    throw "Demo kit has no valid licensed-workstation preflight smoke evidence."
}

$durableQueue = $manifest.durable_queue_recovery
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
    throw "Demo kit has no valid durable queue recovery evidence."
}

if ($manifest.api_probe_ran -eq $true) {
    $probe = $manifest.api_probe
    if (
        $null -eq $probe -or
        $probe.PSObject.Properties.Name -contains "api_directory" -or
        $probe.passed -ne $true -or
        $probe.detected_series -notmatch '^R[0-9]{2}\.[0-9]$' -or
        $probe.evidence_scope -cne "compile-only" -or
        $probe.autocad_launched -ne $false -or
        $probe.live_publish_proven -ne $false
    ) {
        throw "Demo-kit compile-only API evidence is invalid or contains a machine path."
    }
    $frameworks = @{
        "R20.1" = "net45"; "R21.0" = "net48"; "R22.0" = "net48"
        "R23.0" = "net48"; "R23.1" = "net48"; "R24.0" = "net48"
        "R24.1" = "net48"; "R24.2" = "net48"; "R24.3" = "net48"
        "R25.0" = "net8.0-windows"
    }
    if ($frameworks[[string]$probe.detected_series] -cne [string]$probe.target_framework) {
        throw "Demo-kit API series and target framework do not match."
    }
    $assemblies = @($probe.assemblies)
    $expectedAssemblies = @("AcMgd.dll", "AcDbMgd.dll", "AcCoreMgd.dll")
    if (
        $assemblies.Count -ne 3 -or
        @($expectedAssemblies | Where-Object { $_ -notin @($assemblies.Name) }).Count -ne 0
    ) {
        throw "Demo-kit API evidence does not contain the exact managed assembly set."
    }
    foreach ($assembly in $assemblies) {
        if (
            [string]$assembly.Series -cne [string]$probe.detected_series -or
            [string]$assembly.Sha256 -notmatch '^[0-9a-f]{64}$'
        ) {
            throw "Demo-kit API assembly identity is invalid."
        }
    }
}
elseif ($manifest.api_probe_ran -ne $false -or $null -ne $manifest.api_probe) {
    throw "Demo-kit API evidence is inconsistent."
}

if ($manifest.dependency_audit_ran -eq $true) {
    $audit = $manifest.dependency_audit
    if (
        $null -eq $audit -or
        $audit.passed -ne $true -or
        $audit.lock.file -cne "uv.lock" -or
        [string]$audit.lock.sha256 -notmatch '^[0-9a-f]{64}$' -or
        [string]$audit.lock.requirements_sha256 -notmatch '^[0-9a-f]{64}$' -or
        $audit.python.package_count -lt 1 -or
        $audit.python.vulnerability_count -ne 0 -or
        $audit.python_license_inventory.package_count -ne $audit.python.package_count -or
        $audit.python_license_inventory.unknown_count -ne 0 -or
        @($audit.python_license_inventory.packages).Count -ne $audit.python.package_count -or
        $audit.dotnet.project_count -lt 4 -or
        $audit.dotnet.vulnerability_count -ne 0 -or
        $audit.dotnet.source_count -lt 1 -or
        $audit.network_database_check -ne $true -or
        $audit.autocad_launched -ne $false -or
        $audit.live_publish_proven -ne $false
    ) {
        throw "Demo-kit dependency-audit evidence is invalid."
    }
    $licensePackages = @($audit.python_license_inventory.packages)
    if (@($licensePackages | Group-Object -Property name | Where-Object Count -ne 1).Count -ne 0) {
        throw "Demo-kit dependency license inventory contains duplicate package names."
    }
    foreach ($package in $licensePackages) {
        if (
            [string]::IsNullOrWhiteSpace([string]$package.name) -or
            [string]::IsNullOrWhiteSpace([string]$package.version) -or
            [string]::IsNullOrWhiteSpace([string]$package.license) -or
            [string]$package.license -ceq "UNKNOWN"
        ) {
            throw "Demo-kit dependency license inventory is incomplete."
        }
    }
}
elseif ($manifest.dependency_audit_ran -ne $false -or $null -ne $manifest.dependency_audit) {
    throw "Demo-kit dependency-audit evidence is inconsistent."
}

$sbomPath = $sbomFiles[0].FullName
$sbomItem = Get-Item -LiteralPath $sbomPath -Force
if ($sbomItem.Length -gt 2MB) { throw "Demo-kit SBOM exceeds the 2 MiB safety limit." }
try {
    $sbomRaw = Get-Content -LiteralPath $sbomPath -Raw -Encoding UTF8
    $sbom = $sbomRaw | ConvertFrom-Json
}
catch { throw "Demo-kit SBOM is not valid UTF-8 JSON." }
if (
    $sbom.'$schema' -cne "https://cyclonedx.org/schema/bom-1.7.schema.json" -or
    $sbom.bomFormat -cne "CycloneDX" -or
    $sbom.specVersion -cne "1.7" -or
    $sbom.version -ne 1 -or
    [string]$sbom.serialNumber -notmatch '^urn:uuid:[0-9a-f]{8}-[0-9a-f]{4}-5[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$' -or
    $sbom.metadata.component.type -cne "application" -or
    $sbom.metadata.component.name -cne "cadplot-mcp" -or
    $sbom.metadata.component.version -cne $manifest.package_version -or
    $sbom.metadata.component.'bom-ref' -cne "pkg:pypi/cadplot-mcp@$($manifest.package_version)" -or
    $sbom.metadata.component.purl -cne "pkg:pypi/cadplot-mcp@$($manifest.package_version)" -or
    @($sbom.compositions).Count -ne 1 -or
    $sbom.compositions[0].aggregate -cne "incomplete"
) {
    throw "Demo-kit SBOM identity is invalid."
}
$sbomProperties = @{}
foreach ($property in @($sbom.metadata.component.properties)) {
    if (
        [string]::IsNullOrWhiteSpace([string]$property.name) -or
        $sbomProperties.ContainsKey([string]$property.name)
    ) { throw "Demo-kit SBOM contains an invalid or duplicate property." }
    $sbomProperties[[string]$property.name] = [string]$property.value
}
if (
    $sbomProperties['cadplot:repository-commit'] -cne $manifest.exact_commit -or
    $sbomProperties['cadplot:uv-lock-sha256'] -cne $audit.lock.sha256 -or
    $sbomProperties['cadplot:dependency-requirements-sha256'] -cne $audit.lock.requirements_sha256 -or
    $sbomProperties['cadplot:autodesk-binaries-included'] -cne "false" -or
    $sbomProperties['cadplot:company-assets-included'] -cne "false" -or
    $sbomProperties['cadplot:autocad-launched'] -cne "false" -or
    $sbomProperties['cadplot:live-publish-proven'] -cne "false"
) { throw "Demo-kit SBOM release binding or evidence boundaries are invalid." }
$sbomComponents = @($sbom.components)
$runtimeComponents = @($sbomComponents | Where-Object { $_.type -ceq "library" })
$artifactComponents = @($sbomComponents | Where-Object { $_.type -ceq "file" })
$componentRefs = @($sbomComponents | ForEach-Object { [string]$_.'bom-ref' })
if (
    $sbomComponents.Count -ne ($audit.python.package_count + 2) -or
    $runtimeComponents.Count -ne $audit.python.package_count -or
    $artifactComponents.Count -ne 2 -or
    @($componentRefs | Group-Object | Where-Object Count -ne 1).Count -ne 0 -or
    @($sbom.dependencies).Count -ne ($sbomComponents.Count + 1)
) { throw "Demo-kit SBOM component inventory or dependency graph is invalid." }
$wheelSbom = @($artifactComponents | Where-Object { $_.'bom-ref' -ceq "urn:cadplot:artifact:wheel" })
$sourceSbom = @($artifactComponents | Where-Object { $_.'bom-ref' -ceq "urn:cadplot:artifact:source-archive" })
if (
    $wheelSbom.Count -ne 1 -or
    $sourceSbom.Count -ne 1 -or
    $wheelSbom[0].hashes[0].alg -cne "SHA-256" -or
    $wheelSbom[0].hashes[0].content -cne $manifest.wheel.sha256 -or
    $sourceSbom[0].hashes[0].alg -cne "SHA-256" -or
    $sourceSbom[0].hashes[0].content -cne $manifest.source_archive.sha256 -or
    $sbomRaw -match '(?i)(?:^|[\s"''=])(?:[a-z]:[\\/]|\\\\|/users/|/home/)'
) { throw "Demo-kit SBOM artifact hashes or path-redaction boundary are invalid." }
$sbomHash = (Get-FileHash -LiteralPath $sbomPath -Algorithm SHA256).Hash.ToLowerInvariant()
if (
    $manifest.sbom.file -cne "cadplot-mcp.cdx.json" -or
    $manifest.sbom.sha256 -cne $sbomHash -or
    $manifest.sbom.spec_version -cne "1.7" -or
    $manifest.sbom.component_count -ne $sbomComponents.Count -or
    $manifest.sbom.runtime_dependency_count -ne $runtimeComponents.Count -or
    $manifest.sbom.artifact_count -ne $artifactComponents.Count
) { throw "Demo-kit SBOM manifest evidence is invalid." }

$manifestFiles = @($manifest.files)
$filesWithoutManifest = @($expectedFiles | Where-Object { $_ -cne "demo-kit.json" })
if ($manifestFiles.Count -ne $filesWithoutManifest.Count) {
    throw "Demo-kit file evidence count is invalid."
}
if (@($manifestFiles | Group-Object -Property path | Where-Object Count -ne 1).Count -ne 0) {
    throw "Demo-kit manifest contains duplicate file evidence."
}
foreach ($relative in $filesWithoutManifest) {
    $matches = @($manifestFiles | Where-Object { $_.path -ceq $relative })
    $actualHash = (Get-FileHash -LiteralPath (Join-Path $root $relative) -Algorithm SHA256).Hash.ToLowerInvariant()
    $recordedHash = if ($matches.Count -eq 1) { [string]$matches[0].sha256 } else { "<missing-or-duplicate>" }
    if (
        $matches.Count -ne 1 -or
        $recordedHash -notmatch '^[0-9a-f]{64}$' -or
        $recordedHash -cne $actualHash
    ) {
        throw "Demo-kit file hash mismatch: $relative (recorded=$recordedHash actual=$actualHash)"
    }
}

$wheelHash = (Get-FileHash -LiteralPath $wheelFiles[0].FullName -Algorithm SHA256).Hash.ToLowerInvariant()
$sourceHash = (Get-FileHash -LiteralPath $sourceFiles[0].FullName -Algorithm SHA256).Hash.ToLowerInvariant()
if (
    $manifest.wheel.file -cne $wheelFiles[0].Name -or
    $manifest.wheel.sha256 -cne $wheelHash -or
    $manifest.source_archive.file -cne $sourceFiles[0].Name -or
    $manifest.source_archive.sha256 -cne $sourceHash -or
    [string]$wheelSmoke.wheel_sha256 -cne $wheelHash
) {
    throw "Demo-kit wheel or source archive evidence is invalid."
}

$result = [pscustomobject]@{
    Passed = $true
    KitRoot = $root
    ExactCommit = $manifest.exact_commit
    WheelSha256 = $wheelHash
    SourceSha256 = $sourceHash
    MachinePathsIncluded = $false
    DependencyAuditPassed = $manifest.dependency_audit_ran -eq $true
    SbomVerified = $true
    LocalDemoReady = $true
    AutoCADLaunched = $false
    LivePublishProven = $false
}
if ($PassThru) { $result }
else { $result | ConvertTo-Json -Depth 3 }
