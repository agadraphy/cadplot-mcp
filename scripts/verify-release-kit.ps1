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
    $batch.outputs_complete -ne 300 -or
    $batch.execution_verified -ne 0 -or
    $batch.publish_verified -ne 0 -or
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
    "LICENSE", "README.md", "README.tr.md", "THIRD_PARTY_NOTICES.md",
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
    "scripts/install-release-kit.ps1",
    "scripts/check-autocad-api-series.ps1",
    "scripts/new-local-pilot.ps1", "scripts/collect-pilot-run.py",
    "scripts/assemble-pilot-evidence.py", "scripts/validate-pilot-evidence.py",
    "docs/monday-pilot.md", "docs/pazartesi-demo-tr.md", "docs/release-checklist.md",
    "docs/release-kit-install.md", "docs/pilot-evidence.md", "docs/deployment-modes.md",
    "docs/chatgpt-connection.md", "docs/loopback-http.md",
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
if ($manifest.wheel.file -cne $wheelRelative -or $manifest.wheel.sha256 -cne $wheelHash) {
    throw "Release-kit wheel evidence is invalid."
}
if ($manifest.source_archive -cne "source/$($sourceFiles[0].Name)") {
    throw "Release-kit source archive evidence is invalid."
}

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
    LocalDemoReady = $true
    AutoCADLaunched = $false
    LivePublishProven = $false
}
if ($PassThru) { $result }
else { $result | ConvertTo-Json -Depth 3 }
