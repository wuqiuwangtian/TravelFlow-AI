"""Double-click source launcher on Windows with Python's .pyw association installed."""

import ctypes
import os

from desktop.console import open_console
from desktop.runtime import ROOT, mode

try:
    open_console(ROOT / ".desktop-run" / ("console-" + mode()))
except Exception as error:
    if os.name == "nt":
        ctypes.windll.user32.MessageBoxW(None, str(error), "TravelFlow 启动失败", 0x10)
    else:
        raise
