"""Gated child wrapper; stdin EOF means its owning manager disappeared."""

import json
import os
import signal
import subprocess
import sys
import threading


def main():
    line = sys.stdin.buffer.readline()
    if not line:
        return 1
    command = json.loads(line)
    if not isinstance(command, list) or not command or not all(isinstance(s, str) for s in command):
        return 1
    child = subprocess.Popen(command, stdin=subprocess.DEVNULL)

    def parent_gone():
        while os.read(sys.stdin.fileno(), 4096):
            pass
        if os.name != "nt":
            os.killpg(os.getpgrp(), signal.SIGTERM)
        # Windows job ownership performs cleanup when the manager handle closes.

    threading.Thread(target=parent_gone, daemon=True).start()
    result = child.wait()
    if os.name != "nt":
        os.killpg(os.getpgrp(), signal.SIGTERM)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
