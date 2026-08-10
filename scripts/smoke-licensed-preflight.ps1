[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$preflight = Join-Path $PSScriptRoot "test-licensed-workstation.ps1"
$tempRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath()).TrimEnd('\')
$fixtureRoot = Join-Path $tempRoot ("cadplot-licensed-preflight-" + [Guid]::NewGuid().ToString("N"))
$oldEnvironment = @{
    CADPLOT_CONFIG = $env:CADPLOT_CONFIG
    CADPLOT_WORKSPACE_ROOT = $env:CADPLOT_WORKSPACE_ROOT
    CADPLOT_AUTOCAD_PROGID = $env:CADPLOT_AUTOCAD_PROGID
    CADPLOT_PIPE_NAME = $env:CADPLOT_PIPE_NAME
    CADPLOT_ENABLE_PUBLISH = $env:CADPLOT_ENABLE_PUBLISH
}

try {
    $release = Join-Path $fixtureRoot "release"
    $kitScripts = Join-Path $release "CadPlotMcp.release\scripts"
    $bundle = Join-Path $fixtureRoot "plugins\CadPlotMcp.bundle"
    $python = Join-Path $fixtureRoot "python\0.1.0-1111111"
    $commands = Join-Path $python "bin"
    $pilot = Join-Path $fixtureRoot "pilot"
    $workspace = Join-Path $pilot "pilot-work"
    $input = Join-Path $pilot "pilot-input"
    $receipts = Join-Path $pilot "install-receipts"
    $config = Join-Path $pilot "config.yaml"
    $receipt = Join-Path $receipts ("cadplot-install-" + ("1" * 40) + ".json")
    $adapter = Join-Path $bundle "Contents\Windows\2025\CadPlotMcp.AutoCAD2025.dll"
    $adapter2016 = Join-Path $bundle "Contents\Windows\2016\CadPlotMcp.AutoCAD2016.dll"
    $doctorJson = Join-Path $fixtureRoot "doctor.json"
    $doctorCommand = Join-Path $commands "cadplot-doctor.cmd"
    $output = Join-Path $pilot "licensed-preflight-2025.json"

    $null = New-Item -ItemType Directory -Path $kitScripts,$workspace,$input,$receipts,$commands
    $null = New-Item -ItemType Directory -Path `
        (Split-Path -Parent $adapter),(Split-Path -Parent $adapter2016)
    [System.IO.File]::WriteAllText($config, "version: 1`n", [System.Text.UTF8Encoding]::new($false))
    [System.IO.File]::WriteAllText($receipt, '{"fixture":true}', [System.Text.UTF8Encoding]::new($false))
    [System.IO.File]::WriteAllBytes($adapter, [System.Text.UTF8Encoding]::new($false).GetBytes("adapter-2025"))
    [System.IO.File]::WriteAllBytes(
        $adapter2016,
        [System.Text.UTF8Encoding]::new($false).GetBytes("adapter-2016")
    )
    $adapterHash = (Get-FileHash -LiteralPath $adapter -Algorithm SHA256).Hash.ToLowerInvariant()
    $adapter2016Hash = (
        Get-FileHash -LiteralPath $adapter2016 -Algorithm SHA256
    ).Hash.ToLowerInvariant()
    $receiptHash = (Get-FileHash -LiteralPath $receipt -Algorithm SHA256).Hash.ToLowerInvariant()

    $doctorEvidence = [ordered]@{
        schema_version = 1
        mode = "full"
        ready = $true
        config = $config
        workspace_root = $workspace
        autocad = [ordered]@{
            checked = $true
            available = $true
            progid = "AutoCAD.Application.25.0"
            version = "25.0s (LMS Tech)"
        }
        plugin = [ordered]@{
            checked = $true
            connected = $true
            inspection_identity_matched = $true
            status = [ordered]@{
                ok = $true
                readOnly = $true
                workspaceConfigured = $true
                publishEnabled = $false
                runtimeSupported = $true
                runtimeSeries = "R25.0"
                adapter = "autocad-2025-net8"
                buildCommit = "1" * 40
                pluginSha256 = $adapterHash
                product = "AutoCAD 2025 (ACADVER R25.0; raw 25.0s (LMS Tech))"
                queueAuthentication = $null
            }
        }
        errors = @()
    }
    [System.IO.File]::WriteAllText(
        $doctorJson,
        ($doctorEvidence | ConvertTo-Json -Depth 8),
        [System.Text.UTF8Encoding]::new($false)
    )
    [System.IO.File]::WriteAllText(
        $doctorCommand,
        "@echo off`r`ntype `"$doctorJson`"`r`nexit /b 0`r`n",
        [System.Text.ASCIIEncoding]::new()
    )

    $verifier = Join-Path $kitScripts "verify-release-install.ps1"
    $verifierContent = @"
param([string]`$ReleaseRoot,[string]`$ReceiptPath,[switch]`$PassThru)
`$result = [pscustomobject]@{
    Passed = `$true
    InstallationComplete = `$true
    ReceiptSha256 = '$receiptHash'
    ExactCommit = '$('1' * 40)'
    PackageVersion = '0.1.0'
    BundlePath = '$bundle'
    PythonPath = '$python'
    PilotRoot = '$pilot'
    Config = '$config'
    ConfigChangedSinceInstall = `$true
}
if (`$PassThru) { `$result } else { `$result | ConvertTo-Json }
"@
    [System.IO.File]::WriteAllText(
        $verifier,
        $verifierContent,
        [System.Text.UTF8Encoding]::new($false)
    )

    $env:CADPLOT_CONFIG = $config
    $env:CADPLOT_WORKSPACE_ROOT = $workspace
    $env:CADPLOT_AUTOCAD_PROGID = "AutoCAD.Application.25.0"
    $env:CADPLOT_PIPE_NAME = "cadplot-mcp-2025"
    Remove-Item Env:CADPLOT_ENABLE_PUBLISH -ErrorAction SilentlyContinue

    $positive = & $preflight `
        -ReleaseRoot $release `
        -ReceiptPath $receipt `
        -AutoCADRelease 2025 `
        -OutputPath $output `
        -PassThru
    if (
        $positive.Passed -ne $true -or
        $positive.ReadOnly -ne $true -or
        $positive.PublishEnabled -ne $false -or
        $positive.LivePublishProven -ne $false -or
        -not (Test-Path -LiteralPath $output -PathType Leaf)
    ) {
        throw "Licensed workstation positive preflight did not pass."
    }
    $record = Get-Content -LiteralPath $output -Raw -Encoding UTF8 | ConvertFrom-Json
    if (
        $record.licensed_workstation_preflight_ready -ne $true -or
        $record.autocad_release -cne "2025" -or
        $record.plugin_sha256 -cne $adapterHash -or
        $record.publish_enabled -ne $false -or
        $record.live_publish_proven -ne $false -or
        $record.licensed_live_pilot_ready -ne $false
    ) {
        throw "Licensed workstation evidence fields are invalid."
    }

    $overwriteBlocked = $false
    try {
        & $preflight `
            -ReleaseRoot $release `
            -ReceiptPath $receipt `
            -AutoCADRelease 2025 `
            -OutputPath $output | Out-Null
    }
    catch {
        $overwriteBlocked = $_.Exception.Message -like "*never overwritten*"
    }
    if (-not $overwriteBlocked) { throw "Licensed preflight overwrite was not blocked." }

    $doctorEvidence.autocad.progid = "AutoCAD.Application.20.1"
    $doctorEvidence.autocad.version = "20.1s (LMS Tech)"
    $doctorEvidence.plugin.status.runtimeSeries = "R20.1"
    $doctorEvidence.plugin.status.adapter = "autocad-2016-net45"
    $doctorEvidence.plugin.status.pluginSha256 = $adapter2016Hash
    $doctorEvidence.plugin.status.product = (
        "AutoCAD 2016 (ACADVER R20.1; raw 20.1s (LMS Tech))"
    )
    [System.IO.File]::WriteAllText(
        $doctorJson,
        ($doctorEvidence | ConvertTo-Json -Depth 8),
        [System.Text.UTF8Encoding]::new($false)
    )
    $env:CADPLOT_AUTOCAD_PROGID = "AutoCAD.Application.20.1"
    $env:CADPLOT_PIPE_NAME = "cadplot-mcp-2016"
    $output2016 = Join-Path $pilot "licensed-preflight-2016.json"
    $positive2016 = & $preflight `
        -ReleaseRoot $release `
        -ReceiptPath $receipt `
        -AutoCADRelease 2016 `
        -OutputPath $output2016 `
        -PassThru
    if (
        $positive2016.Passed -ne $true -or
        $positive2016.RuntimeSeries -cne "R20.1" -or
        $positive2016.PluginSha256 -cne $adapter2016Hash -or
        -not (Test-Path -LiteralPath $output2016 -PathType Leaf)
    ) {
        throw "AutoCAD 2016 licensed workstation preflight did not pass."
    }

    $doctorEvidence.autocad.progid = "AutoCAD.Application.25.0"
    $doctorEvidence.autocad.version = "25.0s (LMS Tech)"
    $doctorEvidence.plugin.status.runtimeSeries = "R25.0"
    $doctorEvidence.plugin.status.adapter = "autocad-2025-net8"
    $doctorEvidence.plugin.status.pluginSha256 = $adapterHash
    $doctorEvidence.plugin.status.product = (
        "AutoCAD 2025 (ACADVER R25.0; raw 25.0s (LMS Tech))"
    )
    [System.IO.File]::WriteAllText(
        $doctorJson,
        ($doctorEvidence | ConvertTo-Json -Depth 8),
        [System.Text.UTF8Encoding]::new($false)
    )
    $env:CADPLOT_AUTOCAD_PROGID = "AutoCAD.Application.25.0"
    $env:CADPLOT_PIPE_NAME = "cadplot-mcp-2025"

    $env:CADPLOT_ENABLE_PUBLISH = "1"
    $publishOutput = Join-Path $pilot "publish-enabled.json"
    $publishBlocked = $false
    try {
        & $preflight `
            -ReleaseRoot $release `
            -ReceiptPath $receipt `
            -AutoCADRelease 2025 `
            -OutputPath $publishOutput | Out-Null
    }
    catch {
        $publishBlocked = $_.Exception.Message -like "*must be unset*"
    }
    if (-not $publishBlocked -or (Test-Path -LiteralPath $publishOutput)) {
        throw "Publish-enabled read-only preflight was not blocked."
    }
    Remove-Item Env:CADPLOT_ENABLE_PUBLISH -ErrorAction SilentlyContinue

    $doctorEvidence.plugin.status.adapter = "autocad-2016-net45"
    [System.IO.File]::WriteAllText(
        $doctorJson,
        ($doctorEvidence | ConvertTo-Json -Depth 8),
        [System.Text.UTF8Encoding]::new($false)
    )
    $identityOutput = Join-Path $pilot "wrong-identity.json"
    $identityBlocked = $false
    try {
        & $preflight `
            -ReleaseRoot $release `
            -ReceiptPath $receipt `
            -AutoCADRelease 2025 `
            -OutputPath $identityOutput | Out-Null
    }
    catch {
        $identityBlocked = $_.Exception.Message -like "*identity evidence is inconsistent*"
    }
    if (-not $identityBlocked -or (Test-Path -LiteralPath $identityOutput)) {
        throw "Wrong live adapter identity was not blocked."
    }

    [pscustomobject]@{
        passed = $true
        positive_preflight = $true
        autocad_2016_preflight = $true
        autocad_2025_preflight = $true
        no_overwrite = $overwriteBlocked
        publish_enabled_blocked = $publishBlocked
        wrong_adapter_blocked = $identityBlocked
        autocad_launched = $false
        live_publish_proven = $false
    } | ConvertTo-Json
}
finally {
    foreach ($name in $oldEnvironment.Keys) {
        $value = $oldEnvironment[$name]
        if ($null -eq $value) { Remove-Item "Env:$name" -ErrorAction SilentlyContinue }
        else { [Environment]::SetEnvironmentVariable($name, $value, "Process") }
    }
    $resolvedFixture = [System.IO.Path]::GetFullPath($fixtureRoot)
    if ($resolvedFixture.StartsWith($tempRoot + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
        Remove-Item -LiteralPath $resolvedFixture -Recurse -Force -ErrorAction SilentlyContinue
    }
}
