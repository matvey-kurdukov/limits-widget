# Stops the limits widget and removes its autostart and desktop shortcuts.
# The folder itself (code, .venv, settings) stays: delete it by hand if it is no longer needed.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File uninstall.ps1

$Widget = Join-Path $PSScriptRoot 'widget.py'
$w = [WildcardPattern]::Escape($Widget)
Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" |
    Where-Object { $_.CommandLine -like "*$w*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

$StartupLnk = Join-Path ([Environment]::GetFolderPath('Startup')) 'limits-widget.lnk'
$DesktopLnk = Join-Path ([Environment]::GetFolderPath('Desktop')) ((-join [char[]](0x41B, 0x438, 0x43C, 0x438, 0x442, 0x44B)) + '.lnk')
foreach ($lnk in $StartupLnk, $DesktopLnk) {
    if (Test-Path $lnk) { Remove-Item $lnk; Write-Host "removed $lnk" }
}
Write-Host 'DONE'
