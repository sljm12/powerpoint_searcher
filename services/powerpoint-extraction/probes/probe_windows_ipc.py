"""Reproduce why native Windows AppContainer UNO rendering is unsupported."""

import argparse
import json
import platform
import shutil
import tempfile
from pathlib import Path

from confinement_windows import run_windows_confined


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--worker", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--python", default=r"C:\Program Files\LibreOffice\program\python.exe")
    parser.add_argument("--office", default=r"C:\Program Files\LibreOffice\program\soffice.com")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output) as temporary:
        work = Path(temporary)
        shutil.copyfile(args.fixture, work / "source.pptx")
        shutil.copyfile(args.worker, work / "worker.py")
        pipe_code = r'''
import ctypes as c,json,pathlib,uuid
k=c.WinDLL('kernel32',use_last_error=True)
k.CreateNamedPipeW.argtypes=[c.c_wchar_p,c.c_ulong,c.c_ulong,c.c_ulong,c.c_ulong,c.c_ulong,c.c_ulong,c.c_void_p]
k.CreateNamedPipeW.restype=c.c_void_p
k.CloseHandle.argtypes=[c.c_void_p]
results={}
for prefix in ('OSL_PIPE_','LOCAL\\'):
    handle=k.CreateNamedPipeW('\\\\.\\pipe\\'+prefix+uuid.uuid4().hex,3,0,1,4096,4096,0,None)
    results[prefix]=c.get_last_error() if handle==c.c_void_p(-1).value else 0
    if handle!=c.c_void_p(-1).value:k.CloseHandle(handle)
pathlib.Path('pipes.json').write_text(json.dumps(results))
'''
        assert run_windows_confined([args.python, "-c", pipe_code], work, 10) == 0
        pipes = json.loads((work / "pipes.json").read_text())
        conversion = "import runpy,sys,pathlib,traceback\nsys.argv=" + repr(
            ["worker.py", "--input", "source.pptx", "--output", "rendered", "--office", args.office]
        ) + "\ntry: runpy.run_path('worker.py',run_name='__main__')\nexcept BaseException: pathlib.Path('startup-error.txt').write_text(traceback.format_exc())"
        assert run_windows_confined([args.python, "-c", conversion], work, 60) == 0
        error = (work / "startup-error.txt").read_text() if (work / "startup-error.txt").exists() else None
        if error:
            (output / "startup-error.txt").write_text(error)
        local_code = "import uno,pathlib; context=uno.getComponentContext(); desktop=context.ServiceManager.createInstanceWithContext('com.sun.star.frame.Desktop',context); pathlib.Path('desktop.txt').write_text(str(desktop))"
        local_exit = run_windows_confined([args.python, "-c", local_code], work, 15)
        status = {"platform": platform.platform(), "native_windows_supported": False,
                  "pipe_creation_winerrors": pipes, "uno_startup_error": error,
                  "in_process_desktop_exit_code": local_exit,
                  "actual_conversion_succeeded": (work / "rendered" / "uno.json").exists(),
                  "network_capabilities": [], "loopback_exemptions_added": False,
                  "supported_deployment": "Linux service, including under WSL"}
        (output / "native-windows-status.json").write_text(json.dumps(status, indent=2))
        print(json.dumps(status))


if __name__ == "__main__":
    main()
