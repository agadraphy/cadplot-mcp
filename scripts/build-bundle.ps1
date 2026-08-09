[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$AutoCAD2016SdkDir,

    [Parameter(Mandatory = $true)]
    [string]$AutoCAD2025SdkDir,

    [string]$DotNet = "dotnet",
    [ValidateSet("Release")]
    [string]$Configuration = "Release",
    [string]$Uv = "uv",
    [string]$OutputRoot = ""
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$dotnetRoot = Join-Path $repoRoot "src\dotnet"
$templateBundle = Join-Path $repoRoot "bundle\CadPlotMcp.bundle"

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
            throw "No existing ancestor was found for bundle output: $Path"
        }
        $current = $parent
    }
    while (-not [string]::IsNullOrWhiteSpace($current)) {
        $item = Get-Item -LiteralPath $current -Force
        if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Bundle output must not pass through a symlink or junction: $current"
        }
        $parent = Split-Path -Parent $current
        if ([string]::IsNullOrWhiteSpace($parent) -or $parent -eq $current) { break }
        $current = $parent
    }
}

function Get-PublicApiIdentity {
    param([Parameter(Mandatory = $true)]$Identity)

    [ordered]@{
        detected_series = $Identity.DetectedSeries
        assemblies = @($Identity.Assemblies | ForEach-Object {
            [ordered]@{
                name = $_.Name
                assembly_version = $_.AssemblyVersion
                sha256 = $_.Sha256
            }
        })
    }
}

function New-IsolatedBuildRoot {
    $tempRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath()).TrimEnd('\')
    $leaf = "cadplot-matching-sdk-build-{0}" -f ([Guid]::NewGuid().ToString("N"))
    $candidate = Join-Path $tempRoot $leaf
    if (Test-Path -LiteralPath $candidate) {
        throw "Isolated matching-SDK build root already exists: $candidate"
    }
    $created = New-Item -ItemType Directory -Path $candidate
    if (($created.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Isolated matching-SDK build root must not be redirected: $candidate"
    }
    return $created.FullName
}

function Remove-IsolatedBuildRoot {
    param([Parameter(Mandatory = $true)][string]$Path)

    $tempRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath()).TrimEnd('\')
    $resolved = [System.IO.Path]::GetFullPath($Path).TrimEnd('\')
    $leaf = Split-Path -Leaf $resolved
    if (
        (Split-Path -Parent $resolved) -cne $tempRoot -or
        $leaf -notmatch '^cadplot-matching-sdk-build-[0-9a-f]{32}$'
    ) {
        throw "Refusing to delete an unexpected build root: $resolved"
    }
    if (-not (Test-Path -LiteralPath $resolved)) { return }
    $items = @((Get-Item -LiteralPath $resolved -Force)) + @(
        Get-ChildItem -LiteralPath $resolved -Force -Recurse
    )
    foreach ($item in $items) {
        if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Refusing to delete a redirected build tree; inspect manually: $($item.FullName)"
        }
    }
    [System.IO.Directory]::Delete($resolved, $true)
}

$isolatedBuildRoot = $null
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
        throw "Real bundle build requires a clean worktree."
    }

    [xml]$packageManifest = Get-Content `
        -LiteralPath (Join-Path $templateBundle "PackageContents.xml") `
        -Raw `
        -Encoding UTF8
    $packageVersion = [string]$packageManifest.ApplicationPackage.AppVersion
    $pyproject = Get-Content -LiteralPath (Join-Path $repoRoot "pyproject.toml") -Raw -Encoding UTF8
    $versionMatch = [regex]::Match($pyproject, '(?m)^version = "([^"]+)"$')
    if (-not $versionMatch.Success -or $versionMatch.Groups[1].Value -ne $packageVersion) {
        throw "Python package and AutoCAD bundle versions do not match."
    }

    if ([string]::IsNullOrWhiteSpace($OutputRoot)) {
        $OutputRoot = Join-Path $repoRoot (
            "artifacts\cadplot-bundle-{0}-{1}" -f $packageVersion,$commit.Substring(0, 7)
        )
    }
    $resolvedOutputRoot = [System.IO.Path]::GetFullPath($OutputRoot)
    if (Test-Path -LiteralPath $resolvedOutputRoot) {
        throw "Bundle release target already exists; build never overwrites: $resolvedOutputRoot"
    }
    Assert-NoRedirectedAncestor -Path $resolvedOutputRoot

    & $Uv run python (Join-Path $PSScriptRoot "audit-source-tree.py")
    if ($LASTEXITCODE -ne 0) { throw "Source tree audit failed before bundle build." }

    $apiChecker = Join-Path $PSScriptRoot "check-autocad-api-series.ps1"
    $identity2016 = & $apiChecker `
        -AutoCADApiDir $AutoCAD2016SdkDir `
        -ExpectedSeries "R20.1" `
        -PassThru
    $identity2025 = & $apiChecker `
        -AutoCADApiDir $AutoCAD2025SdkDir `
        -ExpectedSeries "R25.0" `
        -PassThru
    $AutoCAD2016SdkDir = $identity2016.ApiDirectory
    $AutoCAD2025SdkDir = $identity2025.ApiDirectory

    $project2016 = Join-Path $dotnetRoot "CadPlotMcp.AutoCAD2016\CadPlotMcp.AutoCAD2016.csproj"
    $project2025 = Join-Path $dotnetRoot "CadPlotMcp.AutoCAD2025\CadPlotMcp.AutoCAD2025.csproj"

    # Never package an ignored bin/obj artifact from an earlier SDK or protocol-only build. Each
    # supported release is compiled into a fresh, release-specific tree outside the repository.
    $isolatedBuildRoot = New-IsolatedBuildRoot
    $build2016Root = Join-Path $isolatedBuildRoot "2016"
    $build2025Root = Join-Path $isolatedBuildRoot "2025"
    $configurationPivot = $Configuration.ToLowerInvariant()
    $bin2016 = Join-Path $build2016Root "bin\CadPlotMcp.AutoCAD2016\$configurationPivot"
    $bin2025 = Join-Path $build2025Root "bin\CadPlotMcp.AutoCAD2025\$configurationPivot"

    & $DotNet build $project2016 --configuration $Configuration --no-incremental `
        --artifacts-path $build2016Root `
        "-p:BuildAutoCADPlugin=true" "-p:AutoCAD2016SdkDir=$AutoCAD2016SdkDir" `
        "-p:RepositoryCommit=$commit" "-p:UseSharedCompilation=false"
    if ($LASTEXITCODE -ne 0) { throw "AutoCAD 2016 plug-in build failed." }

    & $DotNet build $project2025 --configuration $Configuration --no-incremental `
        --artifacts-path $build2025Root `
        "-p:BuildAutoCADPlugin=true" "-p:AutoCAD2025SdkDir=$AutoCAD2025SdkDir" `
        "-p:RepositoryCommit=$commit" "-p:UseSharedCompilation=false"
    if ($LASTEXITCODE -ne 0) { throw "AutoCAD 2025 plug-in build failed." }

    foreach ($output in @(
        (Join-Path $bin2016 "CadPlotMcp.AutoCAD2016.dll"),
        (Join-Path $bin2016 "CadPlotMcp.Core.dll"),
        (Join-Path $bin2025 "CadPlotMcp.AutoCAD2025.dll"),
        (Join-Path $bin2025 "CadPlotMcp.Core.dll")
    )) {
        if (-not (Test-Path -LiteralPath $output -PathType Leaf)) {
            throw "Isolated matching-SDK build did not produce a required DLL: $output"
        }
        $outputItem = Get-Item -LiteralPath $output -Force
        if (($outputItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Isolated matching-SDK output must not be redirected: $output"
        }
    }

    $commitAfterBuild = @(Invoke-GitReadOnly -Arguments @("rev-parse", "HEAD"))[0].Trim()
    $changesAfterBuild = @(Invoke-GitReadOnly -Arguments @(
        "status",
        "--porcelain=v1",
        "--untracked-files=all"
    ))
    if ($commitAfterBuild -ne $commit -or $changesAfterBuild.Count -ne 0) {
        throw "Repository identity changed during bundle compilation."
    }

    $null = New-Item -ItemType Directory -Path $resolvedOutputRoot
    $outputBundle = Join-Path $resolvedOutputRoot "CadPlotMcp.bundle"
    $null = New-Item -ItemType Directory -Path $outputBundle
    Copy-Item `
        -LiteralPath (Join-Path $templateBundle "PackageContents.xml") `
        -Destination $outputBundle
    Copy-Item -LiteralPath (Join-Path $repoRoot "LICENSE") -Destination $outputBundle

    $dest2016 = Join-Path $outputBundle "Contents\Windows\2016"
    $dest2025 = Join-Path $outputBundle "Contents\Windows\2025"
    $null = New-Item -ItemType Directory -Path $dest2016,$dest2025

    Copy-Item -LiteralPath (Join-Path $bin2016 "CadPlotMcp.AutoCAD2016.dll") -Destination $dest2016
    Copy-Item -LiteralPath (Join-Path $bin2016 "CadPlotMcp.Core.dll") -Destination $dest2016
    Copy-Item -LiteralPath (Join-Path $bin2025 "CadPlotMcp.AutoCAD2025.dll") -Destination $dest2025
    Copy-Item -LiteralPath (Join-Path $bin2025 "CadPlotMcp.Core.dll") -Destination $dest2025

    $bundleVerification = & (Join-Path $PSScriptRoot "verify-bundle.ps1") `
        -BundlePath $outputBundle `
        -PassThru

    $zipPath = Join-Path $resolvedOutputRoot "CadPlotMcp.bundle.zip"
    Compress-Archive -LiteralPath $outputBundle -DestinationPath $zipPath -CompressionLevel Optimal
    & $Uv run python (Join-Path $PSScriptRoot "audit-release-artifacts.py") $resolvedOutputRoot
    if ($LASTEXITCODE -ne 0) { throw "Bundle archive audit failed." }

    $buildManifestPath = Join-Path $resolvedOutputRoot "bundle-build.json"
    $buildManifest = [ordered]@{
        schema_version = 1
        exact_commit = $commit
        package_version = $packageVersion
        created_utc = [DateTime]::UtcNow.ToString("o")
        api_identity = [ordered]@{
            autocad_2016 = Get-PublicApiIdentity -Identity $identity2016
            autocad_2025 = Get-PublicApiIdentity -Identity $identity2025
        }
        bundle = [ordered]@{
            directory = "CadPlotMcp.bundle"
            archive = "CadPlotMcp.bundle.zip"
            archive_sha256 = (
                Get-FileHash -LiteralPath $zipPath -Algorithm SHA256
            ).Hash.ToLowerInvariant()
            files = @($bundleVerification.Hashes | ForEach-Object {
                [ordered]@{ path = $_.Path; sha256 = $_.Sha256 }
            })
        }
        source_tree_audit_passed = $true
        bundle_verification_passed = $true
        archive_audit_passed = $true
        matching_sdk_bundle_built = $true
        company_assets_copied = $false
        autodesk_binaries_included = $false
        autocad_launched = $false
        live_publish_proven = $false
    }
    $manifestJson = $buildManifest | ConvertTo-Json -Depth 7
    $manifestBytes = [System.Text.UTF8Encoding]::new($false).GetBytes($manifestJson)
    $stream = [System.IO.File]::Open(
        $buildManifestPath,
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

    $null = & (Join-Path $PSScriptRoot "verify-bundle-release.ps1") `
        -ReleaseRoot $resolvedOutputRoot `
        -PassThru

    [ordered]@{
        passed = $true
        release_root = $resolvedOutputRoot
        bundle = $outputBundle
        archive = $zipPath
        build_manifest = $buildManifestPath
        exact_commit = $commit
        package_version = $packageVersion
        matching_sdk_bundle_built = $true
        autocad_launched = $false
        live_publish_proven = $false
    } | ConvertTo-Json
}
finally {
    Pop-Location
    if (-not [string]::IsNullOrWhiteSpace($isolatedBuildRoot)) {
        Remove-IsolatedBuildRoot -Path $isolatedBuildRoot
    }
}
