"""Standalone native picker: safe to run through a WSL UNC path without project imports."""

import json
import os
import sys
import threading


def dialog(kind, initialdir=None):
    import tkinter as tk
    from tkinter import filedialog

    if kind not in ("env_file", "database"):
        raise ValueError("Unknown file selection kind")
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        options = {
            "parent": root,
            "title": "选择已有配置文件" if kind == "env_file" else "选择已有数据库（不会创建文件）",
            "filetypes": [("所有文件", "*")],
        }
        if initialdir:
            options["initialdir"] = initialdir
        return filedialog.askopenfilename(**options)
    finally:
        root.destroy()


def main(arguments=None, timeout=280):
    arguments = sys.argv[1:] if arguments is None else arguments
    # A WSL relay disappearing must not leave a native dialog running indefinitely.
    # This only exits this dedicated chooser process, never a persisted PID.
    timer = threading.Timer(timeout, lambda: os._exit(2))
    timer.daemon = True
    timer.start()
    try:
        selected = dialog(arguments[0], arguments[1] if len(arguments) > 1 else None)
        print(json.dumps({"path": selected}))
    finally:
        timer.cancel()


if __name__ == "__main__":
    main()
