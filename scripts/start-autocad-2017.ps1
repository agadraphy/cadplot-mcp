[CmdletBinding()]
param(
    [string]$ConfigPath = $env:CADPLOT_CONFIG,
    [string]$AutoCAD2017Directory = "",
    [string]$Uv = "uv"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($ConfigPath)) { $ConfigPath = Join-Path $repoRoot "config.yaml" }
$directory = & (Join-Path $PSScriptRoot "find-autocad-2017.ps1") -ApiDirectory $AutoCAD2017Directory
$executable = Join-Path $directory "acad.exe"
if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
    throw "The selected directory is an SDK, not an AutoCAD installation containing acad.exe."
}
if (@(Get-Process -Name "acad" -ErrorAction SilentlyContinue).Count -gt 0) {
    throw "Save and close existing AutoCAD sessions first; this script never stops a process."
}
$reportText = & $Uv run --project $repoRoot --frozen cadplot-doctor --config $ConfigPath --mode config
if ($LASTEXITCODE -ne 0) { throw "CadPlot config validation failed; AutoCAD was not started." }
$report = ($reportText -join [Environment]::NewLine) | ConvertFrom-Json
if ($report.ready -ne $true) { throw "CadPlot config is not ready; AutoCAD was not started." }

# Process-scoped only. No user/machine environment, trust settings or drawing is changed.
$values = @{
    CADPLOT_CONFIG = [System.IO.Path]::GetFullPath($ConfigPath)
    CADPLOT_WORKSPACE_ROOT = [string]$report.workspace_root
    CADPLOT_ENABLE_PUBLISH = "1"
    CADPLOT_AUTOCAD_PROGID = "AutoCAD.Application.21.0"
    CADPLOT_PIPE_NAME = "cadplot-mcp-2017"
}
$previous = @{}
foreach ($key in $values.Keys) {
    $previous[$key] = [Environment]::GetEnvironmentVariable($key, "Process")
}
try {
    foreach ($key in $values.Keys) { [Environment]::SetEnvironmentVariable($key, $values[$key], "Process") }
    # The user explicitly runs this launcher to open the interactive CAD application.
    & $executable
}
finally {
    foreach ($key in $values.Keys) { [Environment]::SetEnvironmentVariable($key, $previous[$key], "Process") }
}
