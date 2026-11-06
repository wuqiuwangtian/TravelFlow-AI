"""Own process trees; never terminate a PID read from a state file."""

import contextlib
import ctypes
import json
import os
import signal
import subprocess
import sys
from pathlib import Path


class WindowsJob:
    """Kill-on-close job; a gated wrapper joins before it can create children."""

    def __init__(self, process):
        from ctypes import wintypes as w

        class Basic(ctypes.Structure):
            _fields_ = [
                ("user_time", ctypes.c_longlong),
                ("job_time", ctypes.c_longlong),
                ("flags", w.DWORD),
                ("min_ws", ctypes.c_size_t),
                ("max_ws", ctypes.c_size_t),
                ("active", w.DWORD),
                ("affinity", ctypes.c_size_t),
                ("priority", w.DWORD),
                ("scheduling", w.DWORD),
            ]

        class Extended(ctypes.Structure):
            _fields_ = [
                ("basic", Basic),
                ("io", ctypes.c_ulonglong * 6),
                ("process_memory", ctypes.c_size_t),
                ("job_memory", ctypes.c_size_t),
                ("peak_process", ctypes.c_size_t),
                ("peak_job", ctypes.c_size_t),
            ]

        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        signatures = {
            "CreateJobObjectW": ([ctypes.c_void_p, w.LPCWSTR], w.HANDLE),
            "SetInformationJobObject": (
                [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD],
                w.BOOL,
            ),
            "AssignProcessToJobObject": ([w.HANDLE, w.HANDLE], w.BOOL),
            "OpenProcess": ([w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
            "CloseHandle": ([w.HANDLE], w.BOOL),
            "TerminateJobObject": ([w.HANDLE, w.UINT], w.BOOL),
        }
        for name, (args, result) in signatures.items():
            function = getattr(self.api, name)
            function.argtypes = args
            function.restype = result
        self.handle = self.api.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            limits = Extended()
            limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not self.api.SetInformationJobObject(
                self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            handle = self.api.OpenProcess(0x0100 | 0x0001, False, process.pid)
            if not handle:
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                if not self.api.AssignProcessToJobObject(self.handle, handle):
                    raise ctypes.WinError(ctypes.get_last_error())
            finally:
                self.api.CloseHandle(handle)
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.handle:
            self.api.TerminateJobObject(self.handle, 1)
            self.api.CloseHandle(self.handle)
            self.handle = None


class OwnedProcess:
    def __init__(self, command, *, cwd, env, log):
        self.job = None
        self.process = subprocess.Popen(
            [sys.executable, str(Path(__file__).with_name("child.py"))],
            cwd=cwd,
            env=env,
            stdin=subprocess.PIPE,
            stdout=log,
            stderr=log,
            start_new_session=os.name != "nt",
            creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
        )
        try:
            if os.name == "nt":
                self.job = WindowsJob(self.process)
            # No application process exists until job/group ownership is established.
            self.process.stdin.write((json.dumps(command) + "\n").encode())
            self.process.stdin.flush()
        except BaseException:
            self.stop()
            raise

    def stop(self):
        if self.job:
            self.job.close()
            self.job = None
        elif os.name != "nt" and self.process.poll() is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(self.process.pid, signal.SIGTERM)
        elif self.process.poll() is None:
            # Only the gated wrapper, before assignment or before it launched a child.
            self.process.terminate()
        if self.process.stdin:
            with contextlib.suppress(OSError):
                self.process.stdin.close()
        try:
            self.process.wait(timeout=4)
        except subprocess.TimeoutExpired:
            if os.name != "nt":
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(self.process.pid, signal.SIGKILL)
            else:
                self.process.kill()
            self.process.wait(timeout=4)
