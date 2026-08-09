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
    throw "Bundle release directory does not exist: $root"
}

$releaseItems = @((Get-Item -LiteralPath $root -Force)) + @(
    Get-ChildItem -LiteralPath $root -Force -Recurse
)
foreach ($item in $releaseItems) {
    if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Bundle release must not contain a symlink, junction, or redirected file: $($item.FullName)"
    }
}

$expectedTopLevel = @("CadPlotMcp.bundle", "CadPlotMcp.bundle.zip", "bundle-build.json")
$actualTopLevel = @(Get-ChildItem -LiteralPath $root -Force | Select-Object -ExpandProperty Name)
$missingTopLevel = @($expectedTopLevel | Where-Object { $_ -notin $actualTopLevel })
$unexpectedTopLevel = @($actualTopLevel | Where-Object { $_ -notin $expectedTopLevel })
if ($missingTopLevel.Count -gt 0 -or $unexpectedTopLevel.Count -gt 0) {
    throw "Bundle release top-level contents are not exact. Missing: $($missingTopLevel -join ', '); unexpected: $($unexpectedTopLevel -join ', ')"
}

$bundlePath = Join-Path $root "CadPlotMcp.bundle"
$archivePath = Join-Path $root "CadPlotMcp.bundle.zip"
$manifestPath = Join-Path $root "bundle-build.json"
$bundleVerification = & (Join-Path $PSScriptRoot "verify-bundle.ps1") `
    -BundlePath $bundlePath `
    -PassThru

$manifestItem = Get-Item -LiteralPath $manifestPath -Force
if ($manifestItem.Length -gt 1MB) {
    throw "Bundle build manifest exceeds the 1 MiB safety limit."
}
try {
    $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
}
catch {
    throw "Bundle build manifest is not valid UTF-8 JSON."
}

if ($manifest.schema_version -ne 1) { throw "Unsupported bundle build manifest schema." }
if ($manifest.exact_commit -notmatch '^[0-9a-f]{40}$') {
    throw "Bundle build manifest has an invalid exact commit."
}
if ([string]::IsNullOrWhiteSpace([string]$manifest.package_version)) {
    throw "Bundle build manifest has no package version."
}
$protocolOnlyFixture = $manifest.protocol_only_fixture -eq $true
if (
    $manifest.source_tree_audit_passed -ne $true -or
    $manifest.bundle_verification_passed -ne $true -or
    $manifest.archive_audit_passed -ne $true -or
    $manifest.company_assets_copied -ne $false -or
    $manifest.autodesk_binaries_included -ne $false -or
    $manifest.autocad_launched -ne $false -or
    $manifest.live_publish_proven -ne $false
) {
    throw "Bundle build manifest safety/evidence flags are invalid."
}
if ($manifest.matching_sdk_bundle_built -ne $true) {
    if (-not $AllowProtocolOnlyFixture -or -not $protocolOnlyFixture) {
        throw "Bundle release is not a matching-SDK build."
    }
}
elseif ($protocolOnlyFixture) {
    throw "A matching-SDK bundle cannot be labelled as a protocol-only fixture."
}

[xml]$package = Get-Content `
    -LiteralPath (Join-Path $bundlePath "PackageContents.xml") `
    -Raw `
    -Encoding UTF8
if ([string]$package.ApplicationPackage.AppVersion -cne [string]$manifest.package_version) {
    throw "Bundle manifest and build manifest versions do not match."
}
if (
    $manifest.bundle.directory -cne "CadPlotMcp.bundle" -or
    $manifest.bundle.archive -cne "CadPlotMcp.bundle.zip"
) {
    throw "Bundle build manifest names are invalid."
}

$shaPattern = '^[0-9a-f]{64}$'
$expectedApiNames = @("AcMgd.dll", "AcDbMgd.dll", "AcCoreMgd.dll")
foreach ($api in @(
    @{ Name = "AutoCAD 2016"; Value = $manifest.api_identity.autocad_2016; Series = "R20.1"; Version = '^20\.1\.' },
    @{ Name = "AutoCAD 2025"; Value = $manifest.api_identity.autocad_2025; Series = "R25.0"; Version = '^25\.0\.' }
)) {
    if ($api.Value.detected_series -cne $api.Series) {
        throw "$($api.Name) build identity is not $($api.Series)."
    }
    $assemblies = @($api.Value.assemblies)
    $actualNames = @($assemblies | Select-Object -ExpandProperty name)
    if (
        $assemblies.Count -ne 3 -or
        @($expectedApiNames | Where-Object { $_ -notin $actualNames }).Count -ne 0 -or
        @($actualNames | Select-Object -Unique).Count -ne 3
    ) {
        throw "$($api.Name) build identity has an invalid assembly set."
    }
    foreach ($assembly in $assemblies) {
        if (
            [string]$assembly.assembly_version -notmatch $api.Version -or
            [string]$assembly.sha256 -notmatch $shaPattern
        ) {
            throw "$($api.Name) build identity has invalid assembly evidence."
        }
    }
}

$manifestFiles = @($manifest.bundle.files)
if ($manifestFiles.Count -ne $bundleVerification.Hashes.Count) {
    throw "Bundle build manifest file count does not match the verified bundle."
}
foreach ($expectedHash in $bundleVerification.Hashes) {
    $matches = @($manifestFiles | Where-Object { $_.path -ceq $expectedHash.Path })
    if ($matches.Count -ne 1 -or $matches[0].sha256 -cne $expectedHash.Sha256) {
        throw "Bundle build manifest file hash mismatch: $($expectedHash.Path)"
    }
}

$archiveHash = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant()
if ($manifest.bundle.archive_sha256 -cne $archiveHash) {
    throw "Bundle archive hash does not match the build manifest."
}

Add-Type -AssemblyName System.IO.Compression.FileSystem
$archive = [System.IO.Compression.ZipFile]::OpenRead($archivePath)
try {
    $fileEntries = @($archive.Entries | Where-Object {
        -not [string]::IsNullOrEmpty($_.Name)
    })
    $expectedEntryNames = @($bundleVerification.Hashes | ForEach-Object {
        "CadPlotMcp.bundle/$($_.Path)"
    })
    $actualEntryNames = @($fileEntries | ForEach-Object { $_.FullName.Replace('\', '/') })
    if (
        $fileEntries.Count -ne $expectedEntryNames.Count -or
        @($expectedEntryNames | Where-Object { $_ -notin $actualEntryNames }).Count -ne 0
    ) {
        throw "Bundle archive entries do not exactly match the verified bundle."
    }
    foreach ($entry in $fileEntries) {
        $relative = $entry.FullName.Replace('\', '/').Substring("CadPlotMcp.bundle/".Length)
        $expected = @($bundleVerification.Hashes | Where-Object { $_.Path -ceq $relative })
        $algorithm = [System.Security.Cryptography.SHA256]::Create()
        $entryStream = $entry.Open()
        try {
            $entryHash = [BitConverter]::ToString(
                $algorithm.ComputeHash($entryStream)
            ).Replace('-', '').ToLowerInvariant()
        }
        finally {
            $entryStream.Dispose()
            $algorithm.Dispose()
        }
        if ($expected.Count -ne 1 -or $entryHash -cne $expected[0].Sha256) {
            throw "Bundle archive entry hash mismatch: $relative"
        }
    }
}
finally {
    $archive.Dispose()
}

$result = [pscustomobject]@{
    Passed = $true
    ReleaseRoot = $root
    ExactCommit = $manifest.exact_commit
    PackageVersion = $manifest.package_version
    ArchiveSha256 = $archiveHash
    MatchingSdkBundleBuilt = $manifest.matching_sdk_bundle_built -eq $true
    ProtocolOnlyFixture = $protocolOnlyFixture
    AutoCADLaunched = $false
    LivePublishProven = $false
}
if ($PassThru) {
    $result
}
else {
    $result | ConvertTo-Json -Depth 3
}
