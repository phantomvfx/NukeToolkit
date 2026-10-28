<# Add (or -Remove) TAPTrack on Nuke's plugin path via one marked block in <nuke dir>\init.py. -NukeDir to choose the folder, -DryRun to preview. #>
param([string]$NukeDir = "", [switch]$Remove, [switch]$DryRun)
$a = @((Join-Path $PSScriptRoot "add_to_nuke.py"))
if ($NukeDir) { $a += @("--nuke-dir", $NukeDir) }
if ($Remove) { $a += "--remove" }
if ($DryRun) { $a += "--dry-run" }
python @a
exit $LASTEXITCODE
