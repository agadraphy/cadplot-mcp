[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$BundleReleaseRoot
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$resolvedBundleRelease = [System.IO.Path]::GetFullPath($BundleReleaseRoot)
$bundleEvidence = & (Join-Path $PSScriptRoot "verify-bundle-release.ps1") `
    -ReleaseRoot $resolvedBundleRelease `
    -PassThru `
    -AllowProtocolOnlyFixture
if (
    $bundleEvidence.MatchingSdkBundleBuilt -ne $false -or
    $bundleEvidence.ProtocolOnlyFixture -ne $true
) {
    throw "Release-kit smoke requires a protocol-only bundle fixture."
}

$smokeRoot = Join-Path (
    [System.IO.Path]::GetTempPath()
) ("cadplot-release-kit-smoke-{0}" -f [Guid]::NewGuid().ToString("N"))
$resolvedSmokeRoot = [System.IO.Path]::GetFullPath($smokeRoot)
$resolvedTempRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath()).TrimEnd('\') + '\'
if (-not $resolvedSmokeRoot.StartsWith($resolvedTempRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Release-kit smoke root escaped the system temporary directory."
}

function Write-SmokeJson {
    param([string]$Path, $Value)
    [System.IO.File]::WriteAllText(
        $Path,
        ($Value | ConvertTo-Json -Depth 7),
        [System.Text.UTF8Encoding]::new($false)
    )
}

try {
    $kitRoot = Join-Path $resolvedSmokeRoot "CadPlotMcp.release"
    $null = New-Item -ItemType Directory -Path $kitRoot
    foreach ($directory in @("autocad", "python", "source", "scripts", "docs", "config")) {
        $null = New-Item -ItemType Directory -Path (Join-Path $kitRoot $directory)
    }
    Copy-Item -LiteralPath (Join-Path $resolvedBundleRelease "CadPlotMcp.bundle") `
        -Destination (Join-Path $kitRoot "autocad\CadPlotMcp.bundle") -Recurse
    Copy-Item -LiteralPath (Join-Path $resolvedBundleRelease "CadPlotMcp.bundle.zip") `
        -Destination (Join-Path $kitRoot "autocad\CadPlotMcp.bundle.zip")
    Copy-Item -LiteralPath (Join-Path $resolvedBundleRelease "bundle-build.json") `
        -Destination (Join-Path $kitRoot "autocad\bundle-build.json")

    $wheels = @(Get-ChildItem -LiteralPath (Join-Path $repoRoot "dist") -File `
        -Filter "cadplot_mcp-*-py3-none-any.whl")
    if ($wheels.Count -ne 1) { throw "Release-kit smoke expected exactly one built wheel." }
    Copy-Item -LiteralPath $wheels[0].FullName -Destination (Join-Path $kitRoot "python")
    Copy-Item -LiteralPath (Join-Path $repoRoot "pyproject.toml") -Destination (Join-Path $kitRoot "python")
    Copy-Item -LiteralPath (Join-Path $repoRoot "uv.lock") -Destination (Join-Path $kitRoot "python")
    $sourceName = "cadplot-mcp-source-0000000.zip"
    $sourcePath = Join-Path $kitRoot "source\$sourceName"
    & git -C $repoRoot archive --format=zip --output=$sourcePath HEAD
    if ($LASTEXITCODE -ne 0) { throw "Release-kit fixture source archive failed." }

    foreach ($scriptName in @(
        "install-bundle.ps1", "uninstall-bundle.ps1", "verify-bundle.ps1",
        "verify-bundle-release.ps1", "verify-release-kit.ps1",
        "check-autocad-api-series.ps1", "new-local-pilot.ps1",
        "collect-pilot-run.py", "assemble-pilot-evidence.py",
        "validate-pilot-evidence.py"
    )) {
        Copy-Item -LiteralPath (Join-Path $PSScriptRoot $scriptName) `
            -Destination (Join-Path $kitRoot "scripts\$scriptName")
    }
    foreach ($docName in @(
        "monday-pilot.md", "pazartesi-demo-tr.md", "release-checklist.md",
        "release-kit-install.md", "pilot-evidence.md"
    )) {
        Copy-Item -LiteralPath (Join-Path $repoRoot "docs\$docName") `
            -Destination (Join-Path $kitRoot "docs\$docName")
    }
    Copy-Item -LiteralPath (Join-Path $repoRoot "examples\config.inventory.example.yaml") `
        -Destination (Join-Path $kitRoot "config\config.inventory.example.yaml")
    foreach ($fileName in @("LICENSE", "README.md", "README.tr.md", "THIRD_PARTY_NOTICES.md")) {
        Copy-Item -LiteralPath (Join-Path $repoRoot $fileName) -Destination $kitRoot
    }

    $wheelName = $wheels[0].Name
    $wheelPath = Join-Path $kitRoot "python\$wheelName"
    $dependencyAudit = [ordered]@{
        schema_version = 1
        generated_utc = [DateTime]::UtcNow.ToString("o")
        passed = $true
        lock = [ordered]@{
            file = "uv.lock"
            sha256 = (Get-FileHash -LiteralPath (Join-Path $kitRoot "python\uv.lock") -Algorithm SHA256).Hash.ToLowerInvariant()
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
    $files = @(Get-ChildItem -LiteralPath $kitRoot -File -Recurse | Sort-Object FullName | ForEach-Object {
        [ordered]@{
            path = $_.FullName.Substring($kitRoot.Length + 1).Replace('\', '/')
            sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        }
    })
    $manifestPath = Join-Path $kitRoot "release-kit.json"
    Write-SmokeJson -Path $manifestPath -Value ([ordered]@{
        schema_version = 1
        exact_commit = $bundleEvidence.ExactCommit
        package_version = $bundleEvidence.PackageVersion
        created_utc = [DateTime]::UtcNow.ToString("o")
        wheel = [ordered]@{
            file = "python/$wheelName"
            sha256 = (Get-FileHash -LiteralPath $wheelPath -Algorithm SHA256).Hash.ToLowerInvariant()
        }
        source_archive = "source/$sourceName"
        files = $files
        dependency_audit_ran = $true
        dependency_audit = $dependencyAudit
        synthetic_batch_rehearsal = [ordered]@{
            target_drawings = 300
            planning_pages = 15
            ready = 300
            staging_batches = 15
            staged = 300
            restart_pages_before_outputs = 6
            restart_pages_after_outputs = 6
            outputs_complete = 300
            execution_verified = 0
            publish_verified = 0
            manual_review_without_receipts = 300
            source_unchanged = $true
            evidence_digest = "sha256:$('c' * 64)"
            synthetic = $true
        }
        matching_sdk_bundle_built = $false
        protocol_only_fixture = $true
        local_demo_ready = $true
        licensed_live_pilot_ready = $false
        public_release_ready = $false
        company_assets_copied = $false
        autodesk_binaries_included = $false
        autocad_launched = $false
        live_publish_proven = $false
    })
    $archivePath = Join-Path $resolvedSmokeRoot "CadPlotMcp.release.zip"
    Compress-Archive -LiteralPath $kitRoot -DestinationPath $archivePath -CompressionLevel Optimal
    $outerPath = Join-Path $resolvedSmokeRoot "release-kit-build.json"
    Write-SmokeJson -Path $outerPath -Value ([ordered]@{
        schema_version = 1
        exact_commit = $bundleEvidence.ExactCommit
        package_version = $bundleEvidence.PackageVersion
        created_utc = [DateTime]::UtcNow.ToString("o")
        kit_directory = "CadPlotMcp.release"
        kit_archive = "CadPlotMcp.release.zip"
        kit_manifest_sha256 = (Get-FileHash -LiteralPath $manifestPath -Algorithm SHA256).Hash.ToLowerInvariant()
        kit_archive_sha256 = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant()
        dependency_audit_ran = $true
        dependency_audit = $dependencyAudit
        matching_sdk_bundle_built = $false
        protocol_only_fixture = $true
        local_demo_ready = $true
        licensed_live_pilot_ready = $false
        public_release_ready = $false
        company_assets_copied = $false
        autodesk_binaries_included = $false
        autocad_launched = $false
        live_publish_proven = $false
    })

    $verifier = Join-Path $PSScriptRoot "verify-release-kit.ps1"
    $rejectedAsReal = $false
    try { & $verifier -ReleaseRoot $resolvedSmokeRoot -PassThru }
    catch {
        if ($_.Exception.Message -notlike "*not a matching-SDK build*") { throw }
        $rejectedAsReal = $true
    }
    if (-not $rejectedAsReal) { throw "Release-kit verifier accepted a protocol fixture as real." }
    $embeddedVerifier = Join-Path $kitRoot "scripts\verify-release-kit.ps1"
    $embeddedResult = & $embeddedVerifier `
        -ReleaseRoot $resolvedSmokeRoot `
        -PassThru `
        -AllowProtocolOnlyFixture
    if ($embeddedResult.Passed -ne $true -or $embeddedResult.DependencyAuditPassed -ne $true) {
        throw "Embedded release-kit verifier did not pass its own exact package."
    }

    $outerBytes = [System.IO.File]::ReadAllBytes($outerPath)
    $tamperedOuter = Get-Content -LiteralPath $outerPath -Raw | ConvertFrom-Json
    $tamperedOuter.dependency_audit.python_license_inventory.packages[0].license = "UNKNOWN"
    Write-SmokeJson -Path $outerPath -Value $tamperedOuter
    $licenseTamperBlocked = $false
    try { & $verifier -ReleaseRoot $resolvedSmokeRoot -PassThru -AllowProtocolOnlyFixture }
    catch {
        if ($_.Exception.Message -notlike "*license inventory*") { throw }
        $licenseTamperBlocked = $true
    }
    if (-not $licenseTamperBlocked) {
        throw "Release-kit verifier accepted an unknown dependency license."
    }
    [System.IO.File]::WriteAllBytes($outerPath, $outerBytes)

    $archiveBytes = [System.IO.File]::ReadAllBytes($archivePath)
    $archiveBytes[0] = $archiveBytes[0] -bxor 1
    [System.IO.File]::WriteAllBytes($archivePath, $archiveBytes)
    $tamperBlocked = $false
    try { & $verifier -ReleaseRoot $resolvedSmokeRoot -PassThru -AllowProtocolOnlyFixture }
    catch {
        if ($_.Exception.Message -notlike "*outer hash evidence does not match*") { throw }
        $tamperBlocked = $true
    }
    if (-not $tamperBlocked) { throw "Release-kit verifier accepted a modified outer archive." }

    [ordered]@{
        passed = $true
        protocol_only_fixture = $true
        protocol_only_rejected_as_real = $rejectedAsReal
        exact_tree_and_hashes_verified = $true
        embedded_self_verification_passed = $true
        dependency_audit_verified = $true
        dependency_license_tamper_blocked = $licenseTamperBlocked
        archive_tamper_blocked = $tamperBlocked
        matching_sdk_bundle_built = $false
        autocad_launched = $false
        live_publish_proven = $false
    } | ConvertTo-Json
}
finally {
    if (Test-Path -LiteralPath $resolvedSmokeRoot) {
        Remove-Item -LiteralPath $resolvedSmokeRoot -Recurse -Force
    }
}
