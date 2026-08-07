"""The worker queue everything slow goes through.

The hard requirement from the project brief (§3.2) is that the GUI thread
never blocks. So the GUI never runs work: it submits a :class:`Job`,
immediately echoes "running ..." into the chat, and gets the result later as
an event.

This module is deliberately **framework-agnostic** - no Qt import anywhere.
It is plain threads, a ``queue.Queue`` and a callback, which means the whole
backbone is unit-testable without spinning up an application, and a
different front-end (CLI, tray-only, web) could reuse it untouched. The Qt
adapter that turns these events into signals lives in
:mod:`assistant.ui.qt.bridge`, and is the *only* place the two worlds meet.
"""
from __future__ import annotations

import itertools
import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional

logger = logging.getLogger("assistant.jobs")


class JobState(str, Enum):
    QUEUED = "queued"
    STARTED = "started"
    PROGRESS = "progress"
    FINISHED = "finished"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class JobEvent:
    """One thing that happened to a job. Emitted from a worker thread."""

    state: JobState
    job_id: int
    title: str
    text: str = ""
    duration: float = 0.0

    @property
    def is_terminal(self) -> bool:
        return self.state in (JobState.FINISHED, JobState.FAILED, JobState.CANCELLED)


class JobHandle:
    """Passed to a job's function so it can report progress and check for cancel."""

    def __init__(self, job_id: int, title: str, emit: Callable[[JobEvent], None]) -> None:
        self.id = job_id
        self.title = title
        self._emit = emit
        self._cancel = threading.Event()

    def progress(self, text: str) -> None:
        if text:
            self._emit(JobEvent(JobState.PROGRESS, self.id, self.title, text))

    def cancel(self) -> None:
        self._cancel.set()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()


@dataclass(order=True)
class _QueuedJob:
    priority: int
    seq: int
    handle: Optional["JobHandle"] = field(compare=False, default=None)
    fn: Optional[Callable[[JobHandle], str]] = field(compare=False, default=None)
    #: Poison pill telling a worker to exit. Carried as a real queue item
    #: rather than ``None`` because a PriorityQueue has to order whatever it
    #: is given, and ``None < None`` raises.
    stop: bool = field(compare=False, default=False)


class JobRunner:
    """A small pool of worker threads pulling jobs off a priority queue.

    Priority exists so a two-second "what's the time" is not stuck behind a
    four-minute report generation. Same-priority jobs run in submission
    order, so two quick commands typed back-to-back stay in order.
    """

    def __init__(
        self,
        workers: int = 2,
        on_event: Optional[Callable[[JobEvent], None]] = None,
        name: str = "job",
    ) -> None:
        self._queue: queue.PriorityQueue[_QueuedJob] = queue.PriorityQueue()
        self._on_event = on_event
        self._seq = itertools.count()
        self._ids = itertools.count(1)
        self._handles: dict[int, JobHandle] = {}
        self._lock = threading.Lock()
        self._stopping = threading.Event()
        self._threads = [
            threading.Thread(target=self._loop, name=f"{name}-worker-{i + 1}", daemon=True)
            for i in range(max(1, workers))
        ]
        for thread in self._threads:
            thread.start()

    # -- submission -------------------------------------------------------
    def submit(
        self,
        title: str,
        fn: Callable[[JobHandle], str],
        priority: int = 5,
    ) -> int:
        """Queue ``fn`` to run on a worker. Returns the job id immediately."""
        job_id = next(self._ids)
        handle = JobHandle(job_id, title, self._emit)
        with self._lock:
            self._handles[job_id] = handle
        self._emit(JobEvent(JobState.QUEUED, job_id, title))
        self._queue.put(_QueuedJob(priority, next(self._seq), handle, fn))
        return job_id

    def cancel(self, job_id: int) -> bool:
        """Ask a running job to stop. Cooperative - the job must check."""
        with self._lock:
            handle = self._handles.get(job_id)
        if handle is None:
            return False
        handle.cancel()
        return True

    @property
    def pending(self) -> int:
        return self._queue.qsize()

    # -- lifecycle --------------------------------------------------------
    def shutdown(self, wait: bool = False, timeout: float = 2.0) -> None:
        self._stopping.set()
        for _ in self._threads:
            # Priority -1 so shutdown jumps ahead of anything still queued.
            self._queue.put(_QueuedJob(-1, next(self._seq), stop=True))
        if wait:
            for thread in self._threads:
                thread.join(timeout=timeout)

    # -- internals --------------------------------------------------------
    def _emit(self, event: JobEvent) -> None:
        if self._on_event is None:
            return
        try:
            self._on_event(event)
        except Exception:  # noqa: BLE001 - a broken listener must not kill the worker
            logger.exception("Job event listener raised")

    def _loop(self) -> None:
        while True:
            item = self._queue.get()
            try:
                if item.stop or item.fn is None or self._stopping.is_set():
                    return
                self._run_one(item)
            finally:
                self._queue.task_done()

    def _run_one(self, item: _QueuedJob) -> None:
        handle = item.handle
        started = time.monotonic()
        self._emit(JobEvent(JobState.STARTED, handle.id, handle.title))
        try:
            if handle.cancelled:
                self._emit(
                    JobEvent(JobState.CANCELLED, handle.id, handle.title, "Cancelled before it started.")
                )
                return
            result = item.fn(handle)
            elapsed = time.monotonic() - started
            state = JobState.CANCELLED if handle.cancelled else JobState.FINISHED
            self._emit(JobEvent(state, handle.id, handle.title, str(result or ""), elapsed))
        except Exception as exc:  # noqa: BLE001 - surfaced to the user in chat
            logger.exception("Job %r failed", handle.title)
            self._emit(
                JobEvent(
                    JobState.FAILED,
                    handle.id,
                    handle.title,
                    f"{type(exc).__name__}: {exc}",
                    time.monotonic() - started,
                )
            )
        finally:
            with self._lock:
                self._handles.pop(handle.id, None)
