import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

if sys.platform == "win32":
    from confinement_windows import run_windows_confined, run_windows_confined_async


@unittest.skipUnless(sys.platform == "win32", "Windows AppContainer probe")
class WindowsConfinementTests(unittest.TestCase):
    python = os.environ.get("EXTRACTION_UNO_PYTHON", r"C:\Program Files\LibreOffice\program\python.exe")

    def test_filesystem_network_and_environment_are_isolated(self) -> None:
        with tempfile.TemporaryDirectory(dir=".") as parent, socket.socket() as listener:
            root = Path(parent).resolve()
            work = root / "work"
            work.mkdir()
            secret = root / "secret"
            secret.write_text("private")
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            source = work / "probe.py"
            source.write_text(f"""
import os, pathlib, socket, json
try:
    pathlib.Path({str(secret)!r}).read_bytes()
except (PermissionError, FileNotFoundError):
    pass
else:
    raise AssertionError('Private file was readable')
assert 'EXTRACTION_PRIVATE_CANARY' not in os.environ
for endpoint in [('127.0.0.1', {listener.getsockname()[1]}), ('1.1.1.1', 53)]:
    with socket.socket() as connection:
        connection.settimeout(1)
        assert connection.connect_ex(endpoint) != 0
pathlib.Path('result.json').write_text(json.dumps({{'filesystem': 'denied', 'loopback': 'denied', 'external': 'denied', 'secret_env': 'absent'}}))
""")
            os.environ["EXTRACTION_PRIVATE_CANARY"] = "must-not-be-inherited"
            try:
                self.assertEqual(run_windows_confined([self.python, str(source)], work, 10), 0)
            finally:
                del os.environ["EXTRACTION_PRIVATE_CANARY"]
            self.assertEqual(json.loads((work / "result.json").read_text())["loopback"], "denied")

    def _descendant_script(self, work: Path) -> Path:
        script = work / "probe.py"
        script.write_text(f"""
import os, pathlib, subprocess, sys, time
child = "import pathlib,time; time.sleep(2); pathlib.Path('escaped').write_text('bad')"
p = subprocess.Popen([{self.python!r}, '-c', child], creationflags=subprocess.DETACHED_PROCESS,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
pathlib.Path('started').write_text(str(p.pid))
time.sleep(30)
""")
        return script

    def test_timeout_kills_detached_descendants(self) -> None:
        with tempfile.TemporaryDirectory(dir=".") as directory:
            work = Path(directory).resolve()
            script = self._descendant_script(work)
            with self.assertRaises(subprocess.TimeoutExpired):
                run_windows_confined([self.python, str(script)], work, 0.8)
            time.sleep(2.2)
            self.assertFalse((work / "escaped").exists())

    def test_cancellation_waits_for_job_and_profile_cleanup(self) -> None:
        async def exercise(work: Path) -> None:
            task = asyncio.create_task(run_windows_confined_async([self.python, str(self._descendant_script(work))], work, 30))
            await asyncio.sleep(0.8)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            await asyncio.sleep(2.2)
            self.assertFalse((work / "escaped").exists())
        with tempfile.TemporaryDirectory(dir=".") as directory:
            asyncio.run(exercise(Path(directory).resolve()))


if __name__ == "__main__":
    unittest.main()
