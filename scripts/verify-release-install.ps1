[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ReleaseRoot,

    [Parameter(Mandatory = $true)]
    [string]$ReceiptPath,

    [switch]$AllowProtocolOnlyFixture,

    [switch]$PassThru
)

$ErrorActionPreference = "Stop"

function Get-NormalizedDirectoryPath {
    param([Parameter(Mandatory = $true)][string]$Path)

    return [System.IO.Path]::GetFullPath($Path).TrimEnd('\')
}

function Get-JsonSha256 {
    param([Parameter(Mandatory = $true)]$Value)

    $json = $Value | ConvertTo-Json -Compress -Depth 8
    $bytes = [System.Text.UTF8Encoding]::new($false).GetBytes($json)
    $algorithm = [System.Security.Cryptography.SHA256]::Create()
    try {
        return [BitConverter]::ToString(
            $algorithm.ComputeHash($bytes)
        ).Replace('-', '').ToLowerInvariant()
    }
    finally { $algorithm.Dispose() }
}

function Assert-NoRedirectedAncestor {
    param([Parameter(Mandatory = $true)][string]$Path)

    $current = [System.IO.Path]::GetFullPath($Path)
    if (-not (Test-Path -LiteralPath $current)) {
        throw "Installed release evidence path does not exist: $current"
    }
    while (-not [string]::IsNullOrWhiteSpace($current)) {
        $item = Get-Item -LiteralPath $current -Force
        if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Installed release evidence path must not pass through a symlink or junction: $current"
        }
        $parent = Split-Path -Parent $current
        if ([string]::IsNullOrWhiteSpace($parent) -or $parent -eq $current) { break }
        $current = $parent
    }
}

function Assert-RecordedAbsolutePath {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Label
    )

    if (
        [string]::IsNullOrWhiteSpace($Path) -or
        -not [System.IO.Path]::IsPathRooted($Path) -or
        $Path -match '^[A-Za-z]:[^\\/]'
    ) {
        throw "$Label must be an explicit absolute path."
    }
    $normalized = [System.IO.Path]::GetFullPath($Path).TrimEnd('\')
    if ($Path.TrimEnd('\') -cne $normalized) {
        throw "$Label is not stored as its normalized absolute path."
    }
    return $normalized
}

function Assert-JsonFalse {
    param(
        [Parameter(Mandatory = $true)]$Value,
        [Parameter(Mandatory = $true)][string]$Label
    )

    if ($Value -isnot [bool] -or $Value) {
        throw "CadPlot install receipt safety field is invalid: $Label"
    }
}

function Test-PathsOverlap {
    param(
        [Parameter(Mandatory = $true)][string]$First,
        [Parameter(Mandatory = $true)][string]$Second
    )

    $firstBoundary = $First.TrimEnd('\') + '\'
    $secondBoundary = $Second.TrimEnd('\') + '\'
    return (
        $First.Equals($Second, [System.StringComparison]::OrdinalIgnoreCase) -or
        $firstBoundary.StartsWith($secondBoundary, [System.StringComparison]::OrdinalIgnoreCase) -or
        $secondBoundary.StartsWith($firstBoundary, [System.StringComparison]::OrdinalIgnoreCase)
    )
}

function Assert-NotVolumeRoot {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Label
    )

    if ($Path -ieq [System.IO.Path]::GetPathRoot($Path).TrimEnd('\')) {
        throw "$Label must not be a drive or share root."
    }
}

function Assert-MatchingBundleHashes {
    param(
        [Parameter(Mandatory = $true)]$Recorded,
        [Parameter(Mandatory = $true)]$Actual
    )

    $recordedFiles = @($Recorded)
    $actualFiles = @($Actual)
    if ($recordedFiles.Count -ne $actualFiles.Count) {
        throw "CadPlot install receipt bundle file count is invalid."
    }
    if (@($recordedFiles | Group-Object -Property path | Where-Object Count -ne 1).Count -ne 0) {
        throw "CadPlot install receipt contains duplicate bundle hash evidence."
    }
    foreach ($actualFile in $actualFiles) {
        $matches = @($recordedFiles | Where-Object { $_.path -ceq $actualFile.Path })
        if ($matches.Count -ne 1 -or $matches[0].sha256 -cne $actualFile.Sha256) {
            throw "CadPlot install receipt bundle hash evidence is invalid: $($actualFile.Path)"
        }
    }
}

$resolvedRelease = Get-NormalizedDirectoryPath -Path $ReleaseRoot
$resolvedReceipt = [System.IO.Path]::GetFullPath($ReceiptPath)
$kitRoot = Join-Path $resolvedRelease "CadPlotMcp.release"
$releaseVerifier = Join-Path $kitRoot "scripts\verify-release-kit.ps1"
if (-not (Test-Path -LiteralPath $releaseVerifier -PathType Leaf)) {
    throw "Embedded release-kit verifier is missing."
}
$releaseArguments = @{ ReleaseRoot = $resolvedRelease; PassThru = $true }
if ($AllowProtocolOnlyFixture) { $releaseArguments.AllowProtocolOnlyFixture = $true }
$releaseEvidence = & $releaseVerifier @releaseArguments

if (-not (Test-Path -LiteralPath $resolvedReceipt -PathType Leaf)) {
    throw "CadPlot install receipt does not exist: $resolvedReceipt"
}
Assert-NoRedirectedAncestor -Path $resolvedReceipt
$receiptItem = Get-Item -LiteralPath $resolvedReceipt -Force
if ($receiptItem.Length -gt 1MB) { throw "CadPlot install receipt exceeds 1 MiB." }
try {
    $receipt = Get-Content -LiteralPath $resolvedReceipt -Raw -Encoding UTF8 | ConvertFrom-Json
}
catch { throw "CadPlot install receipt is not valid UTF-8 JSON." }

$shaPattern = '^[0-9a-f]{64}$'
if (
    $receipt.schema_version -ne 2 -or
    $receipt.receipt_kind -cne "cadplot_release_install" -or
    [string]$receipt.payload_sha256 -notmatch $shaPattern -or
    $null -eq $receipt.payload
) {
    throw "CadPlot install receipt identity or safety evidence is invalid."
}
$payloadHash = Get-JsonSha256 -Value $receipt.payload
if ($payloadHash -cne $receipt.payload_sha256) {
    throw "CadPlot install receipt payload digest is invalid."
}

$payload = $receipt.payload
$installedUtc = [DateTime]::MinValue
$releaseManifestPath = Join-Path $kitRoot "release-kit.json"
$releaseManifestHash = (
    Get-FileHash -LiteralPath $releaseManifestPath -Algorithm SHA256
).Hash.ToLowerInvariant()
if (
    $payload.exact_commit -cne $releaseEvidence.ExactCommit -or
    $payload.package_version -cne $releaseEvidence.PackageVersion -or
    -not [DateTime]::TryParse([string]$payload.installed_utc, [ref]$installedUtc) -or
    $payload.release_kit_manifest_sha256 -cne $releaseManifestHash -or
    [string]$payload.pilot.config_sha256_at_install -notmatch $shaPattern -or
    [string]$payload.actions.pilot -notin @("create", "reuse_verified_structure") -or
    [string]$payload.actions.python -notin @("install", "reuse_verified") -or
    [string]$payload.actions.bundle -notin @("install", "reuse_verified")
) {
    throw "CadPlot install receipt identity or install-action evidence is invalid."
}
Assert-JsonFalse -Value $payload.autocad_running_at_install -Label "autocad_running_at_install"
Assert-JsonFalse -Value $payload.autocad_launched -Label "autocad_launched"
Assert-JsonFalse -Value $payload.publish_enabled -Label "publish_enabled"
Assert-JsonFalse -Value $payload.live_publish_proven -Label "live_publish_proven"
Assert-JsonFalse -Value $payload.company_assets_copied -Label "company_assets_copied"

$bundlePath = Assert-RecordedAbsolutePath -Path ([string]$payload.bundle.path) -Label "Bundle path"
$pythonPath = Assert-RecordedAbsolutePath -Path ([string]$payload.python.path) -Label "Python path"
$pilotRoot = Assert-RecordedAbsolutePath -Path ([string]$payload.pilot.root) -Label "Pilot root"
$configPath = Assert-RecordedAbsolutePath -Path ([string]$payload.pilot.config) -Label "Pilot config"
$authorizedInput = Assert-RecordedAbsolutePath `
    -Path ([string]$payload.pilot.authorized_input) `
    -Label "Authorized input"
$isolatedWorkspace = Assert-RecordedAbsolutePath `
    -Path ([string]$payload.pilot.isolated_workspace) `
    -Label "Isolated workspace"

$installedRoots = @(
    [pscustomobject]@{ Label = "Release root"; Path = $resolvedRelease },
    [pscustomobject]@{ Label = "Bundle path"; Path = $bundlePath },
    [pscustomobject]@{ Label = "Python path"; Path = $pythonPath },
    [pscustomobject]@{ Label = "Pilot root"; Path = $pilotRoot }
)
foreach ($entry in $installedRoots) {
    Assert-NotVolumeRoot -Path $entry.Path -Label $entry.Label
}
for ($left = 0; $left -lt $installedRoots.Count; $left++) {
    for ($right = $left + 1; $right -lt $installedRoots.Count; $right++) {
        if (Test-PathsOverlap -First $installedRoots[$left].Path -Second $installedRoots[$right].Path) {
            throw (
                "CadPlot installed-release evidence paths must not overlap: " +
                "$($installedRoots[$left].Label) and $($installedRoots[$right].Label)."
            )
        }
    }
}
$expectedReceipt = Join-Path $pilotRoot (
    "install-receipts\cadplot-install-{0}.json" -f $releaseEvidence.ExactCommit
)
if ($resolvedReceipt -cne $expectedReceipt) {
    throw "CadPlot install receipt is not at the exact commit-bound pilot evidence path."
}

foreach ($path in @($bundlePath, $pythonPath, $pilotRoot, $configPath, $authorizedInput, $isolatedWorkspace)) {
    Assert-NoRedirectedAncestor -Path $path
}
if (
    $configPath -cne (Join-Path $pilotRoot "config.yaml") -or
    $authorizedInput -cne (Join-Path $pilotRoot "pilot-input") -or
    $isolatedWorkspace -cne (Join-Path $pilotRoot "pilot-work") -or
    -not (Test-Path -LiteralPath $configPath -PathType Leaf) -or
    -not (Test-Path -LiteralPath $authorizedInput -PathType Container) -or
    -not (Test-Path -LiteralPath $isolatedWorkspace -PathType Container)
) {
    throw "CadPlot install receipt pilot workspace evidence is invalid."
}

$bundleEvidence = & (Join-Path $kitRoot "scripts\verify-bundle.ps1") `
    -BundlePath $bundlePath `
    -PassThru
Assert-MatchingBundleHashes -Recorded $payload.bundle.files -Actual $bundleEvidence.Hashes

$pythonManifestPath = Join-Path $pythonPath "python-install.json"
$pythonManifestHash = (
    Get-FileHash -LiteralPath $pythonManifestPath -Algorithm SHA256
).Hash.ToLowerInvariant()
$pythonEvidence = & (Join-Path $kitRoot "scripts\verify-python-install.ps1") `
    -InstallRoot $pythonPath `
    -PassThru
if (
    $pythonEvidence.ExactCommit -cne $releaseEvidence.ExactCommit -or
    $pythonEvidence.PackageVersion -cne $releaseEvidence.PackageVersion -or
    $payload.python.manifest_sha256 -cne $pythonManifestHash -or
    $payload.python.distribution_count -ne $pythonEvidence.DistributionCount
) {
    throw "CadPlot install receipt Python evidence is invalid."
}

$configHash = (Get-FileHash -LiteralPath $configPath -Algorithm SHA256).Hash.ToLowerInvariant()
$receiptHash = (Get-FileHash -LiteralPath $resolvedReceipt -Algorithm SHA256).Hash.ToLowerInvariant()
$result = [pscustomobject]@{
    Passed = $true
    ReceiptPath = $resolvedReceipt
    ReceiptSha256 = $receiptHash
    ExactCommit = $releaseEvidence.ExactCommit
    PackageVersion = $releaseEvidence.PackageVersion
    ReleaseRoot = $resolvedRelease
    BundlePath = $bundlePath
    PythonPath = $pythonPath
    PilotRoot = $pilotRoot
    Config = $configPath
    ConfigChangedSinceInstall = $configHash -cne $payload.pilot.config_sha256_at_install
    BundleFileCount = @($bundleEvidence.Hashes).Count
    DistributionCount = $pythonEvidence.DistributionCount
    AutoCADLaunched = $false
    PublishEnabled = $false
    LivePublishProven = $false
}
if ($PassThru) { $result }
else { $result | ConvertTo-Json -Depth 3 }
