"""Double-click source launcher on Windows with Python's .pyw association installed."""

import ctypes
import os

from desktop.runtime import ROOT, mode

try:
    if os.name == "nt":
        from desktop.launcher import show_launcher

        show_launcher()
    else:
        from desktop.console import open_console

        open_console(ROOT / ".desktop-run" / ("console-" + mode()))
except Exception as error:
    if os.name == "nt":
        ctypes.windll.user32.MessageBoxW(None, str(error), "TravelFlow 启动失败", 0x10)
    else:
        raise
