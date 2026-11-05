from __future__ import annotations

import ctypes
import os
import signal
import subprocess
import sys
import threading


def main() -> int:
    parent_pid = int(sys.argv[1])
    unit = sys.argv[2]
    command = sys.argv[3:]
    if os.getppid() != parent_pid:
        return 1
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(1, signal.SIGTERM, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "Could not arm renderer parent-death signal")
    if os.getppid() != parent_pid:
        return 1
    stopping = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopping.set())
    process = subprocess.Popen(command)
    while process.poll() is None:
        if stopping.wait(0.05):
            try:
                subprocess.run(
                    ["systemctl", "--user", "stop", unit],
                    check=False,
                    timeout=10,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except subprocess.TimeoutExpired:
                pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            break
    return process.returncode or 0


if __name__ == "__main__":
    raise SystemExit(main())
