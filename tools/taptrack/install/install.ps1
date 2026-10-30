<#
 TAPTrack installer (Windows): tracker environment + pinned tapnet + model weights + a check.
   powershell -ExecutionPolicy Bypass -File install\install.ps1 [-Cuda cu126|cpu] [-Venv <dir>] [-AllModels] [-SkipWeights] [-AddToNuke]
 Safe to run again: it repairs what is missing and skips what is already there.
#>
param(
    [string]$Cuda = "cu126",
    [string]$Venv = "",
    [switch]$AllModels,
    [switch]$SkipWeights,
    [switch]$AddToNuke
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
if (-not $Venv) { $Venv = Join-Path $root ".venv" }

Write-Host "== 1/4  Tracker environment"
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "setup_venv.ps1") -Cuda $Cuda -Venv $Venv
if ($LASTEXITCODE -ne 0) { exit 1 }
$vpy = Join-Path $Venv "Scripts\python.exe"
$env:TAPTRACK_PYTHON = $vpy

if (-not $SkipWeights) {
    Write-Host "== 2/4  Model weights (about 1 GB, 3 GB with -AllModels)"
    $a = @((Join-Path $PSScriptRoot "download_weights.py"))
    if ($AllModels) { $a += "--all" }
    & $vpy @a
    if ($LASTEXITCODE -ne 0) { Write-Host "The weights download failed. Check the internet connection and run install\download_weights.ps1 again." -ForegroundColor Red; exit 1 }
} else { Write-Host "== 2/4  Model weights skipped" }

Write-Host "== 3/4  Nuke"
if ($AddToNuke) { & $vpy (Join-Path $PSScriptRoot "add_to_nuke.py") }
else {
    Write-Host "To make TAPTrack appear in Nuke, run:  install\add_to_nuke.ps1"
    Write-Host "or add this line to your Nuke init.py:   nuke.pluginAddPath(r'$($root)\nuke')"
}

Write-Host "== 4/4  Check"
& $vpy (Join-Path $PSScriptRoot "check_install.py") --selftest
exit $LASTEXITCODE
