"""Перезапуск виджета лимитов: закрывает запущенные копии widget.py из этой папки и стартует новую.

Ярлык «Лимиты» на рабочем столе. Если виджет завис или пропал - клик по ярлыку.
"""
import subprocess
import sys
from pathlib import Path

WIDGET = Path(__file__).with_name("widget.py")
NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW: без мигания консоли

# Ищем по командной строке, а не по имени: pythonw.exe запускает и другие скрипты
KILL = (
    "$w = [WildcardPattern]::Escape('" + str(WIDGET).replace("'", "''") + "'); "
    "Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe' OR Name='python.exe'\" | "
    "Where-Object { $_.CommandLine -like \"*$w*\" } | "
    "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
)
subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", KILL], creationflags=NO_WINDOW)
subprocess.Popen([sys.executable, str(WIDGET)], cwd=str(WIDGET.parent), creationflags=NO_WINDOW)
