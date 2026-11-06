"""Optional native file dialog subprocess; never opens selected file contents."""

import json
import os
import subprocess

from desktop.processes import supervisor_python
from desktop.runtime import ROOT


def pick_file(kind):
    if kind not in ("env_file", "database"):
        raise ValueError("不支持的文件选择类型。")
    if os.name != "nt":
        raise RuntimeError("当前环境未提供原生文件对话框，请直接填写该环境中的文件地址。")
    result = subprocess.run(
        [supervisor_python(), "-m", "desktop.picker", kind],
        cwd=ROOT,
        capture_output=True,
        timeout=300,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if result.returncode:
        raise RuntimeError("文件选择器未打开或未完成；请确认Python含Tk组件，或手动填写地址。")
    try:
        path = json.loads(result.stdout.decode("utf-8"))["path"]
        if not isinstance(path, str):
            raise ValueError()
        return path
    except (ValueError, KeyError, TypeError):
        raise RuntimeError("文件选择器回执无效，请手动填写地址。") from None


def dialog(kind):
    import tkinter as tk
    from tkinter import filedialog

    if kind not in ("env_file", "database"):
        raise ValueError("Unknown file selection kind")
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        return filedialog.askopenfilename(
            parent=root,
            title="选择已有配置文件" if kind == "env_file" else "选择已有数据库（不会创建文件）",
            filetypes=[("所有文件", "*")],
        )
    finally:
        root.destroy()


if __name__ == "__main__":
    import sys

    print(json.dumps({"path": dialog(sys.argv[1])}))
