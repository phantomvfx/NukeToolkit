<#
 Create the TAPTrack worker environment (torch lives here, never inside Nuke) and fetch the pinned tapnet source.
 Usage: powershell -ExecutionPolicy Bypass -File install\setup_venv.ps1 [-Cuda cu126|cpu] [-Venv <dir>]
 Normally you run install\install.ps1, which calls this and then downloads the weights.
#>
param(
    [string]$Cuda = "cu126",
    [string]$Venv = ""
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
if (-not $Venv) { $Venv = Join-Path $root ".venv" }
if (-not $env:PIP_CACHE_DIR) { $env:PIP_CACHE_DIR = Join-Path $root ".pip-cache" }

function Fail([string]$msg) { Write-Host ""; Write-Host "TAPTrack setup stopped: $msg" -ForegroundColor Red; exit 1 }
function Check([string]$what) { if ($LASTEXITCODE -ne 0) { Fail "$what failed (see the messages above)." } }

# --- a usable Python (3.10 - 3.12)
$pyExe = $null
foreach ($v in "3.11", "3.12", "3.10") {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        & py "-$v" -c "import sys" 2>$null
        if ($LASTEXITCODE -eq 0) { $pyExe = @("py", "-$v"); break }
    }
}
if (-not $pyExe -and (Get-Command python -ErrorAction SilentlyContinue)) {
    $ver = (& python -c "import sys;print('%d.%d' % sys.version_info[:2])")
    if ($ver -in "3.10", "3.11", "3.12") { $pyExe = @("python") }
}
if (-not $pyExe) { Fail "Python 3.10, 3.11 or 3.12 was not found. Install Python 3.11 from python.org (tick 'Add to PATH'), then run this again." }

# --- GPU
if ($Cuda -ne "cpu" -and -not (Get-Command nvidia-smi -ErrorAction SilentlyContinue)) {
    Write-Host "No NVIDIA driver found. Installing the CPU version (tracking will be slow). Use -Cuda cu126 once a driver is installed." -ForegroundColor Yellow
    $Cuda = "cpu"
}

Write-Host "Creating the tracker environment in $Venv ..."
if ($pyExe.Count -gt 1) { & $pyExe[0] $pyExe[1] -m venv $Venv } else { & $pyExe[0] -m venv $Venv }
Check "Creating the Python environment"
$vpy = Join-Path $Venv "Scripts\python.exe"

& $vpy -m pip install --upgrade pip
Check "Updating pip"
Write-Host "Installing PyTorch ($Cuda) - this downloads about 2.5 GB the first time ..."
& $vpy -m pip install torch torchvision --index-url "https://download.pytorch.org/whl/$Cuda"
Check "Installing PyTorch"
& $vpy -m pip install -r (Join-Path $PSScriptRoot "requirements-worker.txt")
Check "Installing the other Python packages"

# --- pinned tapnet source (code only; weights come from download_weights)
$vendor = Join-Path $root "vendor\tapnet"
$commit = (Get-Content (Join-Path $PSScriptRoot "tapnet_commit.txt")).Trim()
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Fail "Git was not found. Install Git for Windows (git-scm.com), then run this again." }
if (-not (Test-Path $vendor)) {
    git clone https://github.com/google-deepmind/tapnet.git $vendor
    Check "Downloading the tapnet source"
}
git -C $vendor fetch --quiet origin
git -C $vendor checkout --quiet $commit
Check "Selecting the tested tapnet version"
Write-Host "tapnet pinned at $commit"
Write-Host "Environment ready: $vpy"
