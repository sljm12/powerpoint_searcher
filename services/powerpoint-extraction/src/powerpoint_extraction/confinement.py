"""Run the renderer in an OS-enforced, disposable Linux sandbox."""

import asyncio
import hashlib
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path

from powerpoint_extraction.renderer_limits import check_output_limits


async def run_confined_async(
    command: list[str],
    work_dir: Path,
    timeout: float,
    readonly_paths: tuple[Path, ...] = (),
) -> int:
    """Run a trusted renderer command with /work as its only writable directory.

    Commands must use paths visible inside the sandbox. No caller environment is
    inherited. A nonzero result includes sandbox setup failures.
    """
    if sys.platform == "win32":
        raise RuntimeError("Native Windows renderer confinement is unavailable; run this service in Linux or WSL")
    if sys.platform != "linux":
        raise RuntimeError("Renderer confinement is unavailable on this platform")
    bwrap = shutil.which("bwrap")
    if bwrap is None:
        raise RuntimeError("Renderer confinement requires bubblewrap")
    systemd_run = shutil.which("systemd-run")
    if systemd_run is None:
        raise RuntimeError("Renderer confinement requires a systemd user scope")
    work_dir = work_dir.resolve(strict=True)
    if not work_dir.is_dir() or timeout <= 0 or not command:
        raise ValueError("A work directory, command, and positive timeout are required")

    unit = "ex1-render-" + hashlib.sha256(os.fsencode(work_dir)).hexdigest()[:20] + ".scope"
    args = [systemd_run, "--user", "--scope", "--quiet", "--collect", "--unit", unit,
            "-p", "MemoryMax=2G", "-p", "MemorySwapMax=0", "-p", "TasksMax=128", sys.executable,
            str(Path(__file__).with_name("scope.py")), bwrap,
            "--unshare-all", "--new-session", "--die-with-parent"]
    for path in readonly_paths:
        if not path.is_absolute() or not path.exists():
            raise ValueError("Read-only paths must be existing absolute paths")
    for path in (Path("/usr"), Path("/lib"), Path("/lib64"),
                 Path("/etc/fonts"), Path("/etc/alternatives"), Path("/etc/libreoffice"),
                 Path("/etc/ld.so.cache"), *readonly_paths):
        if path.exists():
            args.extend(("--ro-bind", str(path), str(path)))
    args.extend(("--symlink", "usr/bin", "/bin", "--proc", "/proc", "--dev", "/dev", "--size", "536870912", "--tmpfs", "/tmp",
                 "--dir", "/home", "--bind", str(work_dir), "/work",
                 "--chdir", "/work", "--clearenv", "--setenv", "HOME", "/tmp",
                 "--setenv", "TMPDIR", "/tmp", "--setenv", "PATH", "/usr/bin:/bin",
                 "--", *command))
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(Path(__file__).with_name("launch_confined.py")),
        str(os.getpid()),
        unit,
        *args,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    deadline = asyncio.get_running_loop().time() + timeout
    try:
        while process.returncode is None:
            check_output_limits(work_dir)
            if asyncio.get_running_loop().time() >= deadline:
                raise subprocess.TimeoutExpired(command, timeout)
            await asyncio.sleep(0.05)
        check_output_limits(work_dir)
        return process.returncode
    finally:
        if process.returncode is None:
            try:
                process.send_signal(signal.SIGTERM)
            except ProcessLookupError:
                pass
        cleanup = asyncio.create_task(process.wait())
        cancelled = False
        while True:
            try:
                await asyncio.shield(cleanup)
                break
            except asyncio.CancelledError:
                cancelled = True
        if cancelled:
            raise asyncio.CancelledError


def run_confined(
    command: list[str], work_dir: Path, timeout: float,
    readonly_paths: tuple[Path, ...] = (),
) -> int:
    return asyncio.run(run_confined_async(command, work_dir, timeout, readonly_paths))

