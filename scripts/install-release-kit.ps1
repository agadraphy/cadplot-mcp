[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = "Medium")]
param(
    [Parameter(Mandatory = $true)]
    [string]$ReleaseRoot,

    [Parameter(Mandatory = $true)]
    [string]$PilotRoot,

    [string]$BundleDestinationRoot = "",

    [string]$PythonDestinationRoot = "",

    [string]$Uv = "",

    [string]$Python = "",

    [switch]$AllowProtocolOnlyFixture,

    [switch]$PassThru
)

$ErrorActionPreference = "Stop"

function Get-NormalizedDirectoryPath {
    param([Parameter(Mandatory = $true)][string]$Path)

    return [System.IO.Path]::GetFullPath($Path).TrimEnd('\')
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

    $volumeRoot = [System.IO.Path]::GetPathRoot($Path).TrimEnd('\')
    if ($Path -ieq $volumeRoot) {
        throw "$Label must not be a drive or share root."
    }
}

function Assert-AutoCADClosed {
    $running = @(Get-Process -Name "acad" -ErrorAction SilentlyContinue)
    if ($running.Count -gt 0) {
        throw "Close every AutoCAD process (acad.exe) before installing the CadPlot release kit."
    }
}

function Write-NewUtf8Json {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)]$Value
    )

    $bytes = [System.Text.UTF8Encoding]::new($false).GetBytes(
        ($Value | ConvertTo-Json -Depth 8)
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

function Assert-InstallReceipt {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)]$Expected
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "CadPlot install receipt does not exist: $Path"
    }
    $item = Get-Item -LiteralPath $Path -Force
    if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "CadPlot install receipt must not be redirected."
    }
    if ($item.Length -gt 1MB) { throw "CadPlot install receipt exceeds 1 MiB." }
    try { $receipt = Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json }
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
    $actualPayloadHash = Get-JsonSha256 -Value $receipt.payload
    if ($actualPayloadHash -cne $receipt.payload_sha256) {
        throw "CadPlot install receipt payload digest is invalid."
    }
    $payload = $receipt.payload
    $installedUtc = [DateTime]::MinValue
    if (
        $payload.exact_commit -cne $Expected.ExactCommit -or
        $payload.package_version -cne $Expected.PackageVersion -or
        -not [DateTime]::TryParse([string]$payload.installed_utc, [ref]$installedUtc) -or
        $payload.release_kit_manifest_sha256 -cne $Expected.ReleaseKitManifestSha256 -or
        $payload.bundle.path -cne $Expected.BundlePath -or
        $payload.python.path -cne $Expected.PythonPath -or
        $payload.python.manifest_sha256 -cne $Expected.PythonManifestSha256 -or
        $payload.python.distribution_count -ne $Expected.DistributionCount -or
        $payload.pilot.root -cne $Expected.PilotRoot -or
        $payload.pilot.config -cne $Expected.Config -or
        [string]$payload.pilot.config_sha256_at_install -notmatch $shaPattern -or
        $payload.pilot.authorized_input -cne $Expected.AuthorizedInput -or
        $payload.pilot.isolated_workspace -cne $Expected.IsolatedWorkspace -or
        [string]$payload.actions.pilot -notin @("create", "reuse_verified_structure") -or
        [string]$payload.actions.python -notin @("install", "reuse_verified") -or
        [string]$payload.actions.bundle -notin @("install", "reuse_verified") -or
        $payload.autocad_running_at_install -ne $false -or
        $payload.autocad_launched -ne $false -or
        $payload.publish_enabled -ne $false -or
        $payload.live_publish_proven -ne $false -or
        $payload.company_assets_copied -ne $false
    ) {
        throw "CadPlot install receipt identity or safety evidence is invalid."
    }
    $receiptFiles = @($payload.bundle.files)
    if ($receiptFiles.Count -ne @($Expected.BundleFiles).Count) {
        throw "CadPlot install receipt bundle file count is invalid."
    }
    foreach ($expectedFile in @($Expected.BundleFiles)) {
        $matches = @($receiptFiles | Where-Object { $_.path -ceq $expectedFile.path })
        if ($matches.Count -ne 1 -or $matches[0].sha256 -cne $expectedFile.sha256) {
            throw "CadPlot install receipt bundle hash evidence is invalid: $($expectedFile.path)"
        }
    }
    if (@($receiptFiles | Group-Object -Property path | Where-Object Count -ne 1).Count -ne 0) {
        throw "CadPlot install receipt contains duplicate bundle hash evidence."
    }
    return $receipt
}

function Assert-NoRedirectedPath {
    param([Parameter(Mandatory = $true)][string]$Path)

    $current = [System.IO.Path]::GetFullPath($Path)
    while (-not (Test-Path -LiteralPath $current)) {
        $parent = Split-Path -Parent $current
        if ([string]::IsNullOrWhiteSpace($parent) -or $parent -eq $current) {
            throw "No existing ancestor was found for release installation path: $Path"
        }
        $current = $parent
    }
    while (-not [string]::IsNullOrWhiteSpace($current)) {
        $item = Get-Item -LiteralPath $current -Force
        if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Release installation path must not pass through a symlink or junction: $current"
        }
        $parent = Split-Path -Parent $current
        if ([string]::IsNullOrWhiteSpace($parent) -or $parent -eq $current) { break }
        $current = $parent
    }
}

function Assert-MatchingBundleHashes {
    param(
        [Parameter(Mandatory = $true)]$Expected,
        [Parameter(Mandatory = $true)]$Actual
    )

    if (@($Expected.Hashes).Count -ne @($Actual.Hashes).Count) {
        throw "Installed bundle file count does not match the verified release kit."
    }
    foreach ($expectedHash in @($Expected.Hashes)) {
        $matches = @($Actual.Hashes | Where-Object { $_.Path -ceq $expectedHash.Path })
        if ($matches.Count -ne 1 -or $matches[0].Sha256 -cne $expectedHash.Sha256) {
            throw "Installed bundle does not match the verified release kit: $($expectedHash.Path)"
        }
    }
}

function Assert-PilotWorkspace {
    param([Parameter(Mandatory = $true)][string]$Root)

    if (-not (Test-Path -LiteralPath $Root -PathType Container)) {
        throw "Pilot workspace does not exist: $Root"
    }
    Assert-NoRedirectedPath -Path $Root
    $configPath = Join-Path $Root "config.yaml"
    $inputPath = Join-Path $Root "pilot-input"
    $workPath = Join-Path $Root "pilot-work"
    if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) {
        throw "Existing pilot workspace has no config.yaml: $Root"
    }
    foreach ($directory in @($inputPath, $workPath)) {
        if (-not (Test-Path -LiteralPath $directory -PathType Container)) {
            throw "Existing pilot workspace is incomplete: $directory"
        }
    }
    foreach ($itemPath in @($configPath, $inputPath, $workPath)) {
        $item = Get-Item -LiteralPath $itemPath -Force
        if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Pilot workspace required paths must not be redirected: $itemPath"
        }
    }
    return [pscustomobject]@{
        Root = $Root
        Config = $configPath
        AuthorizedInput = $inputPath
        IsolatedWorkspace = $workPath
    }
}

$resolvedRelease = Get-NormalizedDirectoryPath -Path $ReleaseRoot
$kitRoot = Join-Path $resolvedRelease "CadPlotMcp.release"
$releaseVerifier = Join-Path $kitRoot "scripts\verify-release-kit.ps1"
if (-not (Test-Path -LiteralPath $releaseVerifier -PathType Leaf)) {
    throw "Embedded release-kit verifier is missing."
}
$releaseArguments = @{ ReleaseRoot = $resolvedRelease; PassThru = $true }
if ($AllowProtocolOnlyFixture) { $releaseArguments.AllowProtocolOnlyFixture = $true }
$releaseEvidence = & $releaseVerifier @releaseArguments

if ([string]::IsNullOrWhiteSpace($BundleDestinationRoot)) {
    $BundleDestinationRoot = Join-Path (
        [Environment]::GetFolderPath("ApplicationData")
    ) "Autodesk\ApplicationPlugins"
}
if ([string]::IsNullOrWhiteSpace($PythonDestinationRoot)) {
    if ([string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
        throw "LOCALAPPDATA is unavailable; pass -PythonDestinationRoot explicitly."
    }
    $PythonDestinationRoot = Join-Path $env:LOCALAPPDATA "CadPlotMcp\python"
}

$resolvedBundleDestination = Get-NormalizedDirectoryPath -Path $BundleDestinationRoot
$resolvedPythonDestination = Get-NormalizedDirectoryPath -Path $PythonDestinationRoot
$resolvedPilot = Get-NormalizedDirectoryPath -Path $PilotRoot
Assert-NotVolumeRoot -Path $resolvedBundleDestination -Label "Bundle destination"
Assert-NotVolumeRoot -Path $resolvedPythonDestination -Label "Python destination"
Assert-NotVolumeRoot -Path $resolvedPilot -Label "Pilot workspace"
$bundleTarget = Join-Path $resolvedBundleDestination "CadPlotMcp.bundle"
$pythonTargetName = "{0}-{1}" -f (
    $releaseEvidence.PackageVersion
),$releaseEvidence.ExactCommit.Substring(0, 7)
$pythonTarget = Join-Path $resolvedPythonDestination $pythonTargetName
$bundleSource = Join-Path $kitRoot "autocad\CadPlotMcp.bundle"
$receiptDirectory = Join-Path $resolvedPilot "install-receipts"
$receiptPath = Join-Path $receiptDirectory (
    "cadplot-install-{0}.json" -f $releaseEvidence.ExactCommit
)

$paths = @(
    [pscustomobject]@{ Name = "release kit"; Path = $resolvedRelease },
    [pscustomobject]@{ Name = "bundle destination"; Path = $resolvedBundleDestination },
    [pscustomobject]@{ Name = "Python destination"; Path = $resolvedPythonDestination },
    [pscustomobject]@{ Name = "pilot workspace"; Path = $resolvedPilot }
)
for ($left = 0; $left -lt $paths.Count; $left++) {
    for ($right = $left + 1; $right -lt $paths.Count; $right++) {
        if (Test-PathsOverlap -First $paths[$left].Path -Second $paths[$right].Path) {
            throw "Release installation paths must not overlap: $($paths[$left].Name) and $($paths[$right].Name)."
        }
    }
}
foreach ($path in $paths) { Assert-NoRedirectedPath -Path $path.Path }
Assert-AutoCADClosed

$bundleVerifier = Join-Path $kitRoot "scripts\verify-bundle.ps1"
$sourceBundleEvidence = & $bundleVerifier -BundlePath $bundleSource -PassThru
$bundleAction = "install"
if (Test-Path -LiteralPath $bundleTarget) {
    $installedBundleEvidence = & $bundleVerifier -BundlePath $bundleTarget -PassThru
    Assert-MatchingBundleHashes -Expected $sourceBundleEvidence -Actual $installedBundleEvidence
    $bundleAction = "reuse_verified"
}

$pythonVerifier = Join-Path $kitRoot "scripts\verify-python-install.ps1"
$pythonAction = "install"
$installedPythonEvidence = $null
if (Test-Path -LiteralPath $pythonTarget) {
    $installedPythonEvidence = & $pythonVerifier -InstallRoot $pythonTarget -PassThru
    if (
        $installedPythonEvidence.ExactCommit -cne $releaseEvidence.ExactCommit -or
        $installedPythonEvidence.PackageVersion -cne $releaseEvidence.PackageVersion
    ) {
        throw "Existing Python target does not match the verified release kit."
    }
    $pythonAction = "reuse_verified"
}

$pilotAction = "create"
$pilotEvidence = $null
if (Test-Path -LiteralPath $resolvedPilot) {
    $pilotEvidence = Assert-PilotWorkspace -Root $resolvedPilot
    $pilotAction = "reuse_verified_structure"
}

# Exercise every component installer's discoverable checks before the first mutation.
if ($pilotAction -eq "create") {
    & (Join-Path $kitRoot "scripts\new-local-pilot.ps1") `
        -DestinationRoot $resolvedPilot `
        -WhatIf | Out-Null
}
if ($pythonAction -eq "install") {
    $pythonPreviewArguments = @{
        ReleaseRoot = $resolvedRelease
        DestinationRoot = $resolvedPythonDestination
        PassThru = $true
        WhatIf = $true
    }
    if (-not [string]::IsNullOrWhiteSpace($Uv)) { $pythonPreviewArguments.Uv = $Uv }
    if (-not [string]::IsNullOrWhiteSpace($Python)) { $pythonPreviewArguments.Python = $Python }
    if ($AllowProtocolOnlyFixture) { $pythonPreviewArguments.AllowProtocolOnlyFixture = $true }
    $pythonPreview = & (Join-Path $kitRoot "scripts\install-python.ps1") @pythonPreviewArguments
    if ($pythonPreview.WhatIf -ne $true -or $pythonPreview.Target -cne $pythonTarget) {
        throw "Python installer preflight did not return the exact planned target."
    }
}
if ($bundleAction -eq "install") {
    & (Join-Path $kitRoot "scripts\install-bundle.ps1") `
        -SourceBundle $bundleSource `
        -DestinationRoot $resolvedBundleDestination `
        -WhatIf | Out-Null
}

$preview = [pscustomobject]@{
    Installed = $false
    WhatIf = $true
    ReleaseRoot = $resolvedRelease
    ExactCommit = $releaseEvidence.ExactCommit
    PackageVersion = $releaseEvidence.PackageVersion
    PilotAction = $pilotAction
    PilotRoot = $resolvedPilot
    PythonAction = $pythonAction
    PythonTarget = $pythonTarget
    BundleAction = $bundleAction
    BundleTarget = $bundleTarget
    InstallReceipt = $receiptPath
    AutoCADLaunched = $false
    PublishEnabled = $false
    LivePublishProven = $false
}
$operationTarget = "pilot='$resolvedPilot'; python='$pythonTarget'; bundle='$bundleTarget'"
if (-not $PSCmdlet.ShouldProcess($operationTarget, "Install or reuse verified CadPlot release components")) {
    if ($PassThru) { $preview } else { $preview | ConvertTo-Json -Depth 4 }
    return
}

# Keep the AutoCAD-loadable bundle last so partial failure leaves only non-host components.
if ($pilotAction -eq "create") {
    $pilotJson = & (Join-Path $kitRoot "scripts\new-local-pilot.ps1") `
        -DestinationRoot $resolvedPilot `
        -Confirm:$false
    $createdPilot = $pilotJson | ConvertFrom-Json
    if ($createdPilot.created -ne $true) { throw "Pilot workspace creation did not complete." }
}
$pilotEvidence = Assert-PilotWorkspace -Root $resolvedPilot

if ($pythonAction -eq "install") {
    $pythonArguments = @{
        ReleaseRoot = $resolvedRelease
        DestinationRoot = $resolvedPythonDestination
        PassThru = $true
        Confirm = $false
    }
    if (-not [string]::IsNullOrWhiteSpace($Uv)) { $pythonArguments.Uv = $Uv }
    if (-not [string]::IsNullOrWhiteSpace($Python)) { $pythonArguments.Python = $Python }
    if ($AllowProtocolOnlyFixture) { $pythonArguments.AllowProtocolOnlyFixture = $true }
    $pythonInstall = & (Join-Path $kitRoot "scripts\install-python.ps1") @pythonArguments
    if ($pythonInstall.Installed -ne $true) { throw "Python installation did not complete." }
}
$installedPythonEvidence = & $pythonVerifier -InstallRoot $pythonTarget -PassThru
if (
    $installedPythonEvidence.ExactCommit -cne $releaseEvidence.ExactCommit -or
    $installedPythonEvidence.PackageVersion -cne $releaseEvidence.PackageVersion
) {
    throw "Final Python installation does not match the verified release kit."
}

if ($bundleAction -eq "install") {
    & (Join-Path $kitRoot "scripts\install-bundle.ps1") `
        -SourceBundle $bundleSource `
        -DestinationRoot $resolvedBundleDestination `
        -Confirm:$false | Out-Null
}
$installedBundleEvidence = & $bundleVerifier -BundlePath $bundleTarget -PassThru
Assert-MatchingBundleHashes -Expected $sourceBundleEvidence -Actual $installedBundleEvidence

$bundleFiles = @($installedBundleEvidence.Hashes | Sort-Object Path | ForEach-Object {
    [ordered]@{ path = $_.Path; sha256 = $_.Sha256 }
})
$releaseManifestHash = (
    Get-FileHash -LiteralPath (Join-Path $kitRoot "release-kit.json") -Algorithm SHA256
).Hash.ToLowerInvariant()
$pythonManifestHash = (
    Get-FileHash -LiteralPath (Join-Path $pythonTarget "python-install.json") -Algorithm SHA256
).Hash.ToLowerInvariant()
$receiptExpected = [pscustomobject]@{
    ExactCommit = $releaseEvidence.ExactCommit
    PackageVersion = $releaseEvidence.PackageVersion
    ReleaseKitManifestSha256 = $releaseManifestHash
    BundlePath = $bundleTarget
    BundleFiles = $bundleFiles
    PythonPath = $pythonTarget
    PythonManifestSha256 = $pythonManifestHash
    PilotRoot = $pilotEvidence.Root
    Config = $pilotEvidence.Config
    AuthorizedInput = $pilotEvidence.AuthorizedInput
    IsolatedWorkspace = $pilotEvidence.IsolatedWorkspace
    DistributionCount = $installedPythonEvidence.DistributionCount
}

if (Test-Path -LiteralPath $receiptPath) {
    $null = Assert-InstallReceipt -Path $receiptPath -Expected $receiptExpected
}
else {
    if (-not (Test-Path -LiteralPath $receiptDirectory)) {
        $null = New-Item -ItemType Directory -Path $receiptDirectory
    }
    $receiptDirectoryItem = Get-Item -LiteralPath $receiptDirectory -Force
    if (
        -not $receiptDirectoryItem.PSIsContainer -or
        ($receiptDirectoryItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0
    ) {
        throw "CadPlot install receipt directory must be a regular local directory."
    }
    $configHash = (
        Get-FileHash -LiteralPath $pilotEvidence.Config -Algorithm SHA256
    ).Hash.ToLowerInvariant()
    $receiptPayload = [ordered]@{
        exact_commit = $releaseEvidence.ExactCommit
        package_version = $releaseEvidence.PackageVersion
        installed_utc = [DateTime]::UtcNow.ToString("o")
        release_kit_manifest_sha256 = $releaseManifestHash
        bundle = [ordered]@{ path = $bundleTarget; files = $bundleFiles }
        python = [ordered]@{
            path = $pythonTarget
            manifest_sha256 = $pythonManifestHash
            distribution_count = $installedPythonEvidence.DistributionCount
        }
        pilot = [ordered]@{
            root = $pilotEvidence.Root
            config = $pilotEvidence.Config
            config_sha256_at_install = $configHash
            authorized_input = $pilotEvidence.AuthorizedInput
            isolated_workspace = $pilotEvidence.IsolatedWorkspace
        }
        actions = [ordered]@{
            pilot = $pilotAction
            python = $pythonAction
            bundle = $bundleAction
        }
        autocad_running_at_install = $false
        autocad_launched = $false
        publish_enabled = $false
        live_publish_proven = $false
        company_assets_copied = $false
    }
    Write-NewUtf8Json -Path $receiptPath -Value ([ordered]@{
        schema_version = 2
        receipt_kind = "cadplot_release_install"
        payload_sha256 = Get-JsonSha256 -Value $receiptPayload
        payload = $receiptPayload
    })
    $null = Assert-InstallReceipt -Path $receiptPath -Expected $receiptExpected
}
$installVerifierArguments = @{
    ReleaseRoot = $resolvedRelease
    ReceiptPath = $receiptPath
    PassThru = $true
}
if ($AllowProtocolOnlyFixture) { $installVerifierArguments.AllowProtocolOnlyFixture = $true }
$installEvidence = & (Join-Path $kitRoot "scripts\verify-release-install.ps1") `
    @installVerifierArguments
if (
    $installEvidence.Passed -ne $true -or
    $installEvidence.InstallationComplete -ne $true -or
    $installEvidence.BundleVerified -ne $true -or
    $installEvidence.PythonVerified -ne $true -or
    $installEvidence.BundlePath -cne $bundleTarget -or
    $installEvidence.PythonPath -cne $pythonTarget -or
    $installEvidence.PilotRoot -cne $pilotEvidence.Root -or
    $installEvidence.AutoCADLaunched -ne $false -or
    $installEvidence.PublishEnabled -ne $false -or
    $installEvidence.LivePublishProven -ne $false
) {
    throw "Independent installed-release verification did not confirm the exact targets."
}
$receiptHash = $installEvidence.ReceiptSha256

$result = [pscustomobject]@{
    Installed = $true
    WhatIf = $false
    ReleaseRoot = $resolvedRelease
    ExactCommit = $releaseEvidence.ExactCommit
    PackageVersion = $releaseEvidence.PackageVersion
    PilotAction = $pilotAction
    PilotRoot = $pilotEvidence.Root
    Config = $pilotEvidence.Config
    AuthorizedInput = $pilotEvidence.AuthorizedInput
    IsolatedWorkspace = $pilotEvidence.IsolatedWorkspace
    PythonAction = $pythonAction
    PythonTarget = $pythonTarget
    CommandRoot = (Join-Path $pythonTarget "bin")
    BundleAction = $bundleAction
    BundleTarget = $bundleTarget
    InstallReceipt = $receiptPath
    InstallReceiptSha256 = $receiptHash
    InstallVerified = $true
    ConfigChangedSinceInstall = $installEvidence.ConfigChangedSinceInstall
    AutoCADLaunched = $false
    PublishEnabled = $false
    LivePublishProven = $false
    NextCommand = "& '$(Join-Path $pythonTarget 'bin\cadplot-doctor.cmd')' --config '$($pilotEvidence.Config)' --mode config"
}
if ($PassThru) { $result } else { $result | ConvertTo-Json -Depth 4 }
