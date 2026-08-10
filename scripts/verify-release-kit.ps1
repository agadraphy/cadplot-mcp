[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ReleaseRoot,

    [switch]$PassThru,

    [switch]$AllowProtocolOnlyFixture
)

$ErrorActionPreference = "Stop"
$root = [System.IO.Path]::GetFullPath($ReleaseRoot).TrimEnd('\')
if (-not (Test-Path -LiteralPath $root -PathType Container)) {
    throw "Release-kit directory does not exist: $root"
}

$allItems = @((Get-Item -LiteralPath $root -Force)) + @(
    Get-ChildItem -LiteralPath $root -Force -Recurse
)
foreach ($item in $allItems) {
    if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Release kit must not contain a symlink, junction, or redirected file: $($item.FullName)"
    }
}

$expectedTopLevel = @("CadPlotMcp.release", "CadPlotMcp.release.zip", "release-kit-build.json")
$actualTopLevel = @(Get-ChildItem -LiteralPath $root -Force | Select-Object -ExpandProperty Name)
$missingTopLevel = @($expectedTopLevel | Where-Object { $_ -notin $actualTopLevel })
$unexpectedTopLevel = @($actualTopLevel | Where-Object { $_ -notin $expectedTopLevel })
if ($missingTopLevel.Count -gt 0 -or $unexpectedTopLevel.Count -gt 0) {
    throw "Release-kit top-level contents are not exact. Missing: $($missingTopLevel -join ', '); unexpected: $($unexpectedTopLevel -join ', ')"
}

$kitRoot = Join-Path $root "CadPlotMcp.release"
$archivePath = Join-Path $root "CadPlotMcp.release.zip"
$outerManifestPath = Join-Path $root "release-kit-build.json"
$kitManifestPath = Join-Path $kitRoot "release-kit.json"
foreach ($manifestPath in @($outerManifestPath, $kitManifestPath)) {
    $item = Get-Item -LiteralPath $manifestPath -Force
    if ($item.Length -gt 1MB) { throw "Release-kit manifest exceeds the 1 MiB safety limit." }
}
try {
    $outer = Get-Content -LiteralPath $outerManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $manifest = Get-Content -LiteralPath $kitManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
}
catch { throw "Release-kit manifest is not valid UTF-8 JSON." }

$shaPattern = '^[0-9a-f]{64}$'
if ($outer.schema_version -ne 1 -or $manifest.schema_version -ne 1) {
    throw "Unsupported release-kit manifest schema."
}
if (
    $outer.exact_commit -notmatch '^[0-9a-f]{40}$' -or
    $outer.exact_commit -cne $manifest.exact_commit -or
    [string]::IsNullOrWhiteSpace([string]$outer.package_version) -or
    $outer.package_version -cne $manifest.package_version -or
    $outer.kit_directory -cne "CadPlotMcp.release" -or
    $outer.kit_archive -cne "CadPlotMcp.release.zip"
) {
    throw "Release-kit identity fields are invalid or inconsistent."
}
$protocolOnlyFixture = (
    $outer.protocol_only_fixture -eq $true -and
    $manifest.protocol_only_fixture -eq $true
)
foreach ($evidence in @($outer, $manifest)) {
    if (
        $evidence.local_demo_ready -ne $true -or
        $evidence.licensed_live_pilot_ready -ne $false -or
        $evidence.public_release_ready -ne $false -or
        $evidence.company_assets_copied -ne $false -or
        $evidence.autodesk_binaries_included -ne $false -or
        $evidence.autocad_launched -ne $false -or
        $evidence.live_publish_proven -ne $false
    ) {
        throw "Release-kit safety/evidence flags are invalid."
    }
}
foreach ($evidence in @($outer, $manifest)) {
    $audit = $evidence.dependency_audit
    if (
        $evidence.dependency_audit_ran -ne $true -or
        $null -eq $audit -or
        $audit.passed -ne $true -or
        $audit.lock.file -cne "uv.lock" -or
        [string]$audit.lock.sha256 -notmatch $shaPattern -or
        [string]$audit.lock.requirements_sha256 -notmatch $shaPattern -or
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
        throw "Release-kit dependency-audit evidence is invalid."
    }
    $licensePackages = @($audit.python_license_inventory.packages)
    if (@($licensePackages | Group-Object -Property name | Where-Object Count -ne 1).Count -ne 0) {
        throw "Release-kit dependency license inventory contains duplicate package names."
    }
    foreach ($package in $licensePackages) {
        if (
            [string]::IsNullOrWhiteSpace([string]$package.name) -or
            [string]::IsNullOrWhiteSpace([string]$package.version) -or
            [string]::IsNullOrWhiteSpace([string]$package.license) -or
            [string]$package.license -ceq "UNKNOWN"
        ) {
            throw "Release-kit dependency license inventory is incomplete."
        }
    }
}
if (
    $outer.dependency_audit.lock.sha256 -cne $manifest.dependency_audit.lock.sha256 -or
    $outer.dependency_audit.lock.requirements_sha256 -cne $manifest.dependency_audit.lock.requirements_sha256 -or
    ($outer.dependency_audit.python_license_inventory | ConvertTo-Json -Compress -Depth 6) -cne
        ($manifest.dependency_audit.python_license_inventory | ConvertTo-Json -Compress -Depth 6)
) {
    throw "Release-kit dependency-audit evidence is inconsistent."
}
if (
    ($outer.sbom | ConvertTo-Json -Compress -Depth 4) -cne
        ($manifest.sbom | ConvertTo-Json -Compress -Depth 4)
) { throw "Release-kit SBOM evidence is inconsistent." }
foreach ($evidence in @($outer, $manifest)) {
    $wheelSmoke = $evidence.wheel_install_smoke
    if (
        $wheelSmoke.passed -ne $true -or
        $wheelSmoke.tool_count -ne 20 -or
        $wheelSmoke.http_transport_tool_count -ne 20 -or
        $wheelSmoke.http_transport_loopback_only -ne $true -or
        $wheelSmoke.http_transport_header_guards -ne $true -or
        $wheelSmoke.tunnel_preflight_redacted -ne $true -or
        $wheelSmoke.tunnel_preflight_target_probed -ne $true -or
        [string]$wheelSmoke.tunnel_preflight_tool_surface_sha256 -notmatch $shaPattern -or
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
        throw "Release kit has no valid installed local-target probe evidence."
    }
}
if (
    ($outer.wheel_install_smoke | ConvertTo-Json -Compress -Depth 4) -cne
        ($manifest.wheel_install_smoke | ConvertTo-Json -Compress -Depth 4)
) {
    throw "Release-kit installed local-target probe evidence is inconsistent."
}
foreach ($evidence in @($outer, $manifest)) {
    $durableQueue = $evidence.durable_queue_recovery
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
        throw "Release kit has no valid durable queue recovery evidence."
    }
}
if (
    ($outer.durable_queue_recovery | ConvertTo-Json -Compress -Depth 4) -cne
        ($manifest.durable_queue_recovery | ConvertTo-Json -Compress -Depth 4)
) {
    throw "Release-kit durable queue recovery evidence is inconsistent."
}
foreach ($evidence in @($outer, $manifest)) {
    $licensedPreflight = $evidence.licensed_workstation_preflight_smoke
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
        throw "Release kit has no valid licensed-workstation session/config smoke evidence."
    }
}
if (
    ($outer.licensed_workstation_preflight_smoke | ConvertTo-Json -Compress -Depth 4) -cne
        ($manifest.licensed_workstation_preflight_smoke | ConvertTo-Json -Compress -Depth 4)
) {
    throw "Release-kit licensed-workstation session/config evidence is inconsistent."
}
$embeddedLockHash = (
    Get-FileHash -LiteralPath (Join-Path $kitRoot "python\uv.lock") -Algorithm SHA256
).Hash.ToLowerInvariant()
if ($manifest.dependency_audit.lock.sha256 -cne $embeddedLockHash) {
    throw "Release-kit dependency audit does not match its embedded uv.lock."
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
    throw "Release kit has no valid 300-drawing synthetic batch evidence."
}
if (
    $outer.matching_sdk_bundle_built -ne $manifest.matching_sdk_bundle_built -or
    $outer.matching_sdk_bundle_built -ne $true
) {
    if (-not $AllowProtocolOnlyFixture -or -not $protocolOnlyFixture) {
        throw "Release kit is not a matching-SDK build."
    }
}
elseif ($protocolOnlyFixture) {
    throw "A matching-SDK release kit cannot be labelled as a protocol-only fixture."
}

$expectedDirectories = @(
    "autocad", "autocad/CadPlotMcp.bundle", "autocad/CadPlotMcp.bundle/Contents",
    "autocad/CadPlotMcp.bundle/Contents/Windows",
    "autocad/CadPlotMcp.bundle/Contents/Windows/2016",
    "autocad/CadPlotMcp.bundle/Contents/Windows/2025",
    "python", "source", "scripts", "docs", "config"
)
$actualDirectories = @(Get-ChildItem -LiteralPath $kitRoot -Directory -Recurse | ForEach-Object {
    $_.FullName.Substring($kitRoot.Length + 1).Replace('\', '/')
})
if (
    $actualDirectories.Count -ne $expectedDirectories.Count -or
    @($expectedDirectories | Where-Object { $_ -notin $actualDirectories }).Count -ne 0
) {
    throw "Release-kit directory set is not exact."
}

$fixedFiles = @(
    "LICENSE", "README.md", "README.tr.md", "CHANGELOG.md", "THIRD_PARTY_NOTICES.md",
    "cadplot-mcp.cdx.json",
    "autocad/CadPlotMcp.bundle.zip", "autocad/bundle-build.json",
    "autocad/CadPlotMcp.bundle/PackageContents.xml", "autocad/CadPlotMcp.bundle/LICENSE",
    "autocad/CadPlotMcp.bundle/Contents/Windows/2016/CadPlotMcp.AutoCAD2016.dll",
    "autocad/CadPlotMcp.bundle/Contents/Windows/2016/CadPlotMcp.Core.dll",
    "autocad/CadPlotMcp.bundle/Contents/Windows/2025/CadPlotMcp.AutoCAD2025.dll",
    "autocad/CadPlotMcp.bundle/Contents/Windows/2025/CadPlotMcp.Core.dll",
    "python/pyproject.toml", "python/uv.lock",
    "scripts/install-bundle.ps1", "scripts/uninstall-bundle.ps1",
    "scripts/verify-bundle.ps1", "scripts/verify-bundle-release.ps1",
    "scripts/verify-release-kit.ps1", "scripts/install-python.ps1",
    "scripts/verify-python-install.ps1", "scripts/uninstall-python.ps1",
    "scripts/install-release-kit.ps1", "scripts/verify-release-install.ps1",
    "scripts/uninstall-release-kit.ps1", "scripts/test-licensed-workstation.ps1",
    "scripts/check-autocad-api-series.ps1",
    "scripts/new-local-pilot.ps1", "scripts/collect-pilot-run.py",
    "scripts/collect-batch-recovery.py",
    "scripts/assemble-pilot-evidence.py", "scripts/validate-pilot-evidence.py",
    "scripts/release-acceptance.py",
    "docs/monday-pilot.md", "docs/pazartesi-demo-tr.md", "docs/release-checklist.md",
    "docs/release-kit-install.md", "docs/pilot-evidence.md", "docs/deployment-modes.md",
    "docs/release-acceptance.md", "docs/chatgpt-connection.md", "docs/loopback-http.md",
    "docs/secure-tunnel-handoff.md", "docs/chatgpt-evaluation.md",
    "docs/autodesk-sdk-prerequisites.md", "docs/software-bill-of-materials.md",
    "docs/completion-audit.md",
    "config/config.inventory.example.yaml", "release-kit.json"
)
$wheelFiles = @(Get-ChildItem -LiteralPath (Join-Path $kitRoot "python") -File -Filter "*.whl")
$sourceFiles = @(Get-ChildItem -LiteralPath (Join-Path $kitRoot "source") -File -Filter "*.zip")
if (
    $wheelFiles.Count -ne 1 -or
    $wheelFiles[0].Name -notmatch '^cadplot_mcp-[0-9A-Za-z.]+-py3-none-any\.whl$' -or
    $sourceFiles.Count -ne 1 -or
    $sourceFiles[0].Name -notmatch '^cadplot-mcp-source-[0-9a-f]{7}\.zip$'
) {
    throw "Release kit must contain exactly one conventionally named wheel and source archive."
}
$expectedFiles = $fixedFiles + @(
    "python/$($wheelFiles[0].Name)", "source/$($sourceFiles[0].Name)"
)
$actualFiles = @(Get-ChildItem -LiteralPath $kitRoot -File -Recurse | ForEach-Object {
    $_.FullName.Substring($kitRoot.Length + 1).Replace('\', '/')
})
if (
    $actualFiles.Count -ne $expectedFiles.Count -or
    @($expectedFiles | Where-Object { $_ -notin $actualFiles }).Count -ne 0
) {
    throw "Release-kit file set is not exact."
}

$manifestFiles = @($manifest.files)
$filesWithoutManifest = @($actualFiles | Where-Object { $_ -cne "release-kit.json" })
if ($manifestFiles.Count -ne $filesWithoutManifest.Count) {
    throw "Release-kit file evidence count is invalid."
}
foreach ($relative in $filesWithoutManifest) {
    $matches = @($manifestFiles | Where-Object { $_.path -ceq $relative })
    $actualHash = (Get-FileHash -LiteralPath (Join-Path $kitRoot $relative) -Algorithm SHA256).Hash.ToLowerInvariant()
    $recordedHash = if ($matches.Count -eq 1) {
        [string](($matches | Select-Object -First 1).sha256)
    }
    else { "<missing-or-duplicate>" }
    if (
        $matches.Count -ne 1 -or
        $recordedHash -notmatch $shaPattern -or
        $recordedHash -cne $actualHash
    ) {
        throw "Release-kit file hash mismatch: $relative (recorded=$recordedHash actual=$actualHash)"
    }
}
if (@($manifestFiles | Group-Object -Property path | Where-Object Count -ne 1).Count -ne 0) {
    throw "Release-kit manifest contains duplicate file evidence."
}

$wheelRelative = "python/$($wheelFiles[0].Name)"
$wheelHash = (Get-FileHash -LiteralPath $wheelFiles[0].FullName -Algorithm SHA256).Hash.ToLowerInvariant()
if (
    $manifest.wheel.file -cne $wheelRelative -or
    $manifest.wheel.sha256 -cne $wheelHash -or
    [string]$manifest.wheel_install_smoke.wheel_sha256 -cne $wheelHash
) {
    throw "Release-kit wheel evidence is invalid."
}
if ($manifest.source_archive -cne "source/$($sourceFiles[0].Name)") {
    throw "Release-kit source archive evidence is invalid."
}

$sbomPath = Join-Path $kitRoot "cadplot-mcp.cdx.json"
$sbomItem = Get-Item -LiteralPath $sbomPath -Force
if ($sbomItem.Length -gt 2MB) { throw "Release-kit SBOM exceeds the 2 MiB safety limit." }
try {
    $sbomRaw = Get-Content -LiteralPath $sbomPath -Raw -Encoding UTF8
    $sbom = $sbomRaw | ConvertFrom-Json
}
catch { throw "Release-kit SBOM is not valid UTF-8 JSON." }
$rootRef = "pkg:pypi/cadplot-mcp@$($manifest.package_version)"
if (
    $sbom.'$schema' -cne "https://cyclonedx.org/schema/bom-1.7.schema.json" -or
    $sbom.bomFormat -cne "CycloneDX" -or
    $sbom.specVersion -cne "1.7" -or
    $sbom.version -ne 1 -or
    [string]$sbom.serialNumber -notmatch '^urn:uuid:[0-9a-f]{8}-[0-9a-f]{4}-5[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$' -or
    $sbom.metadata.component.type -cne "application" -or
    $sbom.metadata.component.name -cne "cadplot-mcp" -or
    $sbom.metadata.component.version -cne $manifest.package_version -or
    $sbom.metadata.component.'bom-ref' -cne $rootRef -or
    $sbom.metadata.component.purl -cne $rootRef -or
    @($sbom.compositions).Count -ne 1 -or
    $sbom.compositions[0].aggregate -cne "incomplete" -or
    $sbom.compositions[0].assemblies[0] -cne $rootRef
) { throw "Release-kit SBOM identity is invalid." }
$sbomProperties = @{}
foreach ($property in @($sbom.metadata.component.properties)) {
    if (
        [string]::IsNullOrWhiteSpace([string]$property.name) -or
        $sbomProperties.ContainsKey([string]$property.name)
    ) { throw "Release-kit SBOM contains an invalid or duplicate property." }
    $sbomProperties[[string]$property.name] = [string]$property.value
}
if (
    $sbomProperties['cadplot:repository-commit'] -cne $manifest.exact_commit -or
    $sbomProperties['cadplot:uv-lock-sha256'] -cne $manifest.dependency_audit.lock.sha256 -or
    $sbomProperties['cadplot:dependency-requirements-sha256'] -cne
        $manifest.dependency_audit.lock.requirements_sha256 -or
    $sbomProperties['cadplot:autodesk-binaries-included'] -cne "false" -or
    $sbomProperties['cadplot:company-assets-included'] -cne "false" -or
    $sbomProperties['cadplot:autocad-launched'] -cne "false" -or
    $sbomProperties['cadplot:live-publish-proven'] -cne "false"
) { throw "Release-kit SBOM release binding or evidence boundaries are invalid." }
$sbomComponents = @($sbom.components)
$runtimeComponents = @($sbomComponents | Where-Object { $_.type -ceq "library" })
$artifactComponents = @($sbomComponents | Where-Object { $_.type -ceq "file" })
$componentRefs = @($sbomComponents | ForEach-Object { [string]$_.'bom-ref' })
if (
    $sbomComponents.Count -ne ($manifest.dependency_audit.python.package_count + 7) -or
    $runtimeComponents.Count -ne $manifest.dependency_audit.python.package_count -or
    $artifactComponents.Count -ne 7 -or
    @($componentRefs | Group-Object | Where-Object Count -ne 1).Count -ne 0 -or
    @($sbom.dependencies).Count -ne ($sbomComponents.Count + 1) -or
    $sbom.dependencies[0].ref -cne $rootRef -or
    @($sbom.dependencies[0].dependsOn).Count -ne $sbomComponents.Count
) { throw "Release-kit SBOM component inventory or dependency graph is invalid." }
$expectedArtifactPaths = [ordered]@{
    "python-wheel" = $wheelFiles[0].FullName
    "source-archive" = $sourceFiles[0].FullName
    "autocad-bundle" = (Join-Path $kitRoot "autocad\CadPlotMcp.bundle.zip")
    "autocad-2016-adapter" = (Join-Path $kitRoot "autocad\CadPlotMcp.bundle\Contents\Windows\2016\CadPlotMcp.AutoCAD2016.dll")
    "autocad-2016-core" = (Join-Path $kitRoot "autocad\CadPlotMcp.bundle\Contents\Windows\2016\CadPlotMcp.Core.dll")
    "autocad-2025-adapter" = (Join-Path $kitRoot "autocad\CadPlotMcp.bundle\Contents\Windows\2025\CadPlotMcp.AutoCAD2025.dll")
    "autocad-2025-core" = (Join-Path $kitRoot "autocad\CadPlotMcp.bundle\Contents\Windows\2025\CadPlotMcp.Core.dll")
}
foreach ($artifactName in $expectedArtifactPaths.Keys) {
    $matches = @($artifactComponents | Where-Object {
        $_.'bom-ref' -ceq "urn:cadplot:artifact:$artifactName" -and $_.name -ceq $artifactName
    })
    $actualArtifactHash = (
        Get-FileHash -LiteralPath $expectedArtifactPaths[$artifactName] -Algorithm SHA256
    ).Hash.ToLowerInvariant()
    if (
        $matches.Count -ne 1 -or
        @($matches[0].hashes).Count -ne 1 -or
        $matches[0].hashes[0].alg -cne "SHA-256" -or
        $matches[0].hashes[0].content -cne $actualArtifactHash
    ) { throw "Release-kit SBOM artifact hash is invalid: $artifactName" }
}
$sbomHash = (Get-FileHash -LiteralPath $sbomPath -Algorithm SHA256).Hash.ToLowerInvariant()
if (
    $manifest.sbom.file -cne "cadplot-mcp.cdx.json" -or
    $manifest.sbom.sha256 -cne $sbomHash -or
    $manifest.sbom.spec_version -cne "1.7" -or
    $manifest.sbom.component_count -ne $sbomComponents.Count -or
    $manifest.sbom.runtime_dependency_count -ne $runtimeComponents.Count -or
    $manifest.sbom.artifact_count -ne $artifactComponents.Count -or
    $sbomRaw -match '(?i)(?:^|[\s"''=])(?:[a-z]:[\\/]|\\\\|/users/|/home/)'
) { throw "Release-kit SBOM manifest evidence or path-redaction boundary is invalid." }

$bundleReleaseRoot = Join-Path $kitRoot "autocad"
$bundleVerifierArguments = @{
    ReleaseRoot = $bundleReleaseRoot
    PassThru = $true
}
if ($AllowProtocolOnlyFixture) {
    $bundleVerifierArguments.AllowProtocolOnlyFixture = $true
}
$bundleEvidence = & (Join-Path $PSScriptRoot "verify-bundle-release.ps1") @bundleVerifierArguments
if (
    $bundleEvidence.ExactCommit -cne $manifest.exact_commit -or
    $bundleEvidence.PackageVersion -cne $manifest.package_version -or
    $bundleEvidence.MatchingSdkBundleBuilt -ne ($manifest.matching_sdk_bundle_built -eq $true) -or
    $bundleEvidence.ProtocolOnlyFixture -ne $protocolOnlyFixture
) {
    throw "Embedded AutoCAD bundle identity does not match the release kit."
}

$kitManifestHash = (Get-FileHash -LiteralPath $kitManifestPath -Algorithm SHA256).Hash.ToLowerInvariant()
$archiveHash = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant()
if (
    $outer.kit_manifest_sha256 -cne $kitManifestHash -or
    $outer.kit_archive_sha256 -cne $archiveHash
) {
    throw "Release-kit outer hash evidence does not match."
}

Add-Type -AssemblyName System.IO.Compression.FileSystem
$archive = [System.IO.Compression.ZipFile]::OpenRead($archivePath)
try {
    $fileEntries = @($archive.Entries | Where-Object { -not [string]::IsNullOrEmpty($_.Name) })
    $expectedEntryNames = @($actualFiles | ForEach-Object { "CadPlotMcp.release/$_" })
    $actualEntryNames = @($fileEntries | ForEach-Object { $_.FullName.Replace('\', '/') })
    if (
        $fileEntries.Count -ne $expectedEntryNames.Count -or
        @($expectedEntryNames | Where-Object { $_ -notin $actualEntryNames }).Count -ne 0
    ) {
        throw "Release-kit archive entries do not exactly match the verified directory."
    }
    foreach ($entry in $fileEntries) {
        $relative = $entry.FullName.Replace('\', '/').Substring("CadPlotMcp.release/".Length)
        $algorithm = [System.Security.Cryptography.SHA256]::Create()
        $stream = $entry.Open()
        try {
            $entryHash = [BitConverter]::ToString(
                $algorithm.ComputeHash($stream)
            ).Replace('-', '').ToLowerInvariant()
        }
        finally {
            $stream.Dispose()
            $algorithm.Dispose()
        }
        $directoryHash = (Get-FileHash -LiteralPath (Join-Path $kitRoot $relative) -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($entryHash -cne $directoryHash) {
            throw "Release-kit archive entry hash mismatch: $relative"
        }
    }
}
finally { $archive.Dispose() }

$result = [pscustomobject]@{
    Passed = $true
    ReleaseRoot = $root
    ExactCommit = $manifest.exact_commit
    PackageVersion = $manifest.package_version
    ArchiveSha256 = $archiveHash
    MatchingSdkBundleBuilt = $manifest.matching_sdk_bundle_built -eq $true
    ProtocolOnlyFixture = $protocolOnlyFixture
    DependencyAuditPassed = $true
    SbomVerified = $true
    LocalDemoReady = $true
    AutoCADLaunched = $false
    LivePublishProven = $false
}
if ($PassThru) { $result }
else { $result | ConvertTo-Json -Depth 3 }
