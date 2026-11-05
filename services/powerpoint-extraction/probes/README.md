# Windows confinement experiments

These probes are experimental. The production renderer rejects native Windows execution.
Run the supported service on Linux, including a Linux distribution under WSL.
The experiment does not establish native Windows extraction support.

`confinement_windows.py` owns one disposable AppContainer and Job Object per attempt.
The AppContainer has no network capabilities or loopback exemption.
The job caps committed memory at 2 GiB and active processes at 128.
Timeout and cancellation terminate the job and wait for zero active processes before profile cleanup.
Only a private attempt copy and trusted support scripts enter the AppContainer.
The renderer receives a fixed environment with no service secrets.
The polling disk guardrail rejects an attempt above 512 MiB or 10,000 entries.
Polling is an operational limit, not a filesystem quota, and permits temporary overshoot between checks.

The basic isolation tests pass on Windows 11 build 26200 with LibreOffice 26.8.2.2.
Actual UNO conversion fails before the private pipe becomes available.
A native pipe probe returns access denied for `OSL_PIPE_` and succeeds for `LOCAL\`.
LibreOffice [constructs pipe paths with `OSL_PIPE_`](https://raw.githubusercontent.com/LibreOffice/core/master/sal/osl/w32/pipe.cxx).
Microsoft [requires the `LOCAL\` pipe namespace inside AppContainer](https://learn.microsoft.com/en-us/windows/win32/api/namedpipeapi/nf-namedpipeapi-createnamedpipew).
Creating an in-process UNO Desktop exits with `0xC0000005` on this machine.
Neither attempt enables network access or changes global renderer files.

From the repository root, use the service's uv environment to run the probes on Windows.
The configured LibreOffice Python and soffice executables must be installed under Program Files.
Set `EXTRACTION_UNO_PYTHON` only if that interpreter is elsewhere.

```powershell
uv run --project services/powerpoint-extraction python -m unittest discover -s services/powerpoint-extraction/probes -p test_confinement_windows.py -v
uv run --project services/powerpoint-extraction python services/powerpoint-extraction/probes/probe_windows_ipc.py --fixture services/powerpoint-extraction/evidence/windows/representative.pptx --worker services/powerpoint-extraction/src/powerpoint_extraction/uno_worker.py --output services/powerpoint-extraction/evidence/windows-ipc
```

`probe_windows_ipc.py` writes `native-windows-status.json` and `startup-error.txt`.
The [saved failure evidence](../evidence/windows-ipc/native-windows-status.json) records the actual conversion outcome and native errors.
`probe_windows_rendering.py` is the retained acceptance probe for a future fix.
It requires actual page and group PNGs and does not currently pass inside AppContainer.
Profile creation needs the normal signed-in Windows account.
The restricted Codex execution profile cannot create the profile on this machine, so the recorded probes ran outside that profile.
