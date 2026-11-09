"""Windows loopback bind preflight; the standalone host path imports only stdlib."""

import json
import socket
import subprocess
import sys


def probe_ports(ports):
    results = []
    for port in ports:
        if type(port) is not int or not 0 < port < 65536:
            raise ValueError("Invalid port")
        with socket.socket() as sock:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            try:
                sock.bind(("127.0.0.1", port))
                results.append({"port": port, "code": 0})
            except OSError as error:
                results.append(
                    {"port": port, "code": getattr(error, "winerror", None) or error.errno}
                )
    return results


def ensure_host_ports(folder, ports):
    from desktop.picker import unc_path, windows_picker
    from desktop.runtime import ROOT

    host = windows_picker(folder)
    if host is None:
        return
    try:
        result = subprocess.run(
            [
                host["executable"],
                unc_path(host["distribution"], str(ROOT / "desktop/host_ports.py")),
                *map(str, ports),
            ],
            capture_output=True,
            timeout=5,
        )
        checks = json.loads(result.stdout.decode("utf-8-sig"))
        if result.returncode or not isinstance(checks, list) or len(checks) != len(ports):
            raise ValueError("Invalid host response")
        for port, check in zip(ports, checks, strict=True):
            if (
                not isinstance(check, dict)
                or check.get("port") != port
                or type(check.get("code")) is not int
            ):
                raise ValueError("Invalid host response")
    except (OSError, ValueError, subprocess.SubprocessError):
        raise RuntimeError("Windows端口预检未完成，请检查WSL互通与宿主Python后重试。") from None
    for check in checks:
        if check["code"]:
            reason = "被系统保留或拒绝绑定" if check["code"] == 10013 else "不可绑定或已被占用"
            raise ValueError(
                f"Windows本机端口{check['port']}{reason}（错误码{check['code']}）；请修改端口后重新预览，不会自动改端口或启动服务。"
            )


if __name__ == "__main__":
    print(json.dumps(probe_ports([int(value) for value in sys.argv[1:]])))
