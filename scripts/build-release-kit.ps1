[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$BundleReleaseRoot,

    [Parameter(Mandatory = $true)]
    [string]$ReadinessReport,

    [string]$OutputRoot = ""
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot

function Invoke-GitReadOnly {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)

    $output = @(& git -C $repoRoot @Arguments 2>&1)
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
            throw "No existing ancestor was found for release-kit output: $Path"
        }
        $current = $parent
    }
    while (-not [string]::IsNullOrWhiteSpace($current)) {
        $item = Get-Item -LiteralPath $current -Force
        if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Release-kit output must not pass through a symlink or junction: $current"
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
        [int]$Depth = 7
    )

    $json = $Value | ConvertTo-Json -Depth $Depth
    $bytes = [System.Text.UTF8Encoding]::new($false).GetBytes($json)
    $stream = [System.IO.File]::Open(
        $Path,
        [System.IO.FileMode]::CreateNew,
        [System.IO.FileAccess]::Write,
        [System.IO.FileShare]::Read
    )
    try { $stream.Write($bytes, 0, $bytes.Length) }
    finally { $stream.Dispose() }
}

Push-Location $repoRoot
try {
    $commit = @(Invoke-GitReadOnly -Arguments @("rev-parse", "HEAD"))[0].Trim()
    if ($commit -notmatch "^[0-9a-f]{40}$") {
        throw "Could not resolve an exact 40-character Git commit."
    }
    $worktreeChanges = @(Invoke-GitReadOnly -Arguments @(
        "status", "--porcelain=v1", "--untracked-files=all"
    ))
    if ($worktreeChanges.Count -ne 0) {
        throw "Release-kit build requires a clean worktree."
    }

    $resolvedBundleRelease = [System.IO.Path]::GetFullPath($BundleReleaseRoot)
    $bundleEvidence = & (Join-Path $PSScriptRoot "verify-bundle-release.ps1") `
        -ReleaseRoot $resolvedBundleRelease `
        -PassThru
    if (
        $bundleEvidence.ExactCommit -cne $commit -or
        $bundleEvidence.MatchingSdkBundleBuilt -ne $true -or
        $bundleEvidence.ProtocolOnlyFixture -ne $false -or
        $bundleEvidence.LivePublishProven -ne $false
    ) {
        throw "Bundle release is not a production matching-SDK artifact for the current commit."
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
    if (
        $readiness.passed -ne $true -or
        $readiness.local_demo_ready -ne $true -or
        $readiness.worktree_clean -ne $true -or
        $readiness.licensed_live_pilot_ready -ne $false -or
        $readiness.public_release_ready -ne $false -or
        $readiness.autocad_launched -ne $false -or
        $readiness.live_publish_proven -ne $false -or
        $readiness.company_assets_copied -ne $false -or
        $readiness.exact_commit -cne $commit
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
    $lockHash = (Get-FileHash -LiteralPath (Join-Path $repoRoot "uv.lock") -Algorithm SHA256).Hash.ToLowerInvariant()
    if (
        $readiness.dependency_audit_ran -ne $true -or
        $readiness.dependency_audit.passed -ne $true -or
        $readiness.dependency_audit.lock.file -cne "uv.lock" -or
        $readiness.dependency_audit.lock.sha256 -cne $lockHash -or
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
    ) {
        throw "Release kit requires current lock-bound dependency-audit evidence."
    }
    $licensePackages = @($readiness.dependency_audit.python_license_inventory.packages)
    if (@($licensePackages | Group-Object -Property name | Where-Object Count -ne 1).Count -ne 0) {
        throw "Release dependency license inventory contains duplicate package names."
    }
    foreach ($package in $licensePackages) {
        if (
            [string]::IsNullOrWhiteSpace([string]$package.name) -or
            [string]::IsNullOrWhiteSpace([string]$package.version) -or
            [string]::IsNullOrWhiteSpace([string]$package.license) -or
            [string]$package.license -ceq "UNKNOWN"
        ) {
            throw "Release dependency license inventory is incomplete."
        }
    }
    $batch = $readiness.synthetic_batch_rehearsal
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
        throw "Readiness report has no valid 300-drawing synthetic batch evidence."
    }

    $wheelName = [string]$readiness.wheel
    if ($wheelName -notmatch '^cadplot_mcp-[0-9A-Za-z.]+-py3-none-any\.whl$') {
        throw "Readiness report has an invalid wheel name."
    }
    $wheelPath = Join-Path $repoRoot ("dist\{0}" -f $wheelName)
    if (-not (Test-Path -LiteralPath $wheelPath -PathType Leaf)) {
        throw "Readiness-bound wheel is missing: $wheelPath"
    }
    $wheelHash = (Get-FileHash -LiteralPath $wheelPath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($wheelHash -cne [string]$readiness.wheel_sha256) {
        throw "Wheel hash no longer matches the readiness report."
    }
    if ($bundleEvidence.PackageVersion -cne (($wheelName -split '-')[1])) {
        throw "Python wheel and AutoCAD bundle versions do not match."
    }

    if ([string]::IsNullOrWhiteSpace($OutputRoot)) {
        $OutputRoot = Join-Path $repoRoot (
            "artifacts\cadplot-release-kit-{0}-{1}" -f `
                $bundleEvidence.PackageVersion,$commit.Substring(0, 7)
        )
    }
    $resolvedOutputRoot = [System.IO.Path]::GetFullPath($OutputRoot)
    if (Test-Path -LiteralPath $resolvedOutputRoot) {
        throw "Release-kit target already exists; build never overwrites: $resolvedOutputRoot"
    }
    Assert-NoRedirectedAncestor -Path $resolvedOutputRoot

    $null = New-Item -ItemType Directory -Path $resolvedOutputRoot
    $kitRoot = Join-Path $resolvedOutputRoot "CadPlotMcp.release"
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

    Copy-Item -LiteralPath $wheelPath -Destination (Join-Path $kitRoot "python\$wheelName")
    Copy-Item -LiteralPath (Join-Path $repoRoot "pyproject.toml") `
        -Destination (Join-Path $kitRoot "python\pyproject.toml")
    Copy-Item -LiteralPath (Join-Path $repoRoot "uv.lock") `
        -Destination (Join-Path $kitRoot "python\uv.lock")

    $sourceName = "cadplot-mcp-source-$($commit.Substring(0, 7)).zip"
    $sourcePath = Join-Path $kitRoot "source\$sourceName"
    & git archive --format=zip --output=$sourcePath $commit
    if ($LASTEXITCODE -ne 0) {
        throw "Git source archive failed. The partial release kit was retained for inspection."
    }

    foreach ($scriptName in @(
        "install-bundle.ps1", "uninstall-bundle.ps1", "verify-bundle.ps1",
        "verify-bundle-release.ps1", "verify-release-kit.ps1",
        "install-python.ps1", "verify-python-install.ps1",
        "check-autocad-api-series.ps1", "new-local-pilot.ps1",
        "collect-pilot-run.py", "assemble-pilot-evidence.py",
        "validate-pilot-evidence.py"
    )) {
        Copy-Item -LiteralPath (Join-Path $PSScriptRoot $scriptName) `
            -Destination (Join-Path $kitRoot "scripts\$scriptName")
    }
    foreach ($docName in @(
        "monday-pilot.md", "pazartesi-demo-tr.md", "release-checklist.md",
        "release-kit-install.md", "pilot-evidence.md", "deployment-modes.md",
        "chatgpt-connection.md", "loopback-http.md"
    )) {
        Copy-Item -LiteralPath (Join-Path $repoRoot "docs\$docName") `
            -Destination (Join-Path $kitRoot "docs\$docName")
    }
    Copy-Item -LiteralPath (Join-Path $repoRoot "examples\config.inventory.example.yaml") `
        -Destination (Join-Path $kitRoot "config\config.inventory.example.yaml")
    foreach ($fileName in @("LICENSE", "README.md", "README.tr.md", "THIRD_PARTY_NOTICES.md")) {
        Copy-Item -LiteralPath (Join-Path $repoRoot $fileName) -Destination $kitRoot
    }

    $kitFiles = @(Get-ChildItem -LiteralPath $kitRoot -File -Recurse | Sort-Object FullName)
    $fileEvidence = @($kitFiles | ForEach-Object {
        [ordered]@{
            path = $_.FullName.Substring($kitRoot.Length + 1).Replace('\', '/')
            sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        }
    })
    $kitManifestPath = Join-Path $kitRoot "release-kit.json"
    Write-NewUtf8Json -Path $kitManifestPath -Value ([ordered]@{
        schema_version = 1
        exact_commit = $commit
        package_version = $bundleEvidence.PackageVersion
        created_utc = [DateTime]::UtcNow.ToString("o")
        wheel = [ordered]@{ file = "python/$wheelName"; sha256 = $wheelHash }
        source_archive = "source/$sourceName"
        files = $fileEvidence
        dependency_audit_ran = $true
        dependency_audit = $readiness.dependency_audit
        synthetic_batch_rehearsal = $batch
        matching_sdk_bundle_built = $true
        local_demo_ready = $true
        licensed_live_pilot_ready = $false
        public_release_ready = $false
        company_assets_copied = $false
        autodesk_binaries_included = $false
        autocad_launched = $false
        live_publish_proven = $false
    })

    $kitArchive = Join-Path $resolvedOutputRoot "CadPlotMcp.release.zip"
    Compress-Archive -LiteralPath $kitRoot -DestinationPath $kitArchive -CompressionLevel Optimal
    $outerManifestPath = Join-Path $resolvedOutputRoot "release-kit-build.json"
    Write-NewUtf8Json -Path $outerManifestPath -Value ([ordered]@{
        schema_version = 1
        exact_commit = $commit
        package_version = $bundleEvidence.PackageVersion
        created_utc = [DateTime]::UtcNow.ToString("o")
        kit_directory = "CadPlotMcp.release"
        kit_archive = "CadPlotMcp.release.zip"
        kit_manifest_sha256 = (
            Get-FileHash -LiteralPath $kitManifestPath -Algorithm SHA256
        ).Hash.ToLowerInvariant()
        kit_archive_sha256 = (
            Get-FileHash -LiteralPath $kitArchive -Algorithm SHA256
        ).Hash.ToLowerInvariant()
        dependency_audit_ran = $true
        dependency_audit = $readiness.dependency_audit
        matching_sdk_bundle_built = $true
        local_demo_ready = $true
        licensed_live_pilot_ready = $false
        public_release_ready = $false
        company_assets_copied = $false
        autodesk_binaries_included = $false
        autocad_launched = $false
        live_publish_proven = $false
    })

    $verification = & (Join-Path $kitRoot "scripts\verify-release-kit.ps1") `
        -ReleaseRoot $resolvedOutputRoot `
        -PassThru
    [ordered]@{
        passed = $verification.Passed
        release_root = $resolvedOutputRoot
        exact_commit = $commit
        package_version = $bundleEvidence.PackageVersion
        matching_sdk_bundle_built = $true
        local_demo_ready = $true
        self_verification_passed = $verification.Passed
        dependency_audit_passed = $verification.DependencyAuditPassed
        autocad_launched = $false
        live_publish_proven = $false
    } | ConvertTo-Json
}
finally {
    Pop-Location
}
