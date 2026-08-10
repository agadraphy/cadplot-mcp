[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ReleaseRoot,

    [Parameter(Mandatory = $true)]
    [string]$ReceiptPath,

    [Parameter(Mandatory = $true)]
    [ValidateSet("2016", "2025")]
    [string]$AutoCADRelease,

    [ValidateSet("ReadOnly", "Publish")]
    [string]$SessionMode = "ReadOnly",

    [string]$ReadOnlyPreflightPath = "",

    [Parameter(Mandatory = $true)]
    [string]$OutputPath,

    [ValidateRange(1, 60000)]
    [int]$TimeoutMs = 2000,

    [switch]$PassThru
)

$ErrorActionPreference = "Stop"

function Get-NormalizedPath {
    param([Parameter(Mandatory = $true)][string]$Path)

    return [System.IO.Path]::GetFullPath($Path).TrimEnd('\')
}

function Assert-NoRedirectedAncestor {
    param([Parameter(Mandatory = $true)][string]$Path)

    $current = [System.IO.Path]::GetFullPath($Path)
    while (-not (Test-Path -LiteralPath $current)) {
        $parent = Split-Path -Parent $current
        if ([string]::IsNullOrWhiteSpace($parent) -or $parent -eq $current) {
            throw "Licensed preflight path has no existing local ancestor: $Path"
        }
        $current = $parent
    }
    while (-not [string]::IsNullOrWhiteSpace($current)) {
        $item = Get-Item -LiteralPath $current -Force
        if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Licensed preflight path must not pass through a symlink or junction: $current"
        }
        $parent = Split-Path -Parent $current
        if ([string]::IsNullOrWhiteSpace($parent) -or $parent -eq $current) { break }
        $current = $parent
    }
}

function Get-BytesSha256 {
    param([Parameter(Mandatory = $true)][byte[]]$Bytes)

    $algorithm = [System.Security.Cryptography.SHA256]::Create()
    try {
        return [System.BitConverter]::ToString($algorithm.ComputeHash($Bytes)).Replace("-", "").ToLowerInvariant()
    }
    finally { $algorithm.Dispose() }
}

function Read-StableJsonFile {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Label
    )

    $resolved = [System.IO.Path]::GetFullPath($Path)
    if (-not (Test-Path -LiteralPath $resolved -PathType Leaf)) {
        throw "$Label does not exist."
    }
    Assert-NoRedirectedAncestor -Path $resolved
    $before = Get-Item -LiteralPath $resolved -Force
    if ($before.Length -gt 1MB) { throw "$Label exceeds the 1 MiB safety limit." }
    $bytes = [System.IO.File]::ReadAllBytes($resolved)
    $middle = Get-Item -LiteralPath $resolved -Force
    if (
        $before.Length -ne $middle.Length -or
        $before.LastWriteTimeUtc.Ticks -ne $middle.LastWriteTimeUtc.Ticks -or
        $bytes.Length -ne $middle.Length
    ) {
        throw "$Label changed while it was being read."
    }
    $sha256 = Get-BytesSha256 -Bytes $bytes
    $confirmationBytes = [System.IO.File]::ReadAllBytes($resolved)
    $after = Get-Item -LiteralPath $resolved -Force
    if (
        $middle.Length -ne $after.Length -or
        $middle.LastWriteTimeUtc.Ticks -ne $after.LastWriteTimeUtc.Ticks -or
        $confirmationBytes.Length -ne $after.Length -or
        (Get-BytesSha256 -Bytes $confirmationBytes) -cne $sha256
    ) {
        throw "$Label did not produce two identical stable snapshots."
    }
    try {
        $text = [System.Text.UTF8Encoding]::new($false, $true).GetString($bytes)
        $value = $text | ConvertFrom-Json
    }
    catch { throw "$Label is not valid UTF-8 JSON." }
    return [pscustomobject]@{
        Path = $resolved
        Sha256 = $sha256
        Length = $bytes.Length
        LastWriteTimeUtcTicks = $after.LastWriteTimeUtc.Ticks
        Value = $value
    }
}

function Assert-JsonSnapshotUnchanged {
    param(
        [Parameter(Mandatory = $true)]$Snapshot,
        [Parameter(Mandatory = $true)][string]$Label
    )

    $current = Read-StableJsonFile -Path $Snapshot.Path -Label $Label
    if (
        $current.Sha256 -cne $Snapshot.Sha256 -or
        $current.Length -ne $Snapshot.Length -or
        $current.LastWriteTimeUtcTicks -ne $Snapshot.LastWriteTimeUtcTicks
    ) {
        throw "$Label changed during licensed session verification."
    }
}

function Assert-EnvironmentPath {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Expected
    )

    $value = [Environment]::GetEnvironmentVariable($Name, "Process")
    if ([string]::IsNullOrWhiteSpace($value)) {
        throw "$Name must be set for the current licensed-workstation process."
    }
    $actual = Get-NormalizedPath -Path $value
    if (-not $actual.Equals($Expected, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "$Name does not match the verified installation evidence."
    }
}

function Write-NewUtf8Json {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)]$Value
    )

    $bytes = [System.Text.UTF8Encoding]::new($false).GetBytes(
        ($Value | ConvertTo-Json -Depth 10)
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

$expected = if ($AutoCADRelease -eq "2016") {
    [pscustomobject]@{
        ProgId = "AutoCAD.Application.20.1"
        PipeName = "cadplot-mcp-2016"
        RuntimeSeries = "R20.1"
        VersionPrefix = "20.1"
        Adapter = "autocad-2016-net45"
        AdapterRelativePath = "Contents\Windows\2016\CadPlotMcp.AutoCAD2016.dll"
    }
}
else {
    [pscustomobject]@{
        ProgId = "AutoCAD.Application.25.0"
        PipeName = "cadplot-mcp-2025"
        RuntimeSeries = "R25.0"
        VersionPrefix = "25.0"
        Adapter = "autocad-2025-net8"
        AdapterRelativePath = "Contents\Windows\2025\CadPlotMcp.AutoCAD2025.dll"
    }
}

$release = Get-NormalizedPath -Path $ReleaseRoot
$receipt = [System.IO.Path]::GetFullPath($ReceiptPath)
$output = [System.IO.Path]::GetFullPath($OutputPath)
$verifier = Join-Path $release "CadPlotMcp.release\scripts\verify-release-install.ps1"
if (-not (Test-Path -LiteralPath $verifier -PathType Leaf)) {
    throw "Embedded installed-release verifier is missing."
}
if (Test-Path -LiteralPath $output) {
    throw "Output already exists; licensed preflight evidence is never overwritten."
}
Assert-NoRedirectedAncestor -Path $release
Assert-NoRedirectedAncestor -Path $receipt
Assert-NoRedirectedAncestor -Path (Split-Path -Parent $output)

$installed = & $verifier -ReleaseRoot $release -ReceiptPath $receipt -PassThru
if ($installed.Passed -ne $true -or $installed.InstallationComplete -ne $true) {
    throw "Verified CadPlot release installation is incomplete."
}

$pilotRoot = Get-NormalizedPath -Path ([string]$installed.PilotRoot)
$outputBoundary = $pilotRoot + '\'
if (
    -not $output.StartsWith($outputBoundary, [System.StringComparison]::OrdinalIgnoreCase) -or
    $output.Equals([string]$installed.Config, [System.StringComparison]::OrdinalIgnoreCase)
) {
    throw "Licensed preflight output must be a new file under the verified pilot root."
}
$config = Get-NormalizedPath -Path ([string]$installed.Config)
$workspace = Get-NormalizedPath -Path (Join-Path $pilotRoot "pilot-work")
Assert-EnvironmentPath -Name "CADPLOT_CONFIG" -Expected $config
Assert-EnvironmentPath -Name "CADPLOT_WORKSPACE_ROOT" -Expected $workspace

$actualProgId = [Environment]::GetEnvironmentVariable("CADPLOT_AUTOCAD_PROGID", "Process")
if ($actualProgId -cne $expected.ProgId) {
    throw "CADPLOT_AUTOCAD_PROGID must be $($expected.ProgId) for AutoCAD $AutoCADRelease."
}
$actualPipeName = [Environment]::GetEnvironmentVariable("CADPLOT_PIPE_NAME", "Process")
if ($actualPipeName -cne $expected.PipeName) {
    throw "CADPLOT_PIPE_NAME must be $($expected.PipeName) for AutoCAD $AutoCADRelease."
}
$publishEnvironment = [Environment]::GetEnvironmentVariable("CADPLOT_ENABLE_PUBLISH", "Process")
if ($SessionMode -eq "ReadOnly") {
    if (-not [string]::IsNullOrWhiteSpace($publishEnvironment)) {
        throw "CADPLOT_ENABLE_PUBLISH must be unset during licensed read-only preflight."
    }
    if (-not [string]::IsNullOrWhiteSpace($ReadOnlyPreflightPath)) {
        throw "ReadOnlyPreflightPath is only valid for a publish session."
    }
}
else {
    if ($publishEnvironment -cne "1") {
        throw "CADPLOT_ENABLE_PUBLISH must be exactly 1 for licensed publish-session verification."
    }
    if ([string]::IsNullOrWhiteSpace($ReadOnlyPreflightPath)) {
        throw "A prior read-only preflight is required for publish-session verification."
    }
}

$doctor = Join-Path ([string]$installed.PythonPath) "bin\cadplot-doctor.cmd"
$adapterPath = Join-Path ([string]$installed.BundlePath) $expected.AdapterRelativePath
foreach ($path in @($config, $workspace, $doctor, $adapterPath, $receipt)) {
    if (-not (Test-Path -LiteralPath $path)) {
        throw "Licensed preflight required path is missing: $path"
    }
    Assert-NoRedirectedAncestor -Path $path
}
$configSha256 = (Get-FileHash -LiteralPath $config -Algorithm SHA256).Hash.ToLowerInvariant()
$receiptSha256 = (Get-FileHash -LiteralPath $receipt -Algorithm SHA256).Hash.ToLowerInvariant()
$adapterSha256 = (Get-FileHash -LiteralPath $adapterPath -Algorithm SHA256).Hash.ToLowerInvariant()

$readOnlyPreflight = $null
if ($SessionMode -eq "Publish") {
    $preflightPath = [System.IO.Path]::GetFullPath($ReadOnlyPreflightPath)
    if (
        -not $preflightPath.StartsWith($outputBoundary, [System.StringComparison]::OrdinalIgnoreCase) -or
        $preflightPath.Equals($output, [System.StringComparison]::OrdinalIgnoreCase) -or
        $preflightPath.Equals($config, [System.StringComparison]::OrdinalIgnoreCase) -or
        $preflightPath.Equals($receipt, [System.StringComparison]::OrdinalIgnoreCase)
    ) {
        throw "Prior read-only preflight must be a distinct file under the verified pilot root."
    }
    $readOnlyPreflight = Read-StableJsonFile `
        -Path $preflightPath `
        -Label "Prior read-only preflight"
    $prior = $readOnlyPreflight.Value
    $expectedPriorFields = @(
        "schema_version", "checked_utc", "exact_commit", "package_version",
        "session_mode", "autocad_release", "autocad_progid", "autocad_version",
        "runtime_series", "adapter", "pipe_name", "plugin_sha256",
        "install_receipt_sha256", "config_sha256", "config_changed_since_install",
        "inspection_identity_matched", "workspace_configured", "status_command_read_only",
        "read_only", "publish_enabled", "queue_authentication_active",
        "licensed_workstation_preflight_ready", "licensed_publish_session_ready",
        "autocad_launched", "live_publish_proven", "licensed_live_pilot_ready",
        "company_assets_copied", "next_gate"
    ) | Sort-Object
    $actualPriorFields = @($prior.PSObject.Properties.Name | Sort-Object)
    if (@(Compare-Object -ReferenceObject $expectedPriorFields -DifferenceObject $actualPriorFields).Count -ne 0) {
        throw "Prior read-only preflight fields do not match the documented schema."
    }
    if (
        $prior.schema_version -ne 1 -or
        $prior.exact_commit -cne $installed.ExactCommit -or
        $prior.package_version -cne $installed.PackageVersion -or
        $prior.session_mode -cne "readonly" -or
        $prior.autocad_release -cne $AutoCADRelease -or
        $prior.autocad_progid -cne $expected.ProgId -or
        -not ([string]$prior.autocad_version).StartsWith(
            $expected.VersionPrefix,
            [System.StringComparison]::Ordinal
        ) -or
        $prior.runtime_series -cne $expected.RuntimeSeries -or
        $prior.adapter -cne $expected.Adapter -or
        $prior.pipe_name -cne $expected.PipeName -or
        $prior.plugin_sha256 -cne $adapterSha256 -or
        $prior.install_receipt_sha256 -cne $receiptSha256 -or
        $prior.config_sha256 -cne $configSha256 -or
        $prior.config_changed_since_install -isnot [bool] -or
        $prior.inspection_identity_matched -ne $true -or
        $prior.workspace_configured -ne $true -or
        $prior.status_command_read_only -ne $true -or
        $prior.read_only -ne $true -or
        $prior.publish_enabled -ne $false -or
        $prior.queue_authentication_active -ne $false -or
        $prior.licensed_workstation_preflight_ready -ne $true -or
        $prior.licensed_publish_session_ready -ne $false -or
        $prior.autocad_launched -ne $false -or
        $prior.live_publish_proven -ne $false -or
        $prior.licensed_live_pilot_ready -ne $false -or
        $prior.company_assets_copied -ne $false -or
        $prior.next_gate -cne "Restart with publish opt-in and verify the bound publish session"
    ) {
        throw "Prior read-only preflight is not bound to this verified installation and release."
    }
}

$doctorArguments = @("--config", $config, "--mode", "full", "--timeout-ms", $TimeoutMs)
if ($SessionMode -eq "Publish") { $doctorArguments += "--expect-publish-enabled" }
$doctorOutput = & $doctor @doctorArguments 2>&1
$doctorExitCode = $LASTEXITCODE
$doctorText = ($doctorOutput | Out-String).Trim()
try { $doctorReport = $doctorText | ConvertFrom-Json }
catch { throw "cadplot-doctor did not return valid JSON: $doctorText" }
if ($doctorExitCode -ne 0 -or $doctorReport.ready -ne $true -or $doctorReport.mode -cne "full") {
    throw "cadplot-doctor licensed full preflight failed: $doctorText"
}

$doctorConfig = Get-NormalizedPath -Path ([string]$doctorReport.config)
$doctorWorkspace = Get-NormalizedPath -Path ([string]$doctorReport.workspace_root)
$status = $doctorReport.plugin.status
if (
    -not $doctorConfig.Equals($config, [System.StringComparison]::OrdinalIgnoreCase) -or
    -not $doctorWorkspace.Equals($workspace, [System.StringComparison]::OrdinalIgnoreCase) -or
    $doctorReport.autocad.checked -ne $true -or
    $doctorReport.autocad.available -ne $true -or
    $doctorReport.autocad.progid -cne $expected.ProgId -or
    -not ([string]$doctorReport.autocad.version).StartsWith(
        $expected.VersionPrefix,
        [System.StringComparison]::Ordinal
    ) -or
    $doctorReport.plugin.checked -ne $true -or
    $doctorReport.plugin.connected -ne $true -or
    $doctorReport.plugin.inspection_identity_matched -ne $true -or
    $status.ok -ne $true -or
    $status.readOnly -ne $true -or
    $status.workspaceConfigured -ne $true -or
    $status.runtimeSupported -ne $true -or
    $status.runtimeSeries -cne $expected.RuntimeSeries -or
    $status.adapter -cne $expected.Adapter -or
    $status.buildCommit -cne $installed.ExactCommit -or
    $status.pluginSha256 -cne $adapterSha256
) {
    throw "Licensed AutoCAD, COM, plug-in, release, or session identity evidence is inconsistent."
}
if (
    $SessionMode -eq "ReadOnly" -and (
        $doctorReport.expected_publish_enabled -ne $false -or
        $status.publishEnabled -ne $false -or
        -not [string]::IsNullOrWhiteSpace([string]$status.queueAuthentication) -or
        -not [string]::IsNullOrWhiteSpace([string]$status.publishInitializationError)
    )
) {
    throw "Licensed AutoCAD read-only session evidence is inconsistent."
}
if (
    $SessionMode -eq "Publish" -and (
        $doctorReport.expected_publish_enabled -ne $true -or
        $status.publishEnabled -ne $true -or
        $status.queueAuthentication -cne "windows-dpapi-current-user+hmac-sha256-v1" -or
        -not [string]::IsNullOrWhiteSpace([string]$status.publishInitializationError)
    )
) {
    throw "Licensed AutoCAD publish-session or queue-authentication evidence is inconsistent."
}

$installedAfter = & $verifier -ReleaseRoot $release -ReceiptPath $receipt -PassThru
$currentConfigSha256 = (Get-FileHash -LiteralPath $config -Algorithm SHA256).Hash.ToLowerInvariant()
$currentReceiptSha256 = (Get-FileHash -LiteralPath $receipt -Algorithm SHA256).Hash.ToLowerInvariant()
$currentAdapterSha256 = (Get-FileHash -LiteralPath $adapterPath -Algorithm SHA256).Hash.ToLowerInvariant()
if (
    $installedAfter.Passed -ne $true -or
    $installedAfter.InstallationComplete -ne $true -or
    $installedAfter.ExactCommit -cne $installed.ExactCommit -or
    $installedAfter.PackageVersion -cne $installed.PackageVersion -or
    $installedAfter.ReceiptSha256 -cne $installed.ReceiptSha256 -or
    $currentConfigSha256 -cne $configSha256 -or
    $currentReceiptSha256 -cne $receiptSha256 -or
    $currentAdapterSha256 -cne $adapterSha256
) {
    throw "Licensed installation evidence changed during preflight."
}
if ($null -ne $readOnlyPreflight) {
    Assert-JsonSnapshotUnchanged `
        -Snapshot $readOnlyPreflight `
        -Label "Prior read-only preflight"
}

$evidence = [ordered]@{
    schema_version = 1
    checked_utc = [DateTime]::UtcNow.ToString("o")
    exact_commit = [string]$installed.ExactCommit
    package_version = [string]$installed.PackageVersion
    session_mode = $SessionMode.ToLowerInvariant()
    autocad_release = $AutoCADRelease
    autocad_progid = $expected.ProgId
    autocad_version = [string]$doctorReport.autocad.version
    runtime_series = [string]$status.runtimeSeries
    adapter = [string]$status.adapter
    pipe_name = $expected.PipeName
    plugin_sha256 = $adapterSha256
    install_receipt_sha256 = $receiptSha256
    config_sha256 = $configSha256
    config_changed_since_install = [bool]$installed.ConfigChangedSinceInstall
    inspection_identity_matched = $true
    workspace_configured = $true
    status_command_read_only = $true
    read_only = $SessionMode -eq "ReadOnly"
    publish_enabled = $SessionMode -eq "Publish"
    queue_authentication_active = $SessionMode -eq "Publish"
    autocad_launched = $false
    live_publish_proven = $false
    licensed_live_pilot_ready = $false
    company_assets_copied = $false
}
if ($SessionMode -eq "ReadOnly") {
    $evidence["licensed_workstation_preflight_ready"] = $true
    $evidence["licensed_publish_session_ready"] = $false
    $evidence["next_gate"] = "Restart with publish opt-in and verify the bound publish session"
}
else {
    $evidence["read_only_preflight_verified"] = $true
    $evidence["read_only_preflight_sha256"] = $readOnlyPreflight.Sha256
    $evidence["licensed_workstation_preflight_ready"] = $false
    $evidence["licensed_publish_session_ready"] = $true
    $evidence["queue_authentication"] = "windows-dpapi-current-user+hmac-sha256-v1"
    $evidence["next_gate"] = "Authorized one-sheet staged-copy queue and visual acceptance"
}
Write-NewUtf8Json -Path $output -Value $evidence
$outputSha256 = (Get-FileHash -LiteralPath $output -Algorithm SHA256).Hash.ToLowerInvariant()
$result = [pscustomobject]@{
    Passed = $true
    AutoCADRelease = $AutoCADRelease
    SessionMode = $SessionMode
    RuntimeSeries = $expected.RuntimeSeries
    ExactCommit = $installed.ExactCommit
    PluginSha256 = $adapterSha256
    Output = $output
    OutputSha256 = $outputSha256
    ReadOnly = $SessionMode -eq "ReadOnly"
    PublishEnabled = $SessionMode -eq "Publish"
    LivePublishProven = $false
    LicensedLivePilotReady = $false
}
if ($PassThru) { $result }
else { $result | ConvertTo-Json -Depth 4 }
