[CmdletBinding()]
param(
    [string]$AutoCAD2017SdkDir = "",
    [string]$DotNet = "dotnet",
    [string]$Uv = "uv",
    [string]$OutputRoot = ""
)

$ErrorActionPreference = "Stop"
if (@(Get-Process -Name "acad" -ErrorAction SilentlyContinue).Count -gt 0) {
    throw "Save your drawings and close AutoCAD before installing. No process is stopped automatically."
}
$arguments = @{
    AutoCAD2017SdkDir = $AutoCAD2017SdkDir
    DotNet = $DotNet
    Uv = $Uv
    OutputRoot = $OutputRoot
    PassThru = $true
}
$raw = @(& (Join-Path $PSScriptRoot "build-bundle-2017.ps1") @arguments)
$records = @($raw | Where-Object { $_ -is [pscustomobject] -and $_.passed -eq $true })
if ($records.Count -ne 1) { throw "Matching-SDK builder did not return its release record." }
$build = $records[0]
if ($build.passed -ne $true -or $build.matching_sdk_bundle_built -ne $true) {
    throw "The AutoCAD 2017 matching-SDK bundle was not built."
}
& (Join-Path $PSScriptRoot "install-bundle.ps1") -SourceBundle $build.bundle
Write-Output "AutoCAD 2017 bridge installed. Start a publish-enabled session with scripts/start-autocad-2017.ps1 using the same config as your local MCP."
