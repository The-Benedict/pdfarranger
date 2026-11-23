<#
.SYNOPSIS
    One-time Windows development setup for this PDF Arranger fork.

.DESCRIPTION
    1. Installs MSYS2 (via winget) if it is not already at -MsysRoot.
    2. Updates MSYS2 and installs GTK3, Poppler, pikepdf, img2pdf, etc. into
       the UCRT64 environment with pacman (these have C extensions and must
       come from MSYS2, not PyPI).
    3. Creates .venv in the repo root, inheriting MSYS2's site-packages, and
       drops a .pth hook into it so every run (PyCharm, tests, debugger,
       console) can find the GTK DLLs.
    4. Installs GTK type stubs for PyCharm autocompletion.
    5. Builds translations (setup.py build), smoke-tests the imports and runs
       the non-GUI unit tests.

    Safe to re-run: every step skips work that is already done.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File dev\windows\setup.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File dev\windows\setup.ps1 -SkipUpdate -SkipTests
#>
[CmdletBinding()]
param(
    [string]$MsysRoot = "C:\msys64",
    [switch]$SkipUpdate,
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"

$RepoRoot    = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$MingwBin    = Join-Path $MsysRoot "ucrt64\bin"
$MingwPython = Join-Path $MingwBin "python.exe"
$Bash        = Join-Path $MsysRoot "usr\bin\bash.exe"
$Venv        = Join-Path $RepoRoot ".venv"

# Everything PDF Arranger needs at runtime, plus build/test tooling, from the
# UCRT64 environment. (Upstream's Win32.md still says MINGW64, which MSYS2
# deprecated in March 2026 and is dropping packages from.)
$Packages = @(
    "gtk3"
    "libhandy"
    "poppler"
    "qpdf"
    "gettext"
    "gnutls"
    "python"
    "python-pip"
    "python-setuptools"
    "python-gobject"
    "python-cairo"
    "python-lxml"
    "python-pillow"
    "python-dateutil"
    "python-packaging"
    "python-pikepdf"
    "img2pdf"
    "python-pytest"
    "python-coverage"
    "python-cx-freeze"   # only needed to build the .msi/.zip installer
) | ForEach-Object { "mingw-w64-ucrt-x86_64-$_" }

function Write-Step([string]$Message) {
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

# Run a native command without PowerShell treating stderr output as an error,
# then fail on a non-zero exit code.
function Invoke-Native([scriptblock]$Command, [string]$What, [switch]$AllowFailure) {
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $Command | Out-Host
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previous
    }
    if ($code -ne 0 -and -not $AllowFailure) {
        throw "$What failed (exit code $code)"
    }
    return $code
}

function Invoke-Msys([string]$MsysCommand, [string]$What, [switch]$AllowFailure) {
    $env:MSYSTEM = "UCRT64"
    $env:CHERE_INVOKING = "1"
    $block = { & $Bash -lc $MsysCommand }.GetNewClosure()
    $null = Invoke-Native $block $What -AllowFailure:$AllowFailure
}

# --------------------------------------------------------------------------
Write-Step "Repository: $RepoRoot"
if (-not (Test-Path (Join-Path $RepoRoot "pdfarranger\pdfarranger.py"))) {
    throw "This script must live in <repo>\dev\windows\. Could not find pdfarranger\pdfarranger.py under $RepoRoot"
}
if ($RepoRoot -match "OneDrive") {
    Write-Warning "The repo is inside OneDrive. Sync can lock files during builds; C:\Development\ is a better home."
}

# --------------------------------------------------------------------------
Write-Step "MSYS2"
if (-not (Test-Path $Bash)) {
    if ($MsysRoot -ne "C:\msys64") {
        throw "MSYS2 not found at $MsysRoot. Install it there first, or omit -MsysRoot to let this script install it to C:\msys64."
    }
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw "MSYS2 is not installed and winget is unavailable. Install MSYS2 from https://www.msys2.org then re-run."
    }
    Write-Host "Installing MSYS2 with winget..."
    $null = Invoke-Native { winget install --id MSYS2.MSYS2 -e --accept-source-agreements --accept-package-agreements } "winget install MSYS2"
    if (-not (Test-Path $Bash)) {
        throw "winget finished but $Bash does not exist. Check where MSYS2 was installed and pass -MsysRoot."
    }
} else {
    Write-Host "Found MSYS2 at $MsysRoot"
}

if (-not $SkipUpdate) {
    Write-Step "Updating MSYS2 (pacman -Syu, run twice: the first pass may only update the core)"
    Invoke-Msys "pacman -Syu --noconfirm" "pacman -Syu (pass 1)" -AllowFailure
    Invoke-Msys "pacman -Syu --noconfirm" "pacman -Syu (pass 2)"
}

Write-Step "Installing UCRT64 packages"
Invoke-Msys ("pacman -S --needed --noconfirm " + ($Packages -join " ")) "pacman -S"

if (-not (Test-Path $MingwPython)) {
    throw "Expected $MingwPython after installing packages, but it is missing."
}

# --------------------------------------------------------------------------
Write-Step "Virtual environment (.venv, with MSYS2 site-packages visible)"
function Find-VenvPython {
    foreach ($candidate in @("bin\python.exe", "Scripts\python.exe")) {
        $p = Join-Path $Venv $candidate
        if (Test-Path $p) { return $p }
    }
    return $null
}

$VenvPython = Find-VenvPython
if (-not $VenvPython) {
    $null = Invoke-Native { & $MingwPython -m venv --system-site-packages $Venv } "python -m venv"
    $VenvPython = Find-VenvPython
    if (-not $VenvPython) { throw "venv was created but no python.exe was found inside $Venv" }
} else {
    Write-Host "Reusing $VenvPython"
}

# The .pth hook: Python runs 'import' lines in .pth files at startup, so this
# puts MSYS2's DLL folder on PATH and on the DLL search list for every process
# started with this interpreter (including multiprocessing children).
$SitePackages = (& $VenvPython -c "import sysconfig; print(sysconfig.get_paths()['purelib'])").Trim()
$PthFile = Join-Path $SitePackages "msys2_gtk_dlls.pth"
$PthLine = "import os; _msys2_bin = r'$MingwBin'; os.environ['PATH'] = _msys2_bin + os.pathsep + os.environ.get('PATH', ''); os.add_dll_directory(_msys2_bin) if hasattr(os, 'add_dll_directory') and os.path.isdir(_msys2_bin) else None"
# Write without a BOM: a BOM would stop Python from recognising the import line.
[System.IO.File]::WriteAllText($PthFile, $PthLine + "`n", (New-Object System.Text.UTF8Encoding($false)))
Write-Host "Wrote $PthFile"

Write-Step "Dev-only Python packages (GTK type stubs for PyCharm)"
# NOTE: MSYS2's Python cannot use PyPI's Windows wheels. Only pure-Python
# packages belong here; anything with C code must come from pacman.
$code = Invoke-Native { & $VenvPython -m pip install --disable-pip-version-check --no-cache-dir "pygobject-stubs" "--config-settings=config=Gtk3,Gdk3" } "pip install pygobject-stubs" -AllowFailure
if ($code -ne 0) {
    Write-Warning "pygobject-stubs failed to install. The app still works; PyCharm autocompletion for Gtk will just be weaker."
}

# --------------------------------------------------------------------------
Write-Step "Smoke test: GTK, Poppler, pikepdf, img2pdf"
$smoke = @"
import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Poppler', '0.18')
from gi.repository import Gtk, Poppler
import pikepdf, img2pdf
print('GTK', '.'.join(map(str, (Gtk.get_major_version(), Gtk.get_minor_version(), Gtk.get_micro_version()))),
      '| Poppler', Poppler.get_version(), '| pikepdf', pikepdf.__version__, '| img2pdf', img2pdf.__version__)
"@
$SmokeFile = Join-Path ([System.IO.Path]::GetTempPath()) "pdfarranger_smoke.py"
[System.IO.File]::WriteAllText($SmokeFile, $smoke, (New-Object System.Text.UTF8Encoding($false)))
$null = Invoke-Native { & $VenvPython $SmokeFile } "import smoke test"

Push-Location $RepoRoot
try {
    Write-Step "Building translations and data (setup.py build)"
    $null = Invoke-Native { & $VenvPython setup.py build } "setup.py build"

    if (-not $SkipTests) {
        Write-Step "Unit tests: core + exporter"
        $null = Invoke-Native { & $VenvPython -m unittest -v tests.test_core tests.test_exporter } "unittest"
        Write-Step "Unit tests: outlines (pytest)"
        $null = Invoke-Native { & $VenvPython -m pytest -q tests/test_exporter_outlines.py } "pytest"
    }
} finally {
    Pop-Location
}

# --------------------------------------------------------------------------
Write-Host ""
Write-Host "Setup complete." -ForegroundColor Green
Write-Host "In PyCharm, set the project interpreter to:"
Write-Host "    $VenvPython" -ForegroundColor Yellow
Write-Host "Then pick the 'PDF Arranger' run configuration and press Run."
