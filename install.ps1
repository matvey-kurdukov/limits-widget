# Installs the Claude / ChatGPT limits widget for the current Windows user. No admin rights needed.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File install.ps1
#
# The widget runs from this folder: Python venv with Pillow in .venv, autostart shortcut
# in the Startup folder, desktop shortcut that restarts it. Python 3.12 is installed
# for the current user if there is no suitable Python yet (winget, then python.org).
# Running it again updates the install. -NoShortcuts / -NoStart are for testing.
# The file is ASCII on purpose: Windows PowerShell 5.1 reads BOM-less files as ANSI.
param([switch]$NoShortcuts, [switch]$NoStart)

$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$App = $PSScriptRoot
$Widget = Join-Path $App 'widget.py'
$PyVersion = '3.12.10'

function Step($Text) { Write-Host "== $Text" }
function Fail($Text) { Write-Host "FAIL: $Text"; exit 1 }

function Find-Python {
    # Python 3.9+ with tkinter. The python.exe in WindowsApps is a Microsoft Store stub, skip it
    $ErrorActionPreference = 'Continue'
    $found = @()
    foreach ($c in @(Get-Command python.exe -All -ErrorAction SilentlyContinue)) {
        if ($c.Source -notlike '*\WindowsApps\*') { $found += $c.Source }
    }
    $py = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($py) { $found += (& $py.Source -3 -c "import sys; print(sys.executable)" 2>$null) }
    $found += Get-ChildItem "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe", "$env:ProgramFiles\Python3*\python.exe" -ErrorAction SilentlyContinue |
        Sort-Object FullName -Descending | ForEach-Object { $_.FullName }
    foreach ($exe in $found) {
        if (-not $exe -or -not (Test-Path $exe)) { continue }
        & $exe -c "import sys, tkinter; sys.exit(0 if sys.version_info >= (3, 9) else 1)" 2>$null
        if ($LASTEXITCODE -eq 0) { return $exe }
    }
    return $null
}

function Install-Python {
    $ErrorActionPreference = 'Continue'
    if (Get-Command winget.exe -ErrorAction SilentlyContinue) {
        Step 'Installing Python 3.12 for the current user with winget'
        winget install --id Python.Python.3.12 -e --scope user --silent --accept-source-agreements --accept-package-agreements --disable-interactivity
        $p = Find-Python
        if ($p) { return $p }
    }
    $arch = if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { 'arm64' } else { 'amd64' }
    $setup = Join-Path $env:TEMP "python-$PyVersion-$arch.exe"
    Step "Downloading Python $PyVersion ($arch) from python.org"
    Invoke-WebRequest "https://www.python.org/ftp/python/$PyVersion/python-$PyVersion-$arch.exe" -OutFile $setup -UseBasicParsing
    # Per-user install, the py launcher is skipped: for all users it would ask for admin rights
    Start-Process $setup -ArgumentList '/quiet InstallAllUsers=0 PrependPath=1 Include_tcltk=1 Include_launcher=0 Include_test=0' -Wait
    Remove-Item $setup -ErrorAction SilentlyContinue
    return Find-Python
}

function Get-WidgetProcess {
    $w = [WildcardPattern]::Escape($Widget)
    Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" |
        Where-Object { $_.CommandLine -like "*$w*" -and $_.CommandLine -notlike '*--check*' }
}

if ($env:OS -ne 'Windows_NT') { Fail 'the widget works on Windows only' }
if (-not (Test-Path $Widget)) { Fail "widget.py not found next to install.ps1 ($App)" }

Step 'Looking for Python 3.9+ with tkinter'
$Python = Find-Python
if (-not $Python) { $Python = Install-Python }
if (-not $Python) { Fail 'no Python with tkinter and it could not be installed. Install Python 3.12 from python.org with "tcl/tk" ticked, then run install.ps1 again' }
Write-Host "Python: $Python"

# A running widget holds .venv\Scripts\pythonw.exe, stop it before touching the venv
$running = @(Get-WidgetProcess)
foreach ($p in $running) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }

Step 'Preparing .venv with Pillow'
$Venv = Join-Path $App '.venv'
$VPy = Join-Path $Venv 'Scripts\python.exe'
$VPyw = Join-Path $Venv 'Scripts\pythonw.exe'
$ErrorActionPreference = 'Continue'
if (Test-Path $VPy) { & $VPy -c "import sys" 2>$null }
if (-not (Test-Path $VPy) -or $LASTEXITCODE -ne 0) {
    & $Python -m venv --clear $Venv
    if ($LASTEXITCODE -ne 0) { Fail 'could not create .venv' }
}
& $VPy -m pip install --disable-pip-version-check --quiet --upgrade pillow
if ($LASTEXITCODE -ne 0) { Fail 'pip could not install Pillow, see the error above' }
& $VPy -c "import tkinter, PIL.ImageTk"
if ($LASTEXITCODE -ne 0) { Fail 'tkinter or Pillow does not import in .venv' }
$ErrorActionPreference = 'Stop'

Step 'Checking limits (one 1-token request per account)'
& $VPy $Widget --check
$CheckOk = $LASTEXITCODE -eq 0

$StartupLnk = Join-Path ([Environment]::GetFolderPath('Startup')) 'limits-widget.lnk'
# "Limity" in Cyrillic, built from char codes to keep this file ASCII
$DesktopLnk = Join-Path ([Environment]::GetFolderPath('Desktop')) ((-join [char[]](0x41B, 0x438, 0x43C, 0x438, 0x442, 0x44B)) + '.lnk')
if (-not $NoShortcuts) {
    Step 'Creating autostart and desktop shortcuts'
    $shell = New-Object -ComObject WScript.Shell
    foreach ($s in @(@($StartupLnk, 'widget.py'), @($DesktopLnk, 'restart.pyw'))) {
        $lnk = $shell.CreateShortcut($s[0])
        $lnk.TargetPath = $VPyw
        $lnk.Arguments = '"' + (Join-Path $App $s[1]) + '"'
        $lnk.WorkingDirectory = $App
        $lnk.IconLocation = Join-Path $App 'limits.ico'
        $lnk.Save()
        Write-Host "  $($s[0])"
    }
}

if (-not $NoStart) {
    Step 'Starting the widget'
    if ($NoShortcuts) {
        Start-Process $VPyw -ArgumentList ('"' + $Widget + '"') -WorkingDirectory $App
    } else {
        # Through explorer: the widget is not a child of this terminal and survives when it closes
        Start-Process explorer.exe -ArgumentList ('"' + $StartupLnk + '"')
    }
    $proc = $null
    foreach ($i in 1..10) {
        Start-Sleep -Seconds 1
        $proc = Get-WidgetProcess | Select-Object -First 1
        if ($proc) { break }
    }
    if (-not $proc) { Fail "the widget did not start. Run `"$VPy`" `"$Widget`" to see the error" }
    Write-Host "Widget is running, pid $($proc.ProcessId). It sits at the bottom of the screen and can be dragged."
}

if ($CheckOk) {
    Write-Host 'DONE'
} else {
    Write-Host 'DONE, but Claude limits did not load: see the "Claude: FAIL" line above. "no login" means: run claude in a terminal once and log in with /login (Pro or Max subscription), the widget picks it up by itself within 10 minutes.'
}
