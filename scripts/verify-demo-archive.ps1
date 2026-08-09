[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$DeliveryRoot,

    [switch]$PassThru
)

$ErrorActionPreference = "Stop"
$root = [System.IO.Path]::GetFullPath($DeliveryRoot).TrimEnd('\')
if (-not (Test-Path -LiteralPath $root -PathType Container)) {
    throw "Demo delivery directory does not exist: $root"
}

$allItems = @((Get-Item -LiteralPath $root -Force)) + @(
    Get-ChildItem -LiteralPath $root -Force -Recurse
)
foreach ($item in $allItems) {
    if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Demo delivery must not contain a symlink, junction, or redirected file."
    }
}

$outerManifestPath = Join-Path $root "demo-kit-build.json"
$topDirectories = @(Get-ChildItem -LiteralPath $root -Force -Directory)
$topArchives = @(Get-ChildItem -LiteralPath $root -Force -File -Filter "*.zip")
$topFiles = @(Get-ChildItem -LiteralPath $root -Force -File)
if (
    $topDirectories.Count -ne 1 -or
    $topArchives.Count -ne 1 -or
    $topFiles.Count -ne 2 -or
    -not (Test-Path -LiteralPath $outerManifestPath -PathType Leaf)
) { throw "Demo delivery top-level contents are not exact." }

$outerItem = Get-Item -LiteralPath $outerManifestPath -Force
if ($outerItem.Length -gt 1MB) { throw "Demo delivery manifest exceeds the 1 MiB safety limit." }
try { $outer = Get-Content -LiteralPath $outerManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json }
catch { throw "Demo delivery manifest is not valid UTF-8 JSON." }

$shaPattern = '^[0-9a-f]{64}$'
if (
    $outer.schema_version -ne 1 -or
    [string]$outer.exact_commit -notmatch '^[0-9a-f]{40}$' -or
    [string]$outer.package_version -notmatch '^[0-9]+(?:\.[0-9]+){2}(?:[A-Za-z0-9.+-]*)?$' -or
    [string]$outer.kit_directory -notmatch '^cadplot-demo-kit-[0-9a-f]{7}$' -or
    $outer.kit_directory -cne "cadplot-demo-kit-$(([string]$outer.exact_commit).Substring(0, 7))" -or
    $outer.kit_archive -cne "$($outer.kit_directory).zip" -or
    $outer.kit_manifest -cne "demo-kit.json" -or
    [string]$outer.kit_manifest_sha256 -notmatch $shaPattern -or
    [string]$outer.kit_archive_sha256 -notmatch $shaPattern -or
    $outer.archive_file_count -ne 10 -or
    $outer.local_demo_ready -ne $true -or
    $outer.licensed_live_pilot_ready -ne $false -or
    $outer.public_release_ready -ne $false -or
    $outer.company_assets_copied -ne $false -or
    $outer.autodesk_binaries_included -ne $false -or
    $outer.autocad_launched -ne $false -or
    $outer.live_publish_proven -ne $false
) { throw "Demo delivery identity or safety/evidence fields are invalid." }

$kitRoot = Join-Path $root ([string]$outer.kit_directory)
$archivePath = Join-Path $root ([string]$outer.kit_archive)
if (
    $topDirectories[0].Name -cne $outer.kit_directory -or
    $topArchives[0].Name -cne $outer.kit_archive -or
    -not (Test-Path -LiteralPath $kitRoot -PathType Container) -or
    -not (Test-Path -LiteralPath $archivePath -PathType Leaf)
) { throw "Demo delivery directory or archive identity is invalid." }
if ((Get-Item -LiteralPath $archivePath).Length -gt 25MB) {
    throw "Demo delivery archive exceeds the 25 MiB safety limit."
}

$embeddedVerifier = Join-Path $kitRoot "verify-demo-kit.ps1"
if (-not (Test-Path -LiteralPath $embeddedVerifier -PathType Leaf)) {
    throw "Demo delivery embedded verifier is missing."
}
$innerVerification = & $embeddedVerifier -KitRoot $kitRoot -PassThru
if (
    $innerVerification.Passed -ne $true -or
    $innerVerification.ExactCommit -cne $outer.exact_commit -or
    $innerVerification.MachinePathsIncluded -ne $false -or
    $innerVerification.DependencyAuditPassed -ne $true -or
    $innerVerification.SbomVerified -ne $true -or
    $innerVerification.AutoCADLaunched -ne $false -or
    $innerVerification.LivePublishProven -ne $false
) { throw "Demo delivery embedded verification failed." }

$innerManifestPath = Join-Path $kitRoot "demo-kit.json"
$innerManifestHash = (
    Get-FileHash -LiteralPath $innerManifestPath -Algorithm SHA256
).Hash.ToLowerInvariant()
$archiveHash = (
    Get-FileHash -LiteralPath $archivePath -Algorithm SHA256
).Hash.ToLowerInvariant()
if (
    $outer.kit_manifest_sha256 -cne $innerManifestHash -or
    $outer.kit_archive_sha256 -cne $archiveHash
) { throw "Demo delivery archive hash mismatch or inner manifest hash mismatch." }
try { $inner = Get-Content -LiteralPath $innerManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json }
catch { throw "Demo delivery inner manifest is not valid UTF-8 JSON." }
if (
    $inner.exact_commit -cne $outer.exact_commit -or
    $inner.package_version -cne $outer.package_version -or
    ($inner.sbom | ConvertTo-Json -Compress -Depth 4) -cne
        ($outer.sbom | ConvertTo-Json -Compress -Depth 4)
) { throw "Demo delivery inner and outer evidence is inconsistent." }

$directoryFiles = @(Get-ChildItem -LiteralPath $kitRoot -File -Force | Sort-Object Name)
if ($directoryFiles.Count -ne $outer.archive_file_count) {
    throw "Demo delivery directory file count is invalid."
}
$expectedEntryNames = @($directoryFiles | ForEach-Object {
    "$($outer.kit_directory)/$($_.Name)"
})

Add-Type -AssemblyName System.IO.Compression.FileSystem
$archive = [System.IO.Compression.ZipFile]::OpenRead($archivePath)
try {
    $fileEntries = @($archive.Entries | Where-Object { -not [string]::IsNullOrEmpty($_.Name) })
    $directoryEntries = @($archive.Entries | Where-Object { [string]::IsNullOrEmpty($_.Name) })
    foreach ($entry in $archive.Entries) {
        $name = $entry.FullName.Replace('\', '/')
        $segments = @($name.Split('/') | Where-Object { $_ -ne '' })
        if (
            $name.StartsWith('/') -or
            $name.Contains(':') -or
            @($segments | Where-Object { $_ -eq '.' -or $_ -eq '..' }).Count -ne 0 -or
            -not $name.StartsWith("$($outer.kit_directory)/", [StringComparison]::Ordinal) -or
            $entry.Length -gt 10MB
        ) {
            throw "Demo delivery archive contains an unsafe entry name or size: $($entry.FullName)"
        }
    }
    if (
        $directoryEntries.Count -gt 1 -or
        (
            $directoryEntries.Count -eq 1 -and
            $directoryEntries[0].FullName.Replace('\', '/') -cne "$($outer.kit_directory)/"
        ) -or
        $fileEntries.Count -ne $expectedEntryNames.Count -or
        @(
            $fileEntries |
                ForEach-Object { $_.FullName.Replace('\', '/') } |
                Group-Object |
                Where-Object Count -ne 1
        ).Count -ne 0
    ) {
        $observed = @($archive.Entries | ForEach-Object { $_.FullName.Replace('\', '/') }) -join ', '
        throw "Demo delivery archive entry set is not exact (directories=$($directoryEntries.Count), files=$($fileEntries.Count)): $observed"
    }
    $actualEntryNames = @($fileEntries | ForEach-Object { $_.FullName.Replace('\', '/') })
    if (@($expectedEntryNames | Where-Object { $_ -notin $actualEntryNames }).Count -ne 0) {
        throw "Demo delivery archive entry set is not exact."
    }
    foreach ($entry in $fileEntries) {
        $relative = $entry.FullName.Replace('\', '/').Substring(
            ([string]$outer.kit_directory).Length + 1
        )
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
        $directoryHash = (
            Get-FileHash -LiteralPath (Join-Path $kitRoot $relative) -Algorithm SHA256
        ).Hash.ToLowerInvariant()
        if ($entryHash -cne $directoryHash) {
            throw "Demo delivery archive entry hash mismatch: $relative"
        }
    }
}
finally { $archive.Dispose() }

$result = [pscustomobject]@{
    Passed = $true
    DeliveryRoot = $root
    KitRoot = $kitRoot
    ExactCommit = $outer.exact_commit
    PackageVersion = $outer.package_version
    ManifestSha256 = $innerManifestHash
    ArchiveSha256 = $archiveHash
    SbomSha256 = $outer.sbom.sha256
    ArchiveFileCount = $fileEntries.Count
    MachinePathsIncluded = $false
    LocalDemoReady = $true
    AutoCADLaunched = $false
    LivePublishProven = $false
}
if ($PassThru) { $result }
else { $result | ConvertTo-Json -Depth 3 }
