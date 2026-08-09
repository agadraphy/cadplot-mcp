[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$smokeRoot = Join-Path ([System.IO.Path]::GetTempPath()) (
    "cadplot-demo-kit-smoke-{0}" -f [Guid]::NewGuid().ToString("N")
)
$resolvedRoot = [System.IO.Path]::GetFullPath($smokeRoot)
$tempBoundary = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath()).TrimEnd('\') + '\'
if (-not $resolvedRoot.StartsWith($tempBoundary, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Demo-kit smoke root escaped the system temporary directory."
}

try {
    $null = New-Item -ItemType Directory -Path $resolvedRoot
    $verifier = Join-Path $resolvedRoot "verify-demo-kit.ps1"
    $wheel = Join-Path $resolvedRoot "cadplot_mcp-0.1.0-py3-none-any.whl"
    $source = Join-Path $resolvedRoot "cadplot-mcp-source-0000000.zip"
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot "verify-demo-kit.ps1") -Destination $verifier
    [System.IO.File]::WriteAllText($wheel, "synthetic wheel", [System.Text.UTF8Encoding]::new($false))
    [System.IO.File]::WriteAllText($source, "synthetic source", [System.Text.UTF8Encoding]::new($false))
    $files = @(@($verifier, $wheel, $source) | ForEach-Object {
        [ordered]@{
            path = [System.IO.Path]::GetFileName($_)
            sha256 = (Get-FileHash -LiteralPath $_ -Algorithm SHA256).Hash.ToLowerInvariant()
        }
    })
    $manifest = [ordered]@{
        schema_version = 2
        exact_commit = "0" * 40
        created_utc = [DateTime]::UtcNow.ToString("o")
        source_archive = [ordered]@{ file = [System.IO.Path]::GetFileName($source); sha256 = $files[2].sha256 }
        wheel = [ordered]@{ file = [System.IO.Path]::GetFileName($wheel); sha256 = $files[1].sha256 }
        files = $files
        local_demo_ready = $true
        licensed_live_pilot_ready = $false
        public_release_ready = $false
        source_tree_audit_passed = $true
        api_probe_ran = $true
        api_probe = [ordered]@{
            passed = $true
            detected_series = "R24.3"
            target_framework = "net48"
            assemblies = @(
                [ordered]@{ Name = "AcMgd.dll"; Series = "R24.3"; Sha256 = "a" * 64 },
                [ordered]@{ Name = "AcDbMgd.dll"; Series = "R24.3"; Sha256 = "b" * 64 },
                [ordered]@{ Name = "AcCoreMgd.dll"; Series = "R24.3"; Sha256 = "c" * 64 }
            )
            autocad_launched = $false
            live_publish_proven = $false
            evidence_scope = "compile-only"
        }
        dependency_audit_ran = $true
        dependency_audit = [ordered]@{
            schema_version = 1
            generated_utc = [DateTime]::UtcNow.ToString("o")
            passed = $true
            lock = [ordered]@{
                file = "uv.lock"
                sha256 = "e" * 64
                requirements_sha256 = "f" * 64
            }
            python = [ordered]@{
                tool = "pip-audit fixture"
                package_count = 1
                vulnerability_count = 0
                database = "fixture"
            }
            python_license_inventory = [ordered]@{
                package_count = 1
                unknown_count = 0
                packages = @(
                    [ordered]@{ name = "fixture"; version = "1.0"; license = "MIT" }
                )
            }
            dotnet = [ordered]@{
                tool = "dotnet fixture"
                project_count = 4
                vulnerability_count = 0
                source_count = 1
                database = "fixture"
            }
            network_database_check = $true
            autocad_launched = $false
            live_publish_proven = $false
        }
        synthetic_batch_rehearsal = [ordered]@{
            target_drawings = 300; ready = 300; staged = 300; outputs_complete = 300
            execution_verified = 0; publish_verified = 0; manual_review_without_receipts = 300
            source_unchanged = $true; evidence_digest = "sha256:$('d' * 64)"; synthetic = $true
        }
        company_assets_copied = $false
        autodesk_binaries_included = $false
        autocad_launched = $false
        live_publish_proven = $false
        purpose = "Synthetic verifier fixture"
    }
    [System.IO.File]::WriteAllText(
        (Join-Path $resolvedRoot "demo-kit.json"),
        ($manifest | ConvertTo-Json -Depth 7),
        [System.Text.UTF8Encoding]::new($false)
    )

    $result = & $verifier -KitRoot $resolvedRoot -PassThru
    if (
        $result.Passed -ne $true -or
        $result.MachinePathsIncluded -ne $false -or
        $result.DependencyAuditPassed -ne $true
    ) {
        throw "Demo-kit verifier did not accept the exact path-redacted fixture."
    }
    $manifestPath = Join-Path $resolvedRoot "demo-kit.json"
    $manifestBytes = [System.IO.File]::ReadAllBytes($manifestPath)
    $manifest.dependency_audit.python_license_inventory.packages[0].license = "UNKNOWN"
    [System.IO.File]::WriteAllText(
        $manifestPath,
        ($manifest | ConvertTo-Json -Depth 7),
        [System.Text.UTF8Encoding]::new($false)
    )
    $licenseTamperBlocked = $false
    try { & $verifier -KitRoot $resolvedRoot -PassThru }
    catch {
        if ($_.Exception.Message -notlike "*license inventory*") { throw }
        $licenseTamperBlocked = $true
    }
    if (-not $licenseTamperBlocked) {
        throw "Demo-kit verifier accepted an unknown dependency license."
    }
    [System.IO.File]::WriteAllBytes($manifestPath, $manifestBytes)
    [System.IO.File]::AppendAllText($wheel, "tamper", [System.Text.UTF8Encoding]::new($false))
    $tamperBlocked = $false
    try { & $verifier -KitRoot $resolvedRoot -PassThru }
    catch {
        if ($_.Exception.Message -notlike "*file hash mismatch*") { throw }
        $tamperBlocked = $true
    }
    if (-not $tamperBlocked) { throw "Demo-kit verifier accepted a modified wheel." }

    [ordered]@{
        passed = $true
        exact_tree_and_hashes_verified = $true
        machine_paths_redacted = $true
        dependency_audit_verified = $true
        dependency_license_tamper_blocked = $licenseTamperBlocked
        wheel_tamper_blocked = $true
        autocad_launched = $false
        live_publish_proven = $false
    } | ConvertTo-Json
}
finally {
    if (Test-Path -LiteralPath $resolvedRoot) {
        Remove-Item -LiteralPath $resolvedRoot -Recurse -Force
    }
}
