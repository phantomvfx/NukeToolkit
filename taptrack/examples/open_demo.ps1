<#
 Open the TAPTrack demo script in Nuke with TAPTrack on the plugin path for this session only
 (your init.py is not changed).   powershell -ExecutionPolicy Bypass -File examples\open_demo.ps1 [-Script monitors_demo.nk] [-Nuke <path to Nuke exe>]
#>
param([string]$Nuke = "", [string]$Script = "taptrack_demo.nk")
$root = Split-Path -Parent $PSScriptRoot
if (-not $Nuke) { $Nuke = $env:TAPTRACK_NUKE }
if (-not $Nuke) {
    $Nuke = (Get-ChildItem "L:\Apps\Nuke16*\Nuke16*.exe", "C:\Program Files\Nuke16*\Nuke16*.exe" -ErrorAction SilentlyContinue |
             Where-Object { $_.Name -notmatch "python|crashhandler" } | Select-Object -First 1 -ExpandProperty FullName)
}
if (-not $Nuke) { Write-Host "Nuke 16 was not found. Pass its path:  open_demo.ps1 -Nuke 'C:\Program Files\Nuke16.1v4\Nuke16.1.exe'"; exit 1 }
$env:NUKE_PATH = (Join-Path $root "nuke") + $(if ($env:NUKE_PATH) { ";" + $env:NUKE_PATH } else { "" })
Start-Process -FilePath $Nuke -ArgumentList ('"' + (Join-Path $PSScriptRoot $Script) + '"')
