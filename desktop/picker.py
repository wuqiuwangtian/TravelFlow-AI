"""Optional native file dialog transport; selection never opens file contents."""

import json
import ntpath
import os
import shutil
import subprocess
from pathlib import Path

from desktop.native_dialog import (
    dialog as dialog,
)  # Backward-compatible native test/developer entry.
from desktop.processes import supervisor_python
from desktop.runtime import ROOT, mode, prepare, write_json


def valid_distribution(value):
    return isinstance(value, str) and bool(value) and not any(c in value for c in "\\/\0\r\n")


def configure_windows_picker(folder, windows_python, distribution):
    """Store only launcher-selected host paths; no key or selected business file is read."""
    path = prepare(folder) / "windows-picker.json"
    try:
        if (
            mode() != "wsl"
            or not valid_distribution(distribution)
            or distribution != os.environ.get("WSL_DISTRO_NAME")
            or not isinstance(windows_python, str)
            or any(c in windows_python for c in "\0\r\n")
            or not ntpath.isabs(windows_python)
            or ntpath.basename(windows_python).lower() not in ("python.exe", "pythonw.exe")
        ):
            raise ValueError("Invalid picker environment")
        wslpath = shutil.which("wslpath")
        if not wslpath:
            raise ValueError("WSL path conversion is unavailable")
        converted = subprocess.run(
            [wslpath, "-u", windows_python], capture_output=True, text=True, timeout=10, check=True
        ).stdout.strip()
        executable = Path(converted)
        if not executable.is_absolute() or not executable.is_file():
            raise ValueError("Host interpreter is unavailable")
        settings = {
            "project": str(ROOT),
            "distribution": distribution,
            "executable": str(executable),
        }
    except (OSError, ValueError, subprocess.SubprocessError):
        settings = {"project": str(ROOT), "unavailable": True}
    try:
        write_json(path, settings)
    except OSError:
        return False
    return True


def windows_picker(folder):
    if mode() != "wsl" or folder is None:
        return None
    path = folder / "windows-picker.json"
    try:
        if path.is_symlink():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        distro = data["distribution"]
        executable = Path(data["executable"])
        if (
            data.get("project") != str(ROOT)
            or not valid_distribution(distro)
            or distro != os.environ.get("WSL_DISTRO_NAME")
            or not executable.is_absolute()
            or executable.name.lower() not in ("python.exe", "pythonw.exe")
            or not executable.is_file()
        ):
            return None
        return data
    except (OSError, ValueError, KeyError, TypeError):
        return None


def picker_capability(folder):
    if os.name == "nt":
        return True, ""
    if mode() == "wsl":
        enabled = windows_picker(folder) is not None
        return enabled, (
            "系统选择器只接受当前WSL发行版中的文件，也可手动填写Linux地址。"
            if enabled
            else "可手动填写Linux地址；通过Windows统一启动器连接后，"
            "可启用当前发行版的系统文件选择。"
        )
    return False, ""


def unc_path(distribution, path):
    if (
        not valid_distribution(distribution)
        or not path.startswith("/")
        or "\\" in path
        or any(c in path for c in "\0\r\n")
    ):
        raise ValueError("无法向系统选择器提供该WSL地址，请手动填写路径。")
    return "\\\\wsl.localhost\\" + distribution + path.replace("/", "\\")


def wsl_selection_path(value, distribution):
    if not isinstance(value, str):
        raise ValueError("文件选择器回执无效。")
    if not value:
        return ""
    raw = value.replace("/", "\\")
    if raw.lower().startswith("\\\\?\\unc\\"):
        raw = "\\\\" + raw[8:]
    if not raw.startswith("\\\\"):
        raise ValueError("请选择当前WSL发行版中的文件；不会自动映射Windows盘符。")
    from desktop.launcher import project_path

    try:
        return project_path("wsl", raw, distribution)
    except ValueError:
        raise ValueError("所选文件不属于当前WSL发行版，请重新选择或手动填写Linux地址。") from None


def selected_wsl_file(value, distribution):
    converted = wsl_selection_path(value, distribution)
    if not converted:
        return ""
    selected = Path(converted).resolve()
    if not selected.is_file():
        raise ValueError("所选文件在当前WSL环境中不存在或不是普通文件，请重新选择。")
    return str(selected)


def pick_file(kind, folder=None):
    if kind not in ("env_file", "database"):
        raise ValueError("不支持的文件选择类型。")
    if os.name == "nt":
        command = [supervisor_python(), "-m", "desktop.native_dialog", kind]
    else:
        settings = windows_picker(folder)
        if not settings:
            raise RuntimeError("当前环境未提供可用的系统文件对话框，请直接填写本环境文件地址。")
        distro = settings["distribution"]
        command = [
            settings["executable"],
            unc_path(distro, str(ROOT / "desktop/native_dialog.py")),
            kind,
            unc_path(distro, str(ROOT)),
        ]
    try:
        result = subprocess.run(
            command,
            cwd=ROOT,
            capture_output=True,
            timeout=300,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except (OSError, subprocess.SubprocessError):
        raise RuntimeError(
            "系统文件选择器未能完成，请确认桌面／互通可用，或手动填写地址。"
        ) from None
    if result.returncode:
        raise RuntimeError("文件选择器未打开或未完成；请确认Python含Tk组件，或手动填写地址。")
    try:
        path = json.loads(result.stdout.decode("utf-8-sig"))["path"]
        if not isinstance(path, str):
            raise ValueError()
    except (ValueError, KeyError, TypeError):
        raise RuntimeError("文件选择器回执无效，请手动填写地址。") from None
    return path if os.name == "nt" else selected_wsl_file(path, distro)
