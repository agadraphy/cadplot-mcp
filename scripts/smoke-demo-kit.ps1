[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$smokeRoot = Join-Path ([System.IO.Path]::GetTempPath()) (
    "cadplot-demo-kit-smoke-{0}" -f [Guid]::NewGuid().ToString("N")
)
$resolvedRoot = [System.IO.Path]::GetFullPath($smokeRoot)
$archiveSmokeRoot = Join-Path ([System.IO.Path]::GetTempPath()) (
    "cadplot-demo-archive-smoke-{0}" -f [Guid]::NewGuid().ToString("N")
)
$resolvedArchiveRoot = [System.IO.Path]::GetFullPath($archiveSmokeRoot)
$tempBoundary = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath()).TrimEnd('\') + '\'
if (
    -not $resolvedRoot.StartsWith($tempBoundary, [System.StringComparison]::OrdinalIgnoreCase) -or
    -not $resolvedArchiveRoot.StartsWith($tempBoundary, [System.StringComparison]::OrdinalIgnoreCase)
) {
    throw "Demo-kit smoke root escaped the system temporary directory."
}

try {
    $null = New-Item -ItemType Directory -Path $resolvedRoot
    $verifier = Join-Path $resolvedRoot "verify-demo-kit.ps1"
    $archiveVerifier = Join-Path $resolvedRoot "verify-demo-archive.ps1"
    $wheel = Join-Path $resolvedRoot "cadplot_mcp-0.1.0-py3-none-any.whl"
    $source = Join-Path $resolvedRoot "cadplot-mcp-source-0000000.zip"
    $demoRunbook = Join-Path $resolvedRoot "pazartesi-demo-tr.md"
    $tunnelHandoff = Join-Path $resolvedRoot "secure-tunnel-handoff.md"
    $chatgptEvaluation = Join-Path $resolvedRoot "chatgpt-evaluation.md"
    $completionAudit = Join-Path $resolvedRoot "completion-audit.md"
    $sbomPath = Join-Path $resolvedRoot "cadplot-mcp.cdx.json"
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot "verify-demo-kit.ps1") -Destination $verifier
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot "verify-demo-archive.ps1") `
        -Destination $archiveVerifier
    [System.IO.File]::WriteAllText($wheel, "synthetic wheel", [System.Text.UTF8Encoding]::new($false))
    [System.IO.File]::WriteAllText($source, "synthetic source", [System.Text.UTF8Encoding]::new($false))
    [System.IO.File]::WriteAllText(
        $demoRunbook, "synthetic demo runbook", [System.Text.UTF8Encoding]::new($false)
    )
    [System.IO.File]::WriteAllText(
        $tunnelHandoff, "synthetic tunnel handoff", [System.Text.UTF8Encoding]::new($false)
    )
    [System.IO.File]::WriteAllText(
        $chatgptEvaluation,
        "synthetic ChatGPT evaluation guide",
        [System.Text.UTF8Encoding]::new($false)
    )
    [System.IO.File]::WriteAllText(
        $completionAudit,
        "synthetic completion audit",
        [System.Text.UTF8Encoding]::new($false)
    )
    $wheelHash = (Get-FileHash -LiteralPath $wheel -Algorithm SHA256).Hash.ToLowerInvariant()
    $sourceHash = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash.ToLowerInvariant()
    $rootRef = "pkg:pypi/cadplot-mcp@0.1.0"
    $runtimeRef = "pkg:pypi/fixture@1.0"
    $sourceRef = "urn:cadplot:artifact:source-archive"
    $wheelRef = "urn:cadplot:artifact:wheel"
    $sbom = [ordered]@{
        '$schema' = "https://cyclonedx.org/schema/bom-1.7.schema.json"
        bomFormat = "CycloneDX"
        specVersion = "1.7"
        serialNumber = "urn:uuid:00000000-0000-5000-8000-000000000000"
        version = 1
        metadata = [ordered]@{
            timestamp = [DateTime]::UtcNow.ToString("o")
            tools = [ordered]@{ components = @([ordered]@{
                type = "application"; name = "cadplot-sbom"; version = "0.1.0"
            }) }
            component = [ordered]@{
                type = "application"; 'bom-ref' = $rootRef; name = "cadplot-mcp"
                version = "0.1.0"; purl = $rootRef
                licenses = @([ordered]@{ license = [ordered]@{ id = "MIT" } })
                properties = @(
                    [ordered]@{ name = "cadplot:repository-commit"; value = "0" * 40 },
                    [ordered]@{ name = "cadplot:uv-lock-sha256"; value = "e" * 64 },
                    [ordered]@{ name = "cadplot:dependency-requirements-sha256"; value = "f" * 64 },
                    [ordered]@{ name = "cadplot:autodesk-binaries-included"; value = "false" },
                    [ordered]@{ name = "cadplot:company-assets-included"; value = "false" },
                    [ordered]@{ name = "cadplot:autocad-launched"; value = "false" },
                    [ordered]@{ name = "cadplot:live-publish-proven"; value = "false" }
                )
            }
        }
        components = @(
            [ordered]@{
                type = "library"; 'bom-ref' = $runtimeRef; name = "fixture"; version = "1.0"
                purl = $runtimeRef
                licenses = @([ordered]@{ license = [ordered]@{ name = "MIT" } })
                properties = @(
                    [ordered]@{ name = "cadplot:ecosystem"; value = "python" },
                    [ordered]@{ name = "cadplot:scope"; value = "runtime" }
                )
            },
            [ordered]@{
                type = "file"; 'bom-ref' = $sourceRef; name = "source-archive"
                hashes = @([ordered]@{ alg = "SHA-256"; content = $sourceHash })
                properties = @([ordered]@{ name = "cadplot:release-artifact"; value = "true" })
            },
            [ordered]@{
                type = "file"; 'bom-ref' = $wheelRef; name = "wheel"
                hashes = @([ordered]@{ alg = "SHA-256"; content = $wheelHash })
                properties = @([ordered]@{ name = "cadplot:release-artifact"; value = "true" })
            }
        )
        dependencies = @(
            [ordered]@{ ref = $rootRef; dependsOn = @($runtimeRef, $sourceRef, $wheelRef) },
            [ordered]@{ ref = $runtimeRef; dependsOn = @() },
            [ordered]@{ ref = $sourceRef; dependsOn = @() },
            [ordered]@{ ref = $wheelRef; dependsOn = @() }
        )
        compositions = @([ordered]@{ aggregate = "incomplete"; assemblies = @($rootRef) })
    }
    [System.IO.File]::WriteAllText(
        $sbomPath,
        ($sbom | ConvertTo-Json -Depth 10),
        [System.Text.UTF8Encoding]::new($false)
    )
    $files = @(@(
        $verifier, $archiveVerifier, $wheel, $source, $demoRunbook, $tunnelHandoff,
        $chatgptEvaluation, $sbomPath, $completionAudit
    ) | ForEach-Object {
        [ordered]@{
            path = [System.IO.Path]::GetFileName($_)
            sha256 = (Get-FileHash -LiteralPath $_ -Algorithm SHA256).Hash.ToLowerInvariant()
        }
    })
    $manifest = [ordered]@{
        schema_version = 2
        exact_commit = "0" * 40
        package_version = "0.1.0"
        created_utc = [DateTime]::UtcNow.ToString("o")
        source_archive = [ordered]@{ file = [System.IO.Path]::GetFileName($source); sha256 = $files[3].sha256 }
        wheel = [ordered]@{ file = [System.IO.Path]::GetFileName($wheel); sha256 = $files[2].sha256 }
        sbom = [ordered]@{
            file = "cadplot-mcp.cdx.json"
            sha256 = $files[7].sha256
            spec_version = "1.7"
            component_count = 3
            runtime_dependency_count = 1
            artifact_count = 2
        }
        files = $files
        local_demo_ready = $true
        licensed_live_pilot_ready = $false
        public_release_ready = $false
        source_tree_audit_passed = $true
        api_probe_ran = $true
        api_probe = [ordered]@{
            passed = $true
            detected_series = "R24.3"
            target_framework = "net48"
            assemblies = @(
                [ordered]@{ Name = "AcMgd.dll"; Series = "R24.3"; Sha256 = "a" * 64 },
                [ordered]@{ Name = "AcDbMgd.dll"; Series = "R24.3"; Sha256 = "b" * 64 },
                [ordered]@{ Name = "AcCoreMgd.dll"; Series = "R24.3"; Sha256 = "c" * 64 }
            )
            autocad_launched = $false
            live_publish_proven = $false
            evidence_scope = "compile-only"
        }
        dependency_audit_ran = $true
        dependency_audit = [ordered]@{
            schema_version = 1
            generated_utc = [DateTime]::UtcNow.ToString("o")
            passed = $true
            lock = [ordered]@{
                file = "uv.lock"
                sha256 = "e" * 64
                requirements_sha256 = "f" * 64
            }
            python = [ordered]@{
                tool = "pip-audit fixture"
                package_count = 1
                vulnerability_count = 0
                database = "fixture"
            }
            python_license_inventory = [ordered]@{
                package_count = 1
                unknown_count = 0
                packages = @(
                    [ordered]@{ name = "fixture"; version = "1.0"; license = "MIT" }
                )
            }
            dotnet = [ordered]@{
                tool = "dotnet fixture"
                project_count = 4
                vulnerability_count = 0
                source_count = 1
                database = "fixture"
            }
            network_database_check = $true
            autocad_launched = $false
            live_publish_proven = $false
        }
        wheel_install_smoke = [ordered]@{
            passed = $true
            version = "0.1.0"
            wheel_sha256 = $files[2].sha256
            protocol_version = "2025-11-25"
            tool_count = 20
            http_transport_tool_count = 20
            http_transport_loopback_only = $true
            http_transport_header_guards = $true
            tunnel_preflight_redacted = $true
            tunnel_preflight_target_probed = $true
            tunnel_preflight_tool_surface_sha256 = "a" * 64
            chatgpt_eval_plan_prepared = $true
            chatgpt_eval_case_count = 13
            sbom_cli_verified = $true
            isolated_install = $true
            locked_dependencies = $true
            dependency_hashes_required = $true
            autocad_launched = $false
            live_tunnel_proven = $false
            live_publish_proven = $false
        }
        durable_queue_recovery = [ordered]@{
            passed = $true
            exact_test_count = 15
            pending_intent_recovered = $true
            exact_request_identity_preserved = $true
            interrupted_job_not_replayed = $true
            terminal_receipt_status_recovered = $true
            tampered_intent_blocked = $true
            completed_job_requeue_blocked = $true
            authentication_scheme = "windows-dpapi-current-user+hmac-sha256-v1"
            signed_intent_required = $true
            foreign_key_intent_blocked = $true
            started_marker_authentication_required = $true
            pending_cancellation_durable = $true
            cancelled_job_not_replayed = $true
            cancelled_marker_authentication_required = $true
            running_job_not_cancelled = $true
            protected_key_outside_workspace = $true
            workspace_key_rejected = $true
            corrupt_key_blocked = $true
            net45_dpapi_runtime_proven = $true
            net45_core_image_runtime = "v4.0.30319"
            autocad_launched = $false
            live_publish_proven = $false
            evidence_scope = "production-core-net45+net8-with-synthetic-files"
        }
        synthetic_batch_rehearsal = [ordered]@{
            target_drawings = 300; ready = 300; staged = 300; outputs_complete = 300
            queue_capacity = 7; queue_waves = 45; queue_approvals = 300
            queue_simulated_acceptances = 300; queue_deferred_results = 285
            queue_pipe_attempts = 330; queue_exact_retry_identity_preserved = $true
            queue_status_batches = 15; queue_status_items = 300; queue_status_pending = 300
            queue_status_identity_preserved = $true
            queue_plugin_contacted = $false
            execution_verified = 0; publish_verified = 0; manual_review_without_receipts = 300
            source_unchanged = $true; evidence_digest = "sha256:$('d' * 64)"; synthetic = $true
        }
        company_assets_copied = $false
        autodesk_binaries_included = $false
        autocad_launched = $false
        live_publish_proven = $false
        purpose = "Synthetic verifier fixture"
    }
    [System.IO.File]::WriteAllText(
        (Join-Path $resolvedRoot "demo-kit.json"),
        ($manifest | ConvertTo-Json -Depth 7),
        [System.Text.UTF8Encoding]::new($false)
    )

    $result = & $verifier -KitRoot $resolvedRoot -PassThru
    if (
        $result.Passed -ne $true -or
        $result.MachinePathsIncluded -ne $false -or
        $result.DependencyAuditPassed -ne $true -or
        $result.SbomVerified -ne $true
    ) {
        throw "Demo-kit verifier did not accept the exact path-redacted fixture."
    }

    $null = New-Item -ItemType Directory -Path $resolvedArchiveRoot
    $archiveKitName = "cadplot-demo-kit-0000000"
    $archiveKitRoot = Join-Path $resolvedArchiveRoot $archiveKitName
    Copy-Item -LiteralPath $resolvedRoot -Destination $archiveKitRoot -Recurse
    $archivePath = Join-Path $resolvedArchiveRoot "$archiveKitName.zip"
    Compress-Archive -LiteralPath $archiveKitRoot -DestinationPath $archivePath `
        -CompressionLevel Optimal
    $archiveManifestPath = Join-Path $resolvedArchiveRoot "demo-kit-build.json"
    $archiveManifest = [ordered]@{
        schema_version = 1
        exact_commit = "0" * 40
        package_version = "0.1.0"
        created_utc = [DateTime]::UtcNow.ToString("o")
        kit_directory = $archiveKitName
        kit_archive = "$archiveKitName.zip"
        kit_manifest = "demo-kit.json"
        kit_manifest_sha256 = (
            Get-FileHash -LiteralPath (Join-Path $archiveKitRoot "demo-kit.json") -Algorithm SHA256
        ).Hash.ToLowerInvariant()
        kit_archive_sha256 = (
            Get-FileHash -LiteralPath $archivePath -Algorithm SHA256
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
    [System.IO.File]::WriteAllText(
        $archiveManifestPath,
        ($archiveManifest | ConvertTo-Json -Depth 5),
        [System.Text.UTF8Encoding]::new($false)
    )
    $outerVerifier = Join-Path $archiveKitRoot "verify-demo-archive.ps1"
    $archiveResult = & $outerVerifier -DeliveryRoot $resolvedArchiveRoot -PassThru
    if (
        $archiveResult.Passed -ne $true -or
        $archiveResult.ArchiveFileCount -ne 10 -or
        $archiveResult.MachinePathsIncluded -ne $false -or
        $archiveResult.AutoCADLaunched -ne $false -or
        $archiveResult.LivePublishProven -ne $false
    ) { throw "Demo archive verifier rejected the exact synthetic delivery." }

    $archiveManifestBytes = [System.IO.File]::ReadAllBytes($archiveManifestPath)
    $archiveBytes = [System.IO.File]::ReadAllBytes($archivePath)
    $outerIdentityTamper = [System.Text.Encoding]::UTF8.GetString(
        $archiveManifestBytes
    ) | ConvertFrom-Json
    $outerIdentityTamper.exact_commit = "1" * 40
    [System.IO.File]::WriteAllText(
        $archiveManifestPath,
        ($outerIdentityTamper | ConvertTo-Json -Depth 5),
        [System.Text.UTF8Encoding]::new($false)
    )
    $outerIdentityTamperBlocked = $false
    try { & $outerVerifier -DeliveryRoot $resolvedArchiveRoot -PassThru }
    catch {
        if ($_.Exception.Message -notlike "*identity or safety/evidence fields*") { throw }
        $outerIdentityTamperBlocked = $true
    }
    if (-not $outerIdentityTamperBlocked) {
        throw "Demo archive verifier accepted altered outer identity."
    }
    [System.IO.File]::WriteAllBytes($archiveManifestPath, $archiveManifestBytes)

    $archiveBytes[0] = $archiveBytes[0] -bxor 1
    [System.IO.File]::WriteAllBytes($archivePath, $archiveBytes)
    $archiveHashTamperBlocked = $false
    try { & $outerVerifier -DeliveryRoot $resolvedArchiveRoot -PassThru }
    catch {
        if ($_.Exception.Message -notlike "*archive hash mismatch*") { throw }
        $archiveHashTamperBlocked = $true
    }
    if (-not $archiveHashTamperBlocked) {
        throw "Demo archive verifier accepted a modified archive."
    }
    $archiveBytes[0] = $archiveBytes[0] -bxor 1
    [System.IO.File]::WriteAllBytes($archivePath, $archiveBytes)

    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $updatedArchive = [System.IO.Compression.ZipFile]::Open(
        $archivePath,
        [System.IO.Compression.ZipArchiveMode]::Update
    )
    try {
        $unsafeEntry = $updatedArchive.CreateEntry("$archiveKitName/../escape.txt")
        $unsafeStream = $unsafeEntry.Open()
        try {
            $unsafeBytes = [System.Text.Encoding]::UTF8.GetBytes("unsafe")
            $unsafeStream.Write($unsafeBytes, 0, $unsafeBytes.Length)
        }
        finally { $unsafeStream.Dispose() }
    }
    finally { $updatedArchive.Dispose() }
    $traversalOuter = [System.Text.Encoding]::UTF8.GetString(
        $archiveManifestBytes
    ) | ConvertFrom-Json
    $traversalOuter.kit_archive_sha256 = (
        Get-FileHash -LiteralPath $archivePath -Algorithm SHA256
    ).Hash.ToLowerInvariant()
    [System.IO.File]::WriteAllText(
        $archiveManifestPath,
        ($traversalOuter | ConvertTo-Json -Depth 5),
        [System.Text.UTF8Encoding]::new($false)
    )
    $archiveTraversalBlocked = $false
    try { & $outerVerifier -DeliveryRoot $resolvedArchiveRoot -PassThru }
    catch {
        if ($_.Exception.Message -notlike "*unsafe entry name or size*") { throw }
        $archiveTraversalBlocked = $true
    }
    if (-not $archiveTraversalBlocked) {
        throw "Demo archive verifier accepted a traversal entry with a refreshed outer hash."
    }
    [System.IO.File]::WriteAllBytes($archivePath, $archiveBytes)
    [System.IO.File]::WriteAllBytes($archiveManifestPath, $archiveManifestBytes)
    $manifestPath = Join-Path $resolvedRoot "demo-kit.json"
    $manifestBytes = [System.IO.File]::ReadAllBytes($manifestPath)
    $manifest.dependency_audit.python_license_inventory.packages[0].license = "UNKNOWN"
    [System.IO.File]::WriteAllText(
        $manifestPath,
        ($manifest | ConvertTo-Json -Depth 7),
        [System.Text.UTF8Encoding]::new($false)
    )
    $licenseTamperBlocked = $false
    try { & $verifier -KitRoot $resolvedRoot -PassThru }
    catch {
        if ($_.Exception.Message -notlike "*license inventory*") { throw }
        $licenseTamperBlocked = $true
    }
    if (-not $licenseTamperBlocked) {
        throw "Demo-kit verifier accepted an unknown dependency license."
    }
    [System.IO.File]::WriteAllBytes($manifestPath, $manifestBytes)
    $queueTamper = [System.Text.Encoding]::UTF8.GetString($manifestBytes) | ConvertFrom-Json
    $queueTamper.synthetic_batch_rehearsal.queue_deferred_results = 284
    [System.IO.File]::WriteAllText(
        $manifestPath,
        ($queueTamper | ConvertTo-Json -Depth 7),
        [System.Text.UTF8Encoding]::new($false)
    )
    $queueTamperBlocked = $false
    try { & $verifier -KitRoot $resolvedRoot -PassThru }
    catch {
        if ($_.Exception.Message -notlike "*300-drawing synthetic batch evidence*") { throw }
        $queueTamperBlocked = $true
    }
    if (-not $queueTamperBlocked) {
        throw "Demo-kit verifier accepted altered queue-backpressure evidence."
    }
    [System.IO.File]::WriteAllBytes($manifestPath, $manifestBytes)
    $durableTamper = [System.Text.Encoding]::UTF8.GetString($manifestBytes) | ConvertFrom-Json
    $durableTamper.durable_queue_recovery.authentication_scheme = "unsigned"
    [System.IO.File]::WriteAllText(
        $manifestPath,
        ($durableTamper | ConvertTo-Json -Depth 7),
        [System.Text.UTF8Encoding]::new($false)
    )
    $durableTamperBlocked = $false
    try { & $verifier -KitRoot $resolvedRoot -PassThru }
    catch {
        if ($_.Exception.Message -notlike "*durable queue recovery evidence*") { throw }
        $durableTamperBlocked = $true
    }
    if (-not $durableTamperBlocked) {
        throw "Demo-kit verifier accepted altered durable queue recovery evidence."
    }
    [System.IO.File]::WriteAllBytes($manifestPath, $manifestBytes)
    $targetProbeTamper = [System.Text.Encoding]::UTF8.GetString($manifestBytes) | ConvertFrom-Json
    $targetProbeTamper.wheel_install_smoke.tunnel_preflight_target_probed = $false
    [System.IO.File]::WriteAllText(
        $manifestPath,
        ($targetProbeTamper | ConvertTo-Json -Depth 7),
        [System.Text.UTF8Encoding]::new($false)
    )
    $targetProbeTamperBlocked = $false
    try { & $verifier -KitRoot $resolvedRoot -PassThru }
    catch {
        if ($_.Exception.Message -notlike "*local-target probe evidence*") { throw }
        $targetProbeTamperBlocked = $true
    }
    if (-not $targetProbeTamperBlocked) {
        throw "Demo-kit verifier accepted altered local-target probe evidence."
    }
    [System.IO.File]::WriteAllBytes($manifestPath, $manifestBytes)
    $sbomBytes = [System.IO.File]::ReadAllBytes($sbomPath)
    $sbomTamper = [System.Text.Encoding]::UTF8.GetString($sbomBytes) | ConvertFrom-Json
    $liveProperty = @($sbomTamper.metadata.component.properties | Where-Object {
        $_.name -ceq "cadplot:live-publish-proven"
    })
    $liveProperty[0].value = "true"
    [System.IO.File]::WriteAllText(
        $sbomPath,
        ($sbomTamper | ConvertTo-Json -Depth 10),
        [System.Text.UTF8Encoding]::new($false)
    )
    $semanticTamper = [System.Text.Encoding]::UTF8.GetString($manifestBytes) | ConvertFrom-Json
    $semanticHash = (Get-FileHash -LiteralPath $sbomPath -Algorithm SHA256).Hash.ToLowerInvariant()
    $semanticTamper.sbom.sha256 = $semanticHash
    @($semanticTamper.files | Where-Object { $_.path -ceq "cadplot-mcp.cdx.json" })[0].sha256 = $semanticHash
    [System.IO.File]::WriteAllText(
        $manifestPath,
        ($semanticTamper | ConvertTo-Json -Depth 7),
        [System.Text.UTF8Encoding]::new($false)
    )
    $sbomSemanticTamperBlocked = $false
    try { & $verifier -KitRoot $resolvedRoot -PassThru }
    catch {
        if ($_.Exception.Message -notlike "*SBOM release binding or evidence boundaries*") { throw }
        $sbomSemanticTamperBlocked = $true
    }
    if (-not $sbomSemanticTamperBlocked) {
        throw "Demo-kit verifier accepted altered SBOM evidence boundaries."
    }
    [System.IO.File]::WriteAllBytes($sbomPath, $sbomBytes)
    [System.IO.File]::WriteAllBytes($manifestPath, $manifestBytes)
    [System.IO.File]::AppendAllText($wheel, "tamper", [System.Text.UTF8Encoding]::new($false))
    $tamperBlocked = $false
    try { & $verifier -KitRoot $resolvedRoot -PassThru }
    catch {
        if ($_.Exception.Message -notlike "*file hash mismatch*") { throw }
        $tamperBlocked = $true
    }
    if (-not $tamperBlocked) { throw "Demo-kit verifier accepted a modified wheel." }

    [ordered]@{
        passed = $true
        exact_tree_and_hashes_verified = $true
        machine_paths_redacted = $true
        dependency_audit_verified = $true
        sbom_verified = $true
        sbom_semantic_tamper_blocked = $sbomSemanticTamperBlocked
        archive_verified = $true
        archive_outer_identity_tamper_blocked = $outerIdentityTamperBlocked
        archive_hash_tamper_blocked = $archiveHashTamperBlocked
        archive_traversal_tamper_blocked = $archiveTraversalBlocked
        dependency_license_tamper_blocked = $licenseTamperBlocked
        queue_backpressure_tamper_blocked = $queueTamperBlocked
        durable_queue_tamper_blocked = $durableTamperBlocked
        tunnel_target_probe_tamper_blocked = $targetProbeTamperBlocked
        wheel_tamper_blocked = $true
        autocad_launched = $false
        live_publish_proven = $false
    } | ConvertTo-Json
}
finally {
    if (Test-Path -LiteralPath $resolvedRoot) {
        Remove-Item -LiteralPath $resolvedRoot -Recurse -Force
    }
    if (Test-Path -LiteralPath $resolvedArchiveRoot) {
        Remove-Item -LiteralPath $resolvedArchiveRoot -Recurse -Force
    }
}
