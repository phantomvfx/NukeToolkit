<# Check the installation and say what to fix. -SelfTest also runs a tiny real tracking job (about 15 s). #>
param([switch]$SelfTest)
$root = Split-Path -Parent $PSScriptRoot
$py = if ($env:TAPTRACK_PYTHON) { $env:TAPTRACK_PYTHON } elseif (Test-Path (Join-Path $root ".venv\Scripts\python.exe")) { Join-Path $root ".venv\Scripts\python.exe" } else { "python" }
$a = @((Join-Path $PSScriptRoot "check_install.py")); if ($SelfTest) { $a += "--selftest" }
& $py @a
exit $LASTEXITCODE
