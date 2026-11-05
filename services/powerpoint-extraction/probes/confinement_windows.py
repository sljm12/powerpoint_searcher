"""Disposable zero-capability AppContainer and renderer Job Object."""

from __future__ import annotations

import asyncio
import ctypes as c
import os
import shutil
import stat
import subprocess
import uuid
from ctypes import wintypes as w
from pathlib import Path

from powerpoint_extraction.renderer_limits import check_output_limits


class StartupInfo(c.Structure):
    _fields_ = [("cb", w.DWORD), ("reserved", w.LPWSTR), ("desktop", w.LPWSTR),
                ("title", w.LPWSTR), ("x", w.DWORD), ("y", w.DWORD),
                ("xsize", w.DWORD), ("ysize", w.DWORD), ("xchars", w.DWORD),
                ("ychars", w.DWORD), ("fill", w.DWORD), ("flags", w.DWORD),
                ("show", w.WORD), ("reserved2", w.WORD), ("reserved2ptr", c.c_void_p),
                ("stdin", w.HANDLE), ("stdout", w.HANDLE), ("stderr", w.HANDLE)]


class StartupInfoEx(c.Structure):
    _fields_ = [("startup", StartupInfo), ("attributes", c.c_void_p)]


class ProcessInformation(c.Structure):
    _fields_ = [("process", w.HANDLE), ("thread", w.HANDLE),
                ("pid", w.DWORD), ("tid", w.DWORD)]


class SecurityCapabilities(c.Structure):
    _fields_ = [("sid", c.c_void_p), ("capabilities", c.c_void_p),
                ("count", w.DWORD), ("reserved", w.DWORD)]


class JobBasicLimits(c.Structure):
    _fields_ = [("process_time", c.c_int64), ("job_time", c.c_int64),
                ("flags", w.DWORD), ("minimum_working_set", c.c_size_t),
                ("maximum_working_set", c.c_size_t), ("active_process_limit", w.DWORD),
                ("affinity", c.c_size_t), ("priority", w.DWORD), ("scheduling", w.DWORD)]


class JobLimits(c.Structure):
    _fields_ = [("basic", JobBasicLimits), ("io", c.c_uint64 * 6),
                ("process_memory", c.c_size_t), ("job_memory", c.c_size_t),
                ("peak_process_memory", c.c_size_t), ("peak_job_memory", c.c_size_t)]


class JobAccounting(c.Structure):
    _fields_ = [("user_time", c.c_int64), ("kernel_time", c.c_int64),
                ("period_user_time", c.c_int64), ("period_kernel_time", c.c_int64),
                ("page_faults", w.DWORD), ("total", w.DWORD),
                ("active", w.DWORD), ("terminated", w.DWORD)]


def _check(success: int) -> None:
    if not success:
        raise c.WinError(c.get_last_error())


def _hresult(result: int, operation: str) -> None:
    if result < 0:
        raise OSError(f"{operation} failed (HRESULT 0x{result & 0xffffffff:08x})")


def _copy_tree(source: Path, destination: Path) -> None:
    """Do not follow renderer-controlled links or Windows junctions."""
    destination.mkdir(parents=True, exist_ok=True)
    for entry in source.iterdir():
        details = entry.lstat()
        if details.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise ValueError("Renderer directories must not contain reparse points")
        target = destination / entry.name
        if stat.S_ISDIR(details.st_mode):
            _copy_tree(entry, target)
        elif stat.S_ISREG(details.st_mode):
            if target.exists() and target.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                raise ValueError("Renderer output destination contains a reparse point")
            shutil.copyfile(entry, target)
        else:
            raise ValueError("Renderer directories must contain ordinary files only")


async def run_windows_confined_async(
    command: list[str], work_dir: Path, timeout: float,
    readonly_paths: tuple[Path, ...] = (),
) -> int:
    """Copy an attempt into its AppContainer, reap its job, then copy successful results.

    Await this function directly so cancellation also waits for process-tree cleanup.
    Only trusted command construction belongs here. Presentation bytes are untrusted.
    """
    work_dir = work_dir.resolve(strict=True)
    if not work_dir.is_dir() or timeout <= 0 or not command:
        raise ValueError("A work directory, command, and positive timeout are required")
    for path in readonly_paths:
        if not path.is_absolute() or not path.is_file() or path.is_symlink():
            raise ValueError("Windows renderer support paths must be absolute ordinary files")

    kernel = c.WinDLL("kernel32", use_last_error=True)
    userenv = c.WinDLL("userenv", use_last_error=True)
    security = c.WinDLL("advapi32", use_last_error=True)
    ole = c.WinDLL("ole32")
    kernel.InitializeProcThreadAttributeList.argtypes = [c.c_void_p, w.DWORD, w.DWORD, c.POINTER(c.c_size_t)]
    kernel.UpdateProcThreadAttribute.argtypes = [c.c_void_p, w.DWORD, c.c_size_t, c.c_void_p, c.c_size_t, c.c_void_p, c.c_void_p]
    kernel.DeleteProcThreadAttributeList.argtypes = [c.c_void_p]
    kernel.CreateProcessW.argtypes = [w.LPCWSTR, w.LPWSTR, c.c_void_p, c.c_void_p, w.BOOL, w.DWORD, c.c_void_p, w.LPCWSTR, c.POINTER(StartupInfo), c.POINTER(ProcessInformation)]
    kernel.CreateJobObjectW.argtypes = [c.c_void_p, w.LPCWSTR]
    kernel.CreateJobObjectW.restype = w.HANDLE
    kernel.SetInformationJobObject.argtypes = [w.HANDLE, c.c_int, c.c_void_p, w.DWORD]
    kernel.QueryInformationJobObject.argtypes = [w.HANDLE, c.c_int, c.c_void_p, w.DWORD, c.c_void_p]
    kernel.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
    kernel.TerminateJobObject.argtypes = [w.HANDLE, w.UINT]
    kernel.TerminateProcess.argtypes = [w.HANDLE, w.UINT]
    kernel.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
    kernel.GetExitCodeProcess.argtypes = [w.HANDLE, c.POINTER(w.DWORD)]
    kernel.ResumeThread.argtypes = [w.HANDLE]
    kernel.ResumeThread.restype = w.DWORD
    kernel.CloseHandle.argtypes = [w.HANDLE]
    kernel.LocalFree.argtypes = [c.c_void_p]
    userenv.CreateAppContainerProfile.argtypes = [w.LPCWSTR, w.LPCWSTR, w.LPCWSTR, c.c_void_p, w.DWORD, c.POINTER(c.c_void_p)]
    userenv.DeleteAppContainerProfile.argtypes = [w.LPCWSTR]
    userenv.GetAppContainerFolderPath.argtypes = [w.LPCWSTR, c.POINTER(w.LPWSTR)]
    security.ConvertSidToStringSidW.argtypes = [c.c_void_p, c.POINTER(w.LPWSTR)]
    security.FreeSid.argtypes = [c.c_void_p]
    ole.CoTaskMemFree.argtypes = [c.c_void_p]

    name = "PptExtraction" + uuid.uuid4().hex
    sid = c.c_void_p()
    _hresult(userenv.CreateAppContainerProfile(name, name, name, None, 0, c.byref(sid)), "CreateAppContainerProfile")
    job = None
    process = ProcessInformation()
    attributes = None
    assigned = False
    profile: Path | None = None
    try:
        sid_text = w.LPWSTR()
        _check(security.ConvertSidToStringSidW(sid, c.byref(sid_text)))
        folder = w.LPWSTR()
        try:
            _hresult(userenv.GetAppContainerFolderPath(sid_text.value, c.byref(folder)), "GetAppContainerFolderPath")
            profile = Path(folder.value)
        finally:
            kernel.LocalFree(c.cast(sid_text, c.c_void_p))
            if folder:
                ole.CoTaskMemFree(c.cast(folder, c.c_void_p))

        private_work = profile / "attempt"
        _copy_tree(work_dir, private_work)
        replacements = [(str(work_dir), str(private_work)), (work_dir.as_posix(), private_work.as_posix())]
        for index, path in enumerate(readonly_paths):
            target = profile / f"support-{index}-{path.name}"
            shutil.copyfile(path, target)
            replacements.extend(((str(path), str(target)), (path.as_posix(), target.as_posix())))
        mapped = command.copy()
        for index, argument in enumerate(mapped):
            for original, replacement in replacements:
                argument = argument.replace(original, replacement)
            mapped[index] = argument

        for directory in ("temp", "roaming", "local"):
            (profile / directory).mkdir(exist_ok=True)
        windows = os.environ.get("SystemRoot", r"C:\Windows")
        environment = {"SystemRoot": windows, "WINDIR": windows,
                       "PATH": str(Path(windows) / "System32"), "USERPROFILE": str(profile),
                       "APPDATA": str(profile / "roaming"), "LOCALAPPDATA": str(profile / "local"),
                       "TEMP": str(profile / "temp"), "TMP": str(profile / "temp")}
        block = c.create_unicode_buffer("\0".join(f"{key}={value}" for key, value in sorted(environment.items())) + "\0\0")
        size = c.c_size_t()
        kernel.InitializeProcThreadAttributeList(None, 1, 0, c.byref(size))
        attributes = c.create_string_buffer(size.value)
        _check(kernel.InitializeProcThreadAttributeList(attributes, 1, 0, c.byref(size)))
        capabilities = SecurityCapabilities(sid, None, 0, 0)
        _check(kernel.UpdateProcThreadAttribute(attributes, 0, 0x00020009, c.byref(capabilities), c.sizeof(capabilities), None, None))
        startup = StartupInfoEx()
        startup.startup.cb = c.sizeof(startup)
        startup.attributes = c.addressof(attributes)

        job = kernel.CreateJobObjectW(None, None)
        _check(job)
        limits = JobLimits()
        limits.basic.flags = 0x2000 | 0x100 | 0x200 | 0x8
        limits.basic.active_process_limit = 128
        limits.process_memory = limits.job_memory = 2 * 1024**3
        _check(kernel.SetInformationJobObject(job, 9, c.byref(limits), c.sizeof(limits)))
        # Suspension prevents the renderer from creating children before job assignment.
        _check(kernel.CreateProcessW(mapped[0], c.create_unicode_buffer(subprocess.list2cmdline(mapped)), None, None, False,
                                     0x00080000 | 0x00000400 | 0x00000004 | 0x08000000,
                                     block, str(private_work), c.byref(startup.startup), c.byref(process)))
        _check(kernel.AssignProcessToJobObject(job, process.process))
        assigned = True
        if kernel.ResumeThread(process.thread) == 0xffffffff:
            raise c.WinError(c.get_last_error())
        deadline = asyncio.get_running_loop().time() + timeout
        while kernel.WaitForSingleObject(process.process, 0) == 258:
            check_output_limits(profile)
            if asyncio.get_running_loop().time() >= deadline:
                raise subprocess.TimeoutExpired(command, timeout)
            await asyncio.sleep(0.05)
        code = w.DWORD()
        _check(kernel.GetExitCodeProcess(process.process, c.byref(code)))
        check_output_limits(profile)
    finally:
        async def reap() -> None:
            if assigned:
                _check(kernel.TerminateJobObject(job, 1))
                accounting = JobAccounting()
                while True:
                    _check(kernel.QueryInformationJobObject(job, 1, c.byref(accounting), c.sizeof(accounting), None))
                    if accounting.active == 0:
                        break
                    await asyncio.sleep(0.01)
            elif process.process:
                _check(kernel.TerminateProcess(process.process, 1))
            if process.process:
                while kernel.WaitForSingleObject(process.process, 0) == 258:
                    await asyncio.sleep(0.01)

        cleanup = asyncio.create_task(reap())
        cancelled = False
        try:
            while True:
                try:
                    await asyncio.shield(cleanup)
                    break
                except asyncio.CancelledError:
                    cancelled = True
        finally:
            for handle in (process.thread, process.process, job):
                if handle:
                    kernel.CloseHandle(handle)
            if attributes is not None:
                kernel.DeleteProcThreadAttributeList(attributes)
            security.FreeSid(sid)
            try:
                if "code" in locals() and code.value == 0 and not cancelled and profile is not None:
                    _copy_tree(profile / "attempt", work_dir)
            finally:
                result = userenv.DeleteAppContainerProfile(name)
                _hresult(result, f"DeleteAppContainerProfile {name}; retry cleanup using this profile name")
        if cancelled:
            raise asyncio.CancelledError
    return code.value


def run_windows_confined(
    command: list[str], work_dir: Path, timeout: float,
    readonly_paths: tuple[Path, ...] = (),
) -> int:
    return asyncio.run(run_windows_confined_async(command, work_dir, timeout, readonly_paths))
