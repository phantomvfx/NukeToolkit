<# Download the model weights into checkpoints\ (about 1 GB; -All for every model, about 3 GB). #>
param([switch]$All)
$root = Split-Path -Parent $PSScriptRoot
$py = if ($env:TAPTRACK_PYTHON) { $env:TAPTRACK_PYTHON } elseif (Test-Path (Join-Path $root ".venv\Scripts\python.exe")) { Join-Path $root ".venv\Scripts\python.exe" } else { "python" }
$a = @((Join-Path $PSScriptRoot "download_weights.py")); if ($All) { $a += "--all" }
& $py @a
exit $LASTEXITCODE
