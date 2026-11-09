"""Windows source launcher; explicit modes, argument-vector WSL transport, no shell."""

import contextlib
import json
import ntpath
import os
import posixpath
import queue
import re
import secrets
import subprocess
import threading

from desktop.processes import supervisor_python
from desktop.runtime import ROOT, write_json


def distributions(data):
    encoding = "utf-16-le" if b"\0" in data or data.startswith(b"\xff\xfe") else "utf-8-sig"
    try:
        names = data.decode(encoding).lstrip("\ufeff").splitlines()
    except UnicodeError:
        raise RuntimeError("WSL发行版列表编码无法识别，请检查WSL状态。") from None
    return [name.strip() for name in names if name.strip()]


def list_distributions():
    result = subprocess.run(
        ["wsl.exe", "--list", "--quiet"],
        capture_output=True,
        timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    if result.returncode:
        raise RuntimeError("未能列出WSL发行版；请确认WSL已安装且至少有一个可用发行版。")
    return distributions(result.stdout)


def project_path(kind, value, distribution=""):
    if not isinstance(value, str) or not value or any(c in value for c in "\0\r\n"):
        raise ValueError("请选择有效的源码目录。")
    if kind == "windows-native":
        drive, _ = ntpath.splitdrive(value)
        if not re.fullmatch(r"[a-zA-Z]:", drive) or not ntpath.isabs(value):
            raise ValueError("Windows原生模式需要独立的本机磁盘源码目录，不能使用WSL共享目录。")
        return ntpath.normpath(value)
    if kind != "wsl":
        raise ValueError("请明确选择Windows原生或WSL。")
    unc = value.replace("/", "\\")
    if unc.startswith("\\\\"):
        parts = unc[2:].split("\\")
        if len(parts) < 3 or parts[0].lower() not in ("wsl.localhost", "wsl$"):
            raise ValueError("请选择所选WSL发行版中的目录。")
        if parts[1].casefold() != distribution.casefold():
            raise ValueError("所选目录属于其他发行版，请核对发行版与源码目录。")
        value = "/" + "/".join(parts[2:])
    if not value.startswith("/") or value.startswith("//") or "\\" in value:
        raise ValueError("WSL源码目录须为Linux绝对路径，或所选发行版的WSL共享目录。")
    return posixpath.normpath(value)


def command(profile, nonce, state_dir=None, picker_python=None):
    kind = profile.get("mode")
    distro = profile.get("distribution", "")
    if kind == "wsl" and (
        not isinstance(distro, str)
        or not distro
        or distro.startswith("-")
        or any(c in distro for c in "\0\r\n")
    ):
        raise ValueError("请选择明确的WSL发行版。")
    project = project_path(kind, profile.get("project", ""), distro)
    interpreter = profile.get("python", "")
    if (
        not isinstance(interpreter, str)
        or not interpreter
        or interpreter.startswith("-")
        or any(c in interpreter for c in "\0\r\n")
    ):
        raise ValueError("请选择该环境中已有的Python解释器。")
    child = [interpreter, "-m", "desktop", "_connect", "--nonce", nonce]
    if state_dir:
        child += ["--state-dir", state_dir]
    if kind == "wsl":
        if picker_python:
            child += ["--picker-python", picker_python, "--picker-distribution", distro]
        return ["wsl.exe", "--distribution", distro, "--cd", project, "--exec", *child], None
    return child, project


def connect(profile, *, browser=True, state_dir=None):
    from desktop.console import CONSOLE_VERSION, console_request, open_browser

    nonce = secrets.token_hex(16)
    args, cwd = command(profile, nonce, state_dir, supervisor_python() if os.name == "nt" else None)
    result = subprocess.run(
        args,
        cwd=cwd,
        capture_output=True,
        timeout=45,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    try:
        reply = json.loads(result.stdout.decode("utf-8-sig"))
        if reply["bridge_version"] != 1 or reply["nonce"] != nonce:
            raise ValueError()
    except (ValueError, KeyError, TypeError):
        raise RuntimeError(
            "目标环境未能打开管理器。请核对Python 3.13+、源码目录和项目版本；"
            f"进程退出码为{result.returncode}。不会自动安装或切换其他环境。"
        ) from None
    if result.returncode:
        raise RuntimeError(reply.get("error", "目标环境启动失败。"))
    expected = project_path(profile["mode"], profile["project"], profile.get("distribution", ""))
    actual = reply.get("project", "")
    if not isinstance(actual, str):
        raise RuntimeError("目标源码目录回执无效。")
    if profile["mode"] == "windows-native":
        expected, actual = ntpath.normcase(expected), ntpath.normcase(actual)
    if reply.get("mode") != profile["mode"] or actual != expected:
        raise RuntimeError("目标环境或实际源码目录与选择不符，请选择真实源码位置。")
    record = reply.get("record", {})
    if (
        not isinstance(record, dict)
        or not isinstance(record.get("token"), str)
        or not isinstance(record.get("instance"), str)
        or type(record.get("port")) is not int
        or not 0 < record["port"] < 65536
        or not re.fullmatch(r"[a-f0-9]{64}", record.get("token", ""))
        or not re.fullmatch(r"[a-f0-9]{32}", record.get("instance", ""))
    ):
        raise RuntimeError("目标管理器的连接回执无效。")
    try:
        ping = console_request(record)
        snapshot = console_request(record, "/api/state")
        if (
            ping.get("console_version") != CONSOLE_VERSION
            or ping.get("instance") != record["instance"]
            or snapshot.get("project") != reply["project"]
            or snapshot.get("mode") != reply["mode"]
        ):
            raise ValueError()
    except (OSError, ValueError) as error:
        raise RuntimeError(
            "管理器已在目标环境启动，但本机连接未确认。请核对WSL本机端口转发后重试；不会改用其他环境。"
        ) from error
    if browser:
        open_browser(record)
    return record


def build_launcher(root, settings=None):
    import tkinter as tk
    from tkinter import filedialog, ttk

    root.title("TravelFlow · 选择运行环境")
    root.minsize(640, 420)
    frame = ttk.Frame(root, padding=24)
    frame.pack(fill="both", expand=True)
    frame.columnconfigure(1, weight=1)
    ttk.Label(frame, text="选择运行环境", font=("Microsoft YaHei UI", 16)).grid(
        row=0, column=0, columnspan=3, sticky="w", pady=(0, 16)
    )
    variables = {key: tk.StringVar() for key in ("mode", "distribution", "project", "python")}
    status = tk.StringVar(value="请选择环境；不会自动启动旅行助手或移动数据。")
    widgets = []
    labels = {
        "mode": "运行环境",
        "distribution": "WSL发行版",
        "project": "源码目录",
        "python": "Python解释器",
    }
    modes = {"Windows 原生": "windows-native", "WSL Linux": "wsl"}
    for row, key in enumerate(variables, 1):
        ttk.Label(frame, text=labels[key]).grid(row=row, column=0, sticky="w", padx=(0, 16), pady=8)
        if key in ("mode", "distribution"):
            field = ttk.Combobox(frame, textvariable=variables[key], state="readonly")
            if key == "mode":
                field["values"] = list(modes)
        else:
            field = ttk.Entry(frame, textvariable=variables[key])
        field.grid(row=row, column=1, sticky="ew", pady=8)
        widgets.append(field)
    events = queue.Queue()
    busy = False
    mode_generation = 0
    settings = settings or ROOT / ".desktop-run/launcher.json"
    saved = {}
    with contextlib.suppress(OSError, ValueError):
        saved = json.loads(settings.read_text(encoding="utf-8"))
    if not isinstance(saved, dict):
        saved = {}

    def choose_project():
        distro = variables["distribution"].get()
        initial = (
            f"\\\\wsl.localhost\\{distro}\\"
            if variables["mode"].get() == "WSL Linux" and distro
            else str(ROOT)
        )
        selected = filedialog.askdirectory(
            parent=root,
            title="选择对应环境的TravelFlow源码目录",
            initialdir=initial,
            mustexist=True,
        )
        if selected:
            variables["project"].set(selected)

    def choose_python():
        wsl = variables["mode"].get() == "WSL Linux"
        distro = variables["distribution"].get()
        if wsl and not distro:
            status.set("请先选择发行版。")
            return
        options = {"parent": root, "title": "选择对应环境中的Python解释器"}
        if wsl:
            options["initialdir"] = f"\\\\wsl.localhost\\{distro}\\usr\\bin"
            options["filetypes"] = [("所有文件", "*")]
        else:
            options["filetypes"] = [("Python", "python*.exe"), ("所有文件", "*")]
        selected = filedialog.askopenfilename(**options)
        if selected:
            try:
                variables["python"].set(project_path("wsl", selected, distro) if wsl else selected)
            except ValueError as error:
                status.set(str(error))

    for row, callback in ((3, choose_project), (4, choose_python)):
        button = ttk.Button(frame, text="浏览…", command=callback)
        button.grid(row=row, column=2, padx=(12, 0))
        widgets.append(button)
    ttk.Label(
        frame,
        text="Windows与WSL应使用各自的源码、配置和数据。切换前请先在原管理页停止服务。\n本窗口只打开管理页，数据仍需在页内预览确认。",
        wraplength=590,
    ).grid(row=5, column=0, columnspan=3, sticky="w", pady=16)
    ttk.Label(frame, textvariable=status, wraplength=590).grid(
        row=7, column=0, columnspan=3, sticky="w", pady=16
    )

    def mode_changed(_=None):
        nonlocal mode_generation
        mode_generation += 1
        generation = mode_generation
        kind = modes.get(variables["mode"].get())
        previous = saved if saved.get("mode") == kind else {}
        variables["project"].set(previous.get("project", "" if kind == "wsl" else str(ROOT)))
        variables["python"].set(
            previous.get("python", "python3" if kind == "wsl" else supervisor_python())
        )
        widgets[1].configure(state="readonly" if kind == "wsl" else "disabled")
        if kind != "wsl":
            status.set("已选择Windows原生；请核对本机独立源码目录和解释器。")
        if kind == "wsl":
            status.set("正在读取已安装发行版…")

            def discover():
                try:
                    events.put(("distros", (generation, list_distributions())))
                except (OSError, RuntimeError, subprocess.SubprocessError) as error:
                    events.put(("discovery_error", (generation, str(error))))

            threading.Thread(target=discover, daemon=True).start()

    widgets[0].bind("<<ComboboxSelected>>", mode_changed)

    def launch():
        nonlocal busy
        if busy:
            return
        profile = {key: variable.get() for key, variable in variables.items()}
        profile["mode"] = modes.get(profile["mode"], "")
        try:
            command(profile, secrets.token_hex(16))
        except ValueError as error:
            status.set(str(error))
            return
        busy = True
        for widget in [*widgets, launch_button]:
            widget.configure(state="disabled")
        status.set("正在连接所选环境的管理器，请等待…")

        def work():
            try:
                connect(profile)
                warning = ""
                try:
                    settings.parent.mkdir(parents=True, exist_ok=True)
                    write_json(settings, profile)
                except OSError:
                    warning = "上次选择未能保存，下次需重新填写。"
                events.put(("opened", (profile, warning)))
            except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
                events.put(("error", str(error)))

        threading.Thread(target=work, daemon=True).start()

    launch_button = ttk.Button(frame, text="打开所选环境的管理页", command=launch)
    launch_button.grid(row=6, column=0, columnspan=3, sticky="w")

    poll_id = None

    def poll():
        nonlocal busy, saved, poll_id
        try:
            while True:
                kind, value = events.get_nowait()
                if kind in ("distros", "discovery_error"):
                    generation, value = value
                    if generation != mode_generation:
                        continue
                    if kind == "discovery_error":
                        status.set(value)
                        continue
                    widgets[1]["values"] = value
                    if saved.get("distribution") in value:
                        variables["distribution"].set(saved["distribution"])
                    status.set("请选择发行版及其实际源码目录。")
                else:
                    busy = False
                    for index, widget in enumerate(widgets):
                        widget.configure(state="readonly" if index < 2 else "normal")
                    if variables["mode"].get() != "WSL Linux":
                        widgets[1].configure(state="disabled")
                    launch_button.configure(state="normal")
                    if kind == "opened":
                        saved, warning = value
                        status.set(
                            "管理页已打开；请在管理页确认数据并启动。关闭此窗口不会停止管理器。"
                            + warning
                        )
                    else:
                        status.set(value)
        except queue.Empty:
            pass
        poll_id = root.after(100, poll)

    def destroyed(event):
        nonlocal poll_id
        if event.widget is root and poll_id is not None:
            root.after_cancel(poll_id)
            poll_id = None

    root.bind("<Destroy>", destroyed, add="+")

    # Remembering a previous choice never makes it the default execution mode.
    poll_id = root.after(100, poll)
    root.protocol(
        "WM_DELETE_WINDOW",
        lambda: root.destroy() if not busy else status.set("连接正在进行，请等待结果后关闭。"),
    )
    return {
        "variables": variables,
        "widgets": widgets,
        "launch_button": launch_button,
        "status": status,
    }


def show_launcher():
    try:
        import tkinter as tk
    except ImportError:
        raise RuntimeError(
            "系统选择窗口需要Python的Tcl/Tk组件。请修复Python安装，或使用对应环境的console命令。"
        ) from None
    try:
        root = tk.Tk()
    except tk.TclError:
        raise RuntimeError("无法打开系统选择窗口，请在可用的桌面会话中重试。") from None
    build_launcher(root)
    root.mainloop()
