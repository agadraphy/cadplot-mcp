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
    "ExistingReceiptCannotBeQueuedAsFreshWork"
)
$filter = @($expectedTests | ForEach-Object {
    "FullyQualifiedName=CadPlotMcp.Core.Tests.PublishJobTests.$_"
}) -join "|"
$resultsRoot = Join-Path `
    ([System.IO.Path]::GetTempPath()) `
    ("cadplot-queue-probe-{0}" -f [Guid]::NewGuid().ToString("N"))
[System.IO.Directory]::CreateDirectory($resultsRoot) | Out-Null

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

    $result = [pscustomobject][ordered]@{
        passed = $true
        exact_test_count = $expectedTests.Count
        pending_intent_recovered = $true
        exact_request_identity_preserved = $true
        interrupted_job_not_replayed = $true
        terminal_receipt_status_recovered = $true
        tampered_intent_blocked = $true
        completed_job_requeue_blocked = $true
        autocad_launched = $false
        live_publish_proven = $false
        evidence_scope = "production-core-with-synthetic-files"
    }
    if ($PassThru) { $result }
    else { $result | ConvertTo-Json -Depth 3 }
}
finally {
    if (Test-Path -LiteralPath $resultsRoot) {
        [System.IO.Directory]::Delete($resultsRoot, $true)
    }
}
