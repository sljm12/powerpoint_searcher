import asyncio
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from powerpoint_extraction.confinement import run_confined, run_confined_async


@unittest.skipUnless(sys.platform == "linux", "Linux bubblewrap probe")
class ConfinementTests(unittest.TestCase):
    def test_filesystem_and_network_are_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as parent, socket.socket() as listener:
            root = Path(parent)
            work = root / "work"
            work.mkdir()
            secret = root / "secret"
            secret.write_text("private")
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            script = f"""
import errno, pathlib, socket
assert not pathlib.Path({str(secret)!r}).exists()
pathlib.Path('/work/result').write_text('ok')
s = socket.socket()
s.settimeout(1)
assert s.connect_ex(('127.0.0.1', {listener.getsockname()[1]})) != 0
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
assert s.connect_ex(('1.1.1.1', 53)) == errno.ENETUNREACH
"""
            self.assertEqual(run_confined(["/usr/bin/python3", "-c", script], work, 10), 0)
            self.assertEqual((work / "result").read_text(), "ok")

    def test_timeout_kills_detached_descendants(self) -> None:
        with tempfile.TemporaryDirectory() as parent:
            work = Path(parent)
            script = """
import subprocess, time
subprocess.Popen(['/usr/bin/python3', '-c', "import time; time.sleep(2); open('/work/escaped', 'w').write('bad')"], start_new_session=True)
time.sleep(30)
"""
            with self.assertRaises(subprocess.TimeoutExpired):
                run_confined(["/usr/bin/python3", "-c", script], work, 0.5)
            time.sleep(3)
            self.assertFalse((work / "escaped").exists())

    def test_cancellation_reaps_worker(self) -> None:
        async def exercise(work: Path) -> None:
            script = """
import pathlib, subprocess, time
subprocess.Popen(['/usr/bin/python3', '-c', "import time; time.sleep(2); open('/work/escaped', 'w').write('bad')"], start_new_session=True)
pathlib.Path('/work/started').write_text('ready')
time.sleep(30)
"""
            task = asyncio.create_task(run_confined_async(["/usr/bin/python3", "-c", script], work, 20))
            for _ in range(200):
                if (work / "started").exists() or task.done():
                    break
                await asyncio.sleep(0.05)
            self.assertTrue((work / "started").exists(), "Confined worker never started")
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            await asyncio.sleep(2.2)
            self.assertFalse((work / "escaped").exists())
        with tempfile.TemporaryDirectory() as parent:
            asyncio.run(exercise(Path(parent)))

    def test_parent_crash_stops_scoped_renderer(self) -> None:
        with tempfile.TemporaryDirectory() as parent:
            work = Path(parent)
            child = (
                "import pathlib,time; pathlib.Path('/work/started').write_text('ready'); "
                "time.sleep(1.5); pathlib.Path('/work/escaped').write_text('bad')"
            )
            script = (
                "import asyncio; from pathlib import Path; "
                "from powerpoint_extraction.confinement import run_confined_async; "
                f"asyncio.run(run_confined_async(['/usr/bin/python3','-c',{child!r}], "
                f"Path({str(work)!r}), 10))"
            )
            server = subprocess.Popen([sys.executable, "-c", script], start_new_session=True)
            try:
                for _ in range(200):
                    if (work / "started").exists() or server.poll() is not None:
                        break
                    time.sleep(0.05)
                self.assertTrue((work / "started").exists(), "Scoped renderer never started")
                server.kill()
                server.wait(timeout=5)
                time.sleep(2)
                self.assertFalse((work / "escaped").exists())
            finally:
                if server.poll() is None:
                    server.kill()
                    server.wait(timeout=5)

    def test_oversized_writer_is_stopped(self) -> None:
        with tempfile.TemporaryDirectory() as parent:
            work = Path(parent)
            script = """
import pathlib, time
with open('/work/large', 'wb') as output:
    output.truncate(513 * 1024**2)
time.sleep(2)
pathlib.Path('/work/escaped').write_text('bad')
"""
            with self.assertRaisesRegex(RuntimeError, "512 MiB"):
                run_confined(["/usr/bin/python3", "-c", script], work, 10)
            time.sleep(2.2)
            self.assertFalse((work / "escaped").exists())


if __name__ == "__main__":
    unittest.main()
