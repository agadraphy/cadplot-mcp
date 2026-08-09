[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = "Medium")]
param(
    [string]$SourceBundle = "",
    [string]$DestinationRoot = ""
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($SourceBundle)) {
    $SourceBundle = Join-Path $repoRoot "artifacts\CadPlotMcp.bundle"
}
if ([string]::IsNullOrWhiteSpace($DestinationRoot)) {
    $DestinationRoot = Join-Path ([Environment]::GetFolderPath("ApplicationData")) "Autodesk\ApplicationPlugins"
}

$resolvedSource = [System.IO.Path]::GetFullPath($SourceBundle)
$resolvedDestinationRoot = [System.IO.Path]::GetFullPath($DestinationRoot)
$destinationBundle = Join-Path $resolvedDestinationRoot "CadPlotMcp.bundle"
$verifier = Join-Path $PSScriptRoot "verify-bundle.ps1"

function Assert-NoRedirectedAncestor {
    param([Parameter(Mandatory = $true)][string]$Path)

    $current = [System.IO.Path]::GetFullPath($Path)
    while (-not (Test-Path -LiteralPath $current)) {
        $parent = Split-Path -Parent $current
        if ([string]::IsNullOrWhiteSpace($parent) -or $parent -eq $current) {
            throw "No existing ancestor was found for installation path: $Path"
        }
        $current = $parent
    }
    while (-not [string]::IsNullOrWhiteSpace($current)) {
        $item = Get-Item -LiteralPath $current -Force
        if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Installation path must not pass through a symlink or junction: $current"
        }
        $parent = Split-Path -Parent $current
        if ([string]::IsNullOrWhiteSpace($parent) -or $parent -eq $current) { break }
        $current = $parent
    }
}

function Assert-SameBundleHashes {
    param(
        [Parameter(Mandatory = $true)]$Expected,
        [Parameter(Mandatory = $true)]$Actual
    )

    if ($Expected.Hashes.Count -ne $Actual.Hashes.Count) {
        throw "Copied bundle file count changed after verification."
    }
    foreach ($expectedHash in $Expected.Hashes) {
        $matches = @($Actual.Hashes | Where-Object { $_.Path -ceq $expectedHash.Path })
        if ($matches.Count -ne 1 -or $matches[0].Sha256 -cne $expectedHash.Sha256) {
            throw "Copied bundle hash mismatch: $($expectedHash.Path)"
        }
    }
}

if (-not (Test-Path -LiteralPath $resolvedSource -PathType Container)) {
    throw "Bundle source does not exist: $resolvedSource"
}
if (-not (Test-Path -LiteralPath (Join-Path $resolvedSource "PackageContents.xml") -PathType Leaf)) {
    throw "Bundle source has no PackageContents.xml: $resolvedSource"
}
$sourceVerification = & $verifier -BundlePath $resolvedSource -PassThru
if (Test-Path -LiteralPath $destinationBundle) {
    throw "CadPlotMcp.bundle is already installed. This script will not overwrite it: $destinationBundle"
}
Assert-NoRedirectedAncestor -Path $resolvedSource
Assert-NoRedirectedAncestor -Path $resolvedDestinationRoot

if ($PSCmdlet.ShouldProcess($destinationBundle, "Install CadPlot MCP bundle")) {
    New-Item -ItemType Directory -Path $resolvedDestinationRoot -Force | Out-Null
    $stagingBundle = Join-Path $resolvedDestinationRoot (
        ".CadPlotMcp.bundle.installing-{0}" -f [Guid]::NewGuid().ToString("N")
    )
    try {
        Copy-Item -LiteralPath $resolvedSource -Destination $stagingBundle -Recurse
        $stagingVerification = & $verifier -BundlePath $stagingBundle -PassThru
        Assert-SameBundleHashes -Expected $sourceVerification -Actual $stagingVerification
        [System.IO.Directory]::Move($stagingBundle, $destinationBundle)
        Write-Output "Installed verified bundle atomically: $destinationBundle"
    }
    catch {
        throw "Bundle installation failed. Any non-loadable staging directory was retained at '$stagingBundle'. $($_.Exception.Message)"
    }
}
