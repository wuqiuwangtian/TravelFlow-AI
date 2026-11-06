"""Private launcher IPC; stdout carries a capability and must never be logged."""

import os
import re
import sys
from pathlib import Path

from desktop.runtime import ROOT, mode


def connect_payload(folder, nonce):
    if not re.fullmatch(r"[a-f0-9]{32}", nonce or ""):
        raise ValueError("启动请求标识无效。")
    if sys.version_info < (3, 13):  # noqa: UP036 - user-selected bootstrap may be older
        raise RuntimeError("目标解释器需要Python 3.13或更新版本，请重新选择解释器。")
    if Path.cwd().resolve() != ROOT:
        raise RuntimeError("目标目录没有加载对应源码，请选择实际项目根目录。")
    if mode() == "wsl":
        # Non-shell WSL execution does not read interactive shell startup files.
        bins = [Path.home() / ".local/bin", Path.home() / ".local/share/pnpm"]
        os.environ["PATH"] = os.pathsep.join(
            [str(path) for path in bins if path.is_dir()] + [os.environ.get("PATH", "")]
        )
    from desktop.console import open_console

    record = open_console(folder, browser=False)
    return {
        "bridge_version": 1,
        "nonce": nonce,
        "mode": mode(),
        "project": str(ROOT),
        "record": record,
    }
