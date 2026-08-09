[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$AutoCAD2016SdkDir,

    [Parameter(Mandatory = $true)]
    [string]$AutoCAD2025SdkDir,

    [string]$ReadinessReport = "",
    [string]$DotNet = "",
    [string]$Uv = "uv",
    [string]$BundleOutputRoot = "",
    [string]$ReleaseOutputRoot = "",
    [switch]$SkipSync
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

function Assert-SeparateRoots {
    param(
        [Parameter(Mandatory = $true)][string]$First,
        [Parameter(Mandatory = $true)][string]$Second
    )

    $separator = [System.IO.Path]::DirectorySeparatorChar
    $firstFull = [System.IO.Path]::GetFullPath($First).TrimEnd($separator)
    $secondFull = [System.IO.Path]::GetFullPath($Second).TrimEnd($separator)
    if (
        $firstFull -ieq $secondFull -or
        $firstFull.StartsWith("$secondFull$separator", [StringComparison]::OrdinalIgnoreCase) -or
        $secondFull.StartsWith("$firstFull$separator", [StringComparison]::OrdinalIgnoreCase)
    ) {
        throw "Bundle and release-kit output roots must be separate, non-nested directories."
    }
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
        throw "Complete release build requires a clean worktree."
    }

    [xml]$packageManifest = Get-Content `
        -LiteralPath (Join-Path $repoRoot "bundle\CadPlotMcp.bundle\PackageContents.xml") `
        -Raw -Encoding UTF8
    $packageVersion = [string]$packageManifest.ApplicationPackage.AppVersion
    if ([string]::IsNullOrWhiteSpace($BundleOutputRoot)) {
        $BundleOutputRoot = Join-Path $repoRoot (
            "artifacts\cadplot-bundle-{0}-{1}" -f $packageVersion,$commit.Substring(0, 7)
        )
    }
    if ([string]::IsNullOrWhiteSpace($ReleaseOutputRoot)) {
        $ReleaseOutputRoot = Join-Path $repoRoot (
            "artifacts\cadplot-release-kit-{0}-{1}" -f $packageVersion,$commit.Substring(0, 7)
        )
    }
    $resolvedBundleRoot = [System.IO.Path]::GetFullPath($BundleOutputRoot)
    $resolvedReleaseRoot = [System.IO.Path]::GetFullPath($ReleaseOutputRoot)
    Assert-SeparateRoots -First $resolvedBundleRoot -Second $resolvedReleaseRoot
    foreach ($target in @($resolvedBundleRoot, $resolvedReleaseRoot)) {
        if (Test-Path -LiteralPath $target) {
            throw "Complete release target already exists; build never overwrites: $target"
        }
    }

    $temporaryReadiness = $false
    if ([string]::IsNullOrWhiteSpace($ReadinessReport)) {
        $ReadinessReport = Join-Path `
            ([System.IO.Path]::GetTempPath()) `
            ("cadplot-complete-release-readiness-{0}.json" -f [Guid]::NewGuid().ToString("N"))
        $temporaryReadiness = $true
        $rehearsalParameters = @{
            AutoCADApiDir = $AutoCAD2025SdkDir
            ReportPath = $ReadinessReport
            AuditDependencies = $true
            SkipSync = $SkipSync
        }
        if (-not [string]::IsNullOrWhiteSpace($DotNet)) {
            $rehearsalParameters["DotNet"] = $DotNet
        }
        & (Join-Path $PSScriptRoot "run-demo-rehearsal.ps1") @rehearsalParameters
    }
    $resolvedReadiness = [System.IO.Path]::GetFullPath($ReadinessReport)
    if (-not (Test-Path -LiteralPath $resolvedReadiness -PathType Leaf)) {
        throw "Complete release readiness report was not produced: $resolvedReadiness"
    }

    $bundleParameters = @{
        AutoCAD2016SdkDir = $AutoCAD2016SdkDir
        AutoCAD2025SdkDir = $AutoCAD2025SdkDir
        Uv = $Uv
        OutputRoot = $resolvedBundleRoot
    }
    if (-not [string]::IsNullOrWhiteSpace($DotNet)) {
        $bundleParameters["DotNet"] = $DotNet
    }
    & (Join-Path $PSScriptRoot "build-bundle.ps1") @bundleParameters
    $bundleEvidence = & (Join-Path $PSScriptRoot "verify-bundle-release.ps1") `
        -ReleaseRoot $resolvedBundleRoot -PassThru
    if ($bundleEvidence.ExactCommit -cne $commit -or $bundleEvidence.MatchingSdkBundleBuilt -ne $true) {
        throw "Complete release bundle verification did not bind the current matching-SDK build."
    }

    & (Join-Path $PSScriptRoot "build-release-kit.ps1") `
        -BundleReleaseRoot $resolvedBundleRoot `
        -ReadinessReport $resolvedReadiness `
        -OutputRoot $resolvedReleaseRoot
    $releaseEvidence = & (Join-Path $PSScriptRoot "verify-release-kit.ps1") `
        -ReleaseRoot $resolvedReleaseRoot -PassThru
    if (
        $releaseEvidence.ExactCommit -cne $commit -or
        $releaseEvidence.MatchingSdkBundleBuilt -ne $true -or
        $releaseEvidence.LivePublishProven -ne $false
    ) {
        throw "Complete release-kit verification crossed an evidence boundary."
    }

    [ordered]@{
        passed = $true
        exact_commit = $commit
        package_version = $packageVersion
        bundle_release_root = $resolvedBundleRoot
        release_kit_root = $resolvedReleaseRoot
        readiness_report = $resolvedReadiness
        temporary_readiness_report = $temporaryReadiness
        matching_sdk_bundle_built = $true
        self_verification_passed = $true
        autocad_launched = $false
        live_publish_proven = $false
        licensed_live_pilot_ready = $false
        public_release_ready = $false
    } | ConvertTo-Json
}
finally {
    Pop-Location
}
