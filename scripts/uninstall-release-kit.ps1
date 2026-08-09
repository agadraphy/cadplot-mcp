[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = "High")]
param(
    [Parameter(Mandatory = $true)]
    [string]$ReleaseRoot,

    [Parameter(Mandatory = $true)]
    [string]$ReceiptPath,

    [switch]$AllowProtocolOnlyFixture,

    [switch]$PassThru
)

$ErrorActionPreference = "Stop"

function Assert-AutoCADClosed {
    $running = @(Get-Process -Name "acad" -ErrorAction SilentlyContinue)
    if ($running.Count -gt 0) {
        throw "Close every AutoCAD process (acad.exe) before uninstalling the CadPlot release."
    }
}

Assert-AutoCADClosed
$resolvedRelease = [System.IO.Path]::GetFullPath($ReleaseRoot).TrimEnd('\')
$resolvedReceipt = [System.IO.Path]::GetFullPath($ReceiptPath)
$kitRoot = Join-Path $resolvedRelease "CadPlotMcp.release"
$installVerifier = Join-Path $kitRoot "scripts\verify-release-install.ps1"
if (-not (Test-Path -LiteralPath $installVerifier -PathType Leaf)) {
    throw "Embedded installed-release verifier is missing."
}

$verificationArguments = @{
    ReleaseRoot = $resolvedRelease
    ReceiptPath = $resolvedReceipt
    AllowRemovedComponents = $true
    PassThru = $true
}
if ($AllowProtocolOnlyFixture) { $verificationArguments.AllowProtocolOnlyFixture = $true }
$initialEvidence = & $installVerifier @verificationArguments
if (
    $initialEvidence.Passed -ne $true -or
    $initialEvidence.AutoCADLaunched -ne $false -or
    $initialEvidence.PublishEnabled -ne $false -or
    $initialEvidence.LivePublishProven -ne $false
) {
    throw "Installed release did not pass the independent pre-uninstall verification."
}

$receiptHashBefore = $initialEvidence.ReceiptSha256
$bundleAction = if ($initialEvidence.BundlePresent) { "remove" } else { "already_absent" }
$pythonAction = if ($initialEvidence.PythonPresent) { "remove" } else { "already_absent" }
$bundleDestination = Split-Path -Parent $initialEvidence.BundlePath
$bundleUninstaller = Join-Path $kitRoot "scripts\uninstall-bundle.ps1"
$pythonUninstaller = Join-Path $kitRoot "scripts\uninstall-python.ps1"

# Exercise every required child uninstaller before the first mutation.
if ($bundleAction -eq "remove") {
    $bundlePreview = & $bundleUninstaller `
        -DestinationRoot $bundleDestination `
        -WhatIf `
        -PassThru
    if (
        $bundlePreview.WhatIf -ne $true -or
        $bundlePreview.BundlePath -cne $initialEvidence.BundlePath
    ) {
        throw "Bundle uninstaller preflight did not return the exact planned target."
    }
}
if ($pythonAction -eq "remove") {
    $pythonPreview = & $pythonUninstaller `
        -InstallRoot $initialEvidence.PythonPath `
        -WhatIf `
        -PassThru
    if (
        $pythonPreview.WhatIf -ne $true -or
        $pythonPreview.InstallRoot -cne $initialEvidence.PythonPath
    ) {
        throw "Python uninstaller preflight did not return the exact planned target."
    }
}

$preview = [pscustomobject]@{
    Uninstalled = $false
    WhatIf = $true
    ReleaseRoot = $resolvedRelease
    ExactCommit = $initialEvidence.ExactCommit
    PackageVersion = $initialEvidence.PackageVersion
    BundleAction = $bundleAction
    BundlePath = $initialEvidence.BundlePath
    PythonAction = $pythonAction
    PythonPath = $initialEvidence.PythonPath
    PilotAction = "preserve"
    PilotRoot = $initialEvidence.PilotRoot
    ReceiptAction = "preserve"
    InstallReceipt = $resolvedReceipt
    InstallReceiptSha256 = $receiptHashBefore
    AutoCADLaunched = $false
    PublishEnabled = $false
    LivePublishProven = $false
}
$operationTarget = (
    "bundle='$($initialEvidence.BundlePath)'; python='$($initialEvidence.PythonPath)'; " +
    "preserve pilot='$($initialEvidence.PilotRoot)'"
)
if (-not $PSCmdlet.ShouldProcess($operationTarget, "Uninstall verified CadPlot release components")) {
    if ($PassThru) { $preview } else { $preview | ConvertTo-Json -Depth 4 }
    return
}

# Remove the host-loadable bundle first. A later failure leaves no bundle for a new AutoCAD process
# to load; rerunning safely resumes from the receipt and verifies any component that remains.
$bundleRemovedThisRun = $false
Assert-AutoCADClosed
if ($bundleAction -eq "remove") {
    $bundleRemoval = & $bundleUninstaller `
        -DestinationRoot $bundleDestination `
        -Confirm:$false `
        -PassThru
    if ($bundleRemoval.Removed -ne $true) {
        throw "Verified AutoCAD bundle removal did not complete."
    }
    $bundleRemovedThisRun = $true
}

$pythonRemovedThisRun = $false
Assert-AutoCADClosed
if ($pythonAction -eq "remove") {
    $pythonRemoval = & $pythonUninstaller `
        -InstallRoot $initialEvidence.PythonPath `
        -Confirm:$false `
        -PassThru
    if ($pythonRemoval.Removed -ne $true) {
        throw "Verified Python environment removal did not complete."
    }
    $pythonRemovedThisRun = $true
}

$finalEvidence = & $installVerifier @verificationArguments
$receiptHashAfter = (
    Get-FileHash -LiteralPath $resolvedReceipt -Algorithm SHA256
).Hash.ToLowerInvariant()
if (
    $finalEvidence.Passed -ne $true -or
    $finalEvidence.BundlePresent -ne $false -or
    $finalEvidence.PythonPresent -ne $false -or
    $finalEvidence.PilotRoot -cne $initialEvidence.PilotRoot -or
    $finalEvidence.ReceiptSha256 -cne $receiptHashBefore -or
    $receiptHashAfter -cne $receiptHashBefore -or
    -not (Test-Path -LiteralPath $initialEvidence.PilotRoot -PathType Container) -or
    -not (Test-Path -LiteralPath $resolvedReceipt -PathType Leaf)
) {
    throw "Release uninstall did not preserve exact pilot/receipt evidence or remove both components."
}

$result = [pscustomobject]@{
    Uninstalled = $true
    WhatIf = $false
    ReleaseRoot = $resolvedRelease
    ExactCommit = $initialEvidence.ExactCommit
    PackageVersion = $initialEvidence.PackageVersion
    BundleAction = $bundleAction
    BundlePath = $initialEvidence.BundlePath
    BundleRemovedThisRun = $bundleRemovedThisRun
    PythonAction = $pythonAction
    PythonPath = $initialEvidence.PythonPath
    PythonRemovedThisRun = $pythonRemovedThisRun
    ComponentsRemovedThisRun = ([int]$bundleRemovedThisRun + [int]$pythonRemovedThisRun)
    PilotPreserved = $true
    PilotRoot = $initialEvidence.PilotRoot
    ReceiptPreserved = $true
    InstallReceipt = $resolvedReceipt
    InstallReceiptSha256 = $receiptHashAfter
    AutoCADLaunched = $false
    PublishEnabled = $false
    LivePublishProven = $false
}
if ($PassThru) { $result }
else { $result | ConvertTo-Json -Depth 4 }
