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

    $wheelPath = Join-Path $repoRoot ("dist\{0}" -f $readiness.wheel)
    if (-not (Test-Path -LiteralPath $wheelPath -PathType Leaf)) {
        throw "Readiness-bound wheel is missing: $wheelPath"
    }
    $wheelHash = (Get-FileHash -LiteralPath $wheelPath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($wheelHash -ne $readiness.wheel_sha256) {
        throw "Wheel hash no longer matches the readiness report."
    }

    if ([string]::IsNullOrWhiteSpace($OutputRoot)) {
        $OutputRoot = Join-Path $repoRoot "artifacts"
    }
    $resolvedOutputRoot = [System.IO.Path]::GetFullPath($OutputRoot)
    $kitRoot = Join-Path $resolvedOutputRoot ("cadplot-demo-kit-{0}" -f $commit.Substring(0, 7))
    if (Test-Path -LiteralPath $kitRoot) {
        throw "Demo kit target already exists; packaging never overwrites: $kitRoot"
    }
    Assert-NoRedirectedAncestor -Path $kitRoot
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

    $sourceHash = (Get-FileHash -LiteralPath $sourceArchive -Algorithm SHA256).Hash.ToLowerInvariant()
    $kitWheelHash = (Get-FileHash -LiteralPath $kitWheel -Algorithm SHA256).Hash.ToLowerInvariant()
    $publicApiProbe = if ($readiness.api_probe_ran -eq $true) {
        Get-PublicApiProbeEvidence -Probe $readiness.api_probe
    }
    else { $null }
    $fileEvidence = @(@($sourceArchive, $kitWheel, $kitVerifier) | ForEach-Object {
        [ordered]@{
            path = [System.IO.Path]::GetFileName($_)
            sha256 = (Get-FileHash -LiteralPath $_ -Algorithm SHA256).Hash.ToLowerInvariant()
        }
    })
    $manifestPath = Join-Path $kitRoot "demo-kit.json"
    $manifest = [ordered]@{
        schema_version = 2
        exact_commit = $commit
        created_utc = [DateTime]::UtcNow.ToString("o")
        source_archive = [ordered]@{
            file = [System.IO.Path]::GetFileName($sourceArchive)
            sha256 = $sourceHash
        }
        wheel = [ordered]@{
            file = [System.IO.Path]::GetFileName($kitWheel)
            sha256 = $kitWheelHash
        }
        files = $fileEvidence
        local_demo_ready = $true
        licensed_live_pilot_ready = $false
        public_release_ready = $false
        source_tree_audit_passed = $true
        api_probe_ran = $readiness.api_probe_ran -eq $true
        api_probe = $publicApiProbe
        autocad_launched = $false
        live_publish_proven = $false
        synthetic_batch_rehearsal = $batch
        company_assets_copied = $false
        autodesk_binaries_included = $false
        purpose = "Portable local/synthetic demo kit; not a live AutoCAD plug-in bundle"
    }
    $manifestJson = $manifest | ConvertTo-Json -Depth 4
    $manifestBytes = [System.Text.UTF8Encoding]::new($false).GetBytes($manifestJson)
    $stream = [System.IO.File]::Open(
        $manifestPath,
        [System.IO.FileMode]::CreateNew,
        [System.IO.FileAccess]::Write,
        [System.IO.FileShare]::Read
    )
    try {
        $stream.Write($manifestBytes, 0, $manifestBytes.Length)
    }
    finally {
        $stream.Dispose()
    }

    $verification = & $kitVerifier -KitRoot $kitRoot -PassThru
    if ($verification.Passed -ne $true -or $verification.MachinePathsIncluded -ne $false) {
        throw "Embedded demo-kit verification failed."
    }

    [ordered]@{
        passed = $true
        kit_root = $kitRoot
        manifest = $manifestPath
        exact_commit = $commit
        source_sha256 = $sourceHash
        wheel_sha256 = $kitWheelHash
        synthetic_batch_evidence_digest = $batch.evidence_digest
        self_verification_passed = $true
        machine_paths_included = $false
        company_assets_copied = $false
        live_publish_proven = $false
    } | ConvertTo-Json
}
finally {
    Pop-Location
}
