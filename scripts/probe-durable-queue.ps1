[CmdletBinding()]
param(
    [string]$DotNet = "dotnet",
    [string]$Configuration = "Release",
    [switch]$PassThru
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$project = Join-Path $repoRoot "src\dotnet\CadPlotMcp.Core.Tests\CadPlotMcp.Core.Tests.csproj"
$expectedTests = @(
    "PendingApprovedIntentRecoversAfterRestartWithExactIdentity",
    "InterruptedRunningIntentIsNeverAutomaticallyReplayed",
    "TerminalReceiptRestoresStatusAfterRestart",
    "TamperedPendingIntentDisablesRecovery",
    "ExistingReceiptCannotBeQueuedAsFreshWork",
    "ForgedUnsignedPendingIntentCannotAuthorizeRestart",
    "ForeignProtectedKeyCannotAuthorizeRestart",
    "TamperedStartedIntentDisablesRecovery",
    "AuthenticationKeyIsDpapiProtectedOutsideWorkspace",
    "AuthenticationKeyInsideWorkspaceIsRejected",
    "CorruptAuthenticationKeyDisablesInitialization"
)
$filter = @($expectedTests | ForEach-Object {
    "FullyQualifiedName=CadPlotMcp.Core.Tests.PublishJobTests.$_"
}) -join "|"
$resultsRoot = Join-Path `
    ([System.IO.Path]::GetTempPath()) `
    ("cadplot-queue-probe-{0}" -f [Guid]::NewGuid().ToString("N"))
[System.IO.Directory]::CreateDirectory($resultsRoot) | Out-Null
$net45Root = Join-Path `
    ([System.IO.Path]::GetTempPath()) `
    ("cadplot-queue-net45-probe-{0}" -f [Guid]::NewGuid().ToString("N"))

try {
    $testOutput = @(& $DotNet test $project `
        --configuration $Configuration `
        --no-build `
        --nologo `
        --filter $filter `
        --logger "trx;LogFileName=durable-queue.trx" `
        --results-directory $resultsRoot 2>&1)
    if ($LASTEXITCODE -ne 0) { throw "Durable queue recovery probe tests failed." }
    foreach ($line in $testOutput) { Write-Host $line }

    $trxFiles = @(Get-ChildItem -LiteralPath $resultsRoot -Filter "*.trx" -File)
    if ($trxFiles.Count -ne 1) { throw "Durable queue probe did not produce one TRX report." }
    [xml]$trx = Get-Content -LiteralPath $trxFiles[0].FullName -Raw -Encoding UTF8
    $namespace = New-Object System.Xml.XmlNamespaceManager($trx.NameTable)
    $namespace.AddNamespace("t", "http://microsoft.com/schemas/VisualStudio/TeamTest/2010")
    $results = @($trx.SelectNodes("//t:UnitTestResult", $namespace))
    if ($results.Count -ne $expectedTests.Count) {
        throw "Durable queue probe returned an unexpected test count."
    }
    $actualNames = @($results | ForEach-Object {
        $parts = ([string]$_.testName).Split('.')
        $parts[$parts.Length - 1]
    } | Sort-Object)
    $expectedNames = @($expectedTests | Sort-Object)
    if (($actualNames -join "`n") -cne ($expectedNames -join "`n")) {
        throw "Durable queue probe returned an unexpected test identity."
    }
    if (@($results | Where-Object { [string]$_.outcome -cne "Passed" }).Count -ne 0) {
        throw "Durable queue probe contains a non-passing result."
    }

    $net45Core = Join-Path `
        $repoRoot `
        "src\dotnet\CadPlotMcp.Core\bin\$Configuration\net45\CadPlotMcp.Core.dll"
    if (-not (Test-Path -LiteralPath $net45Core -PathType Leaf)) {
        throw "The net45 CadPlot core must be built before the durable queue probe."
    }
    [System.IO.Directory]::CreateDirectory($net45Root) | Out-Null
    $net45Workspace = Join-Path $net45Root "workspace"
    $net45State = Join-Path $net45Root "state"
    [System.IO.Directory]::CreateDirectory($net45Workspace) | Out-Null
    [System.IO.Directory]::CreateDirectory($net45State) | Out-Null
    if (-not ("CadPlotMcp.Core.PublishJobQueue" -as [type])) {
        Add-Type -LiteralPath $net45Core
    }
    $net45Key = Join-Path $net45State "queue-auth-key-v1.bin"
    $net45Queue = [CadPlotMcp.Core.PublishJobQueue]::new(
        $net45Workspace,
        5,
        $net45Key
    )
    $net45Telemetry = $net45Queue.GetTelemetry()
    $net45Assembly = [CadPlotMcp.Core.PublishJobQueue].Assembly
    if (
        -not (Test-Path -LiteralPath $net45Key -PathType Leaf) -or
        $net45Telemetry.Authentication -cne "windows-dpapi-current-user+hmac-sha256-v1" -or
        $net45Assembly.ImageRuntimeVersion -cne "v4.0.30319"
    ) {
        throw "The net45 DPAPI/HMAC queue runtime probe failed."
    }

    $result = [pscustomobject][ordered]@{
        passed = $true
        exact_test_count = $expectedTests.Count
        pending_intent_recovered = $true
        exact_request_identity_preserved = $true
        interrupted_job_not_replayed = $true
        terminal_receipt_status_recovered = $true
        tampered_intent_blocked = $true
        completed_job_requeue_blocked = $true
        authentication_scheme = "windows-dpapi-current-user+hmac-sha256-v1"
        signed_intent_required = $true
        foreign_key_intent_blocked = $true
        started_marker_authentication_required = $true
        protected_key_outside_workspace = $true
        workspace_key_rejected = $true
        corrupt_key_blocked = $true
        net45_dpapi_runtime_proven = $true
        net45_core_image_runtime = $net45Assembly.ImageRuntimeVersion
        autocad_launched = $false
        live_publish_proven = $false
        evidence_scope = "production-core-net45+net8-with-synthetic-files"
    }
    if ($PassThru) { $result }
    else { $result | ConvertTo-Json -Depth 3 }
}
finally {
    if (Test-Path -LiteralPath $resultsRoot) {
        [System.IO.Directory]::Delete($resultsRoot, $true)
    }
    if (Test-Path -LiteralPath $net45Root) {
        [System.IO.Directory]::Delete($net45Root, $true)
    }
}
