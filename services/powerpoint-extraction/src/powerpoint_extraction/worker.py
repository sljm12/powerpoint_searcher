from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress
from functools import partial

from .models import Claim, ExtractionFailure, Extractor, Progress
from .store import Store


class Worker:
    def __init__(self, store: Store, extractor: Extractor, ready: Callable[[], bool], timeout: float = 180) -> None:
        self.store, self.extractor, self.ready, self.timeout = store, extractor, ready, timeout
        self.wake = asyncio.Event()
        self.stopping = False
        self.active: Claim | None = None

    async def run(self) -> None:
        while not self.stopping:
            self.store.cleanup()
            claim = self.store.claim() if self.ready() else None
            if claim is None:
                self.wake.clear()
                with suppress(TimeoutError):
                    await asyncio.wait_for(self.wake.wait(), 0.1)
                continue
            self.active = claim
            progress: Progress = partial(self.store.update_progress, claim)
            task = asyncio.create_task(
                self.extractor(
                    claim.source_path,
                    claim.output_path,
                    progress,
                    claim.job.include_notes,
                    claim.job.notes_text_limit,
                )
            )
            started = asyncio.get_running_loop().time()
            timed_out = False
            try:
                while not task.done():
                    if self.stopping or not self.store.is_current(claim):
                        task.cancel()
                        break
                    if asyncio.get_running_loop().time() - started >= self.timeout:
                        timed_out = True
                        task.cancel()
                        break
                    await asyncio.wait({task}, timeout=0.05)
                try:
                    result = await task
                except asyncio.CancelledError:
                    if timed_out:
                        self.store.fail(claim, "conversion_timeout", "Conversion exceeded its time limit; retry or simplify the deck.")
                    elif self.stopping:
                        self.store.fail(claim, "worker_stopped", "Service stopped during processing; retry this job.")
                else:
                    if timed_out:
                        self.store.fail(claim, "conversion_timeout", "Conversion exceeded its time limit; retry or simplify the deck.")
                    elif self.store.is_current(claim):
                        self.store.publish(claim, result)
            except ExtractionFailure as error:
                self.store.fail(claim, error.code, error.message, error.retryable)
            except Exception:
                self.store.fail(claim, "conversion_failed", "Conversion failed; verify the deck and renderer, then retry.")
            finally:
                if not task.done():
                    task.cancel()
                    with suppress(asyncio.CancelledError, Exception):
                        await task
                self.active = None
                self.store.cleanup()

    async def stop(self) -> None:
        self.stopping = True
        self.wake.set()
