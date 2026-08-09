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
    AutoCADLaunched = $false
    PublishEnabled = $false
    LivePublishProven = $false
    NextCommand = "& '$(Join-Path $pythonTarget 'bin\cadplot-doctor.cmd')' --config '$($pilotEvidence.Config)' --mode config"
}
if ($PassThru) { $result } else { $result | ConvertTo-Json -Depth 4 }
