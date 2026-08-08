[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [Parameter(Mandatory = $true)]
    [string]$DestinationRoot
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$template = Join-Path $repoRoot "examples\config.inventory.example.yaml"
$target = [System.IO.Path]::GetFullPath($DestinationRoot)

if (-not (Test-Path -LiteralPath $template -PathType Leaf)) {
    throw "Inventory configuration template is missing: $template"
}
if (Test-Path -LiteralPath $target) {
    throw "Destination already exists; setup never overwrites: $target"
}

$existingAncestor = Split-Path -Parent $target
while (-not (Test-Path -LiteralPath $existingAncestor)) {
    $next = Split-Path -Parent $existingAncestor
    if ($next -eq $existingAncestor -or [string]::IsNullOrWhiteSpace($next)) {
        throw "No existing parent directory was found for: $target"
    }
    $existingAncestor = $next
}
$currentAncestor = [System.IO.Path]::GetFullPath($existingAncestor)
while (-not [string]::IsNullOrWhiteSpace($currentAncestor)) {
    $ancestorItem = Get-Item -LiteralPath $currentAncestor -Force
    if (-not $ancestorItem.PSIsContainer) {
        throw "Pilot destination parent is not a directory: $currentAncestor"
    }
    if (($ancestorItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Pilot destination must not pass through a symlink or junction: $currentAncestor"
    }
    $nextAncestor = Split-Path -Parent $currentAncestor
    if ($nextAncestor -eq $currentAncestor -or [string]::IsNullOrWhiteSpace($nextAncestor)) {
        break
    }
    $currentAncestor = $nextAncestor
}

if ($PSCmdlet.ShouldProcess($target, "Create empty CadPlot pilot workspace")) {
    $null = New-Item -ItemType Directory -Path $target
    $inputRoot = Join-Path $target "pilot-input"
    $workspaceRoot = Join-Path $target "pilot-work"
    $null = New-Item -ItemType Directory -Path $inputRoot
    $null = New-Item -ItemType Directory -Path $workspaceRoot
    $config = Join-Path $target "config.yaml"
    Copy-Item -LiteralPath $template -Destination $config

    [ordered]@{
        created = $true
        root = $target
        config = $config
        authorized_input = $inputRoot
        isolated_workspace = $workspaceRoot
        company_assets_copied = $false
        publish_enabled = $false
        next_command = "`$env:CADPLOT_CONFIG = '$config'; uv run cadplot-doctor --mode config"
    } | ConvertTo-Json
}
