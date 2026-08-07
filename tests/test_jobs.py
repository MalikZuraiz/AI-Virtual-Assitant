import threading
import time

from assistant.core.jobs import JobRunner, JobState


def _collect():
    events = []
    lock = threading.Lock()

    def on_event(event):
        with lock:
            events.append(event)

    return events, on_event


def _wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_a_job_runs_and_reports_its_result():
    events, on_event = _collect()
    runner = JobRunner(workers=1, on_event=on_event)
    try:
        runner.submit("greet", lambda handle: "hello")
        assert _wait_for(lambda: any(e.state is JobState.FINISHED for e in events))
        finished = [e for e in events if e.state is JobState.FINISHED][0]
        assert finished.text == "hello"
        assert finished.title == "greet"
    finally:
        runner.shutdown()


def test_a_failing_job_becomes_an_event_not_a_crash():
    events, on_event = _collect()
    runner = JobRunner(workers=1, on_event=on_event)
    try:
        runner.submit("boom", lambda handle: 1 / 0)
        assert _wait_for(lambda: any(e.state is JobState.FAILED for e in events))
        failed = [e for e in events if e.state is JobState.FAILED][0]
        assert "ZeroDivisionError" in failed.text
    finally:
        runner.shutdown()


def test_progress_is_reported_while_the_job_is_still_running():
    events, on_event = _collect()
    runner = JobRunner(workers=1, on_event=on_event)

    def work(handle):
        handle.progress("step 1")
        handle.progress("step 2")
        return "done"

    try:
        runner.submit("staged", work)
        assert _wait_for(lambda: any(e.state is JobState.FINISHED for e in events))
        progress = [e.text for e in events if e.state is JobState.PROGRESS]
        assert progress == ["step 1", "step 2"]
    finally:
        runner.shutdown()


def test_lower_priority_number_runs_first():
    order = []
    started = threading.Event()
    runner = JobRunner(workers=1)
    try:
        # Occupy the single worker so the next two genuinely queue up.
        runner.submit("blocker", lambda handle: started.wait(2) and None, priority=0)
        runner.submit("slow report", lambda handle: order.append("slow"), priority=9)
        runner.submit("quick lookup", lambda handle: order.append("quick"), priority=1)
        started.set()
        assert _wait_for(lambda: len(order) == 2)
        assert order == ["quick", "slow"]
    finally:
        runner.shutdown()


def test_cancellation_is_visible_to_the_running_job():
    events, on_event = _collect()
    runner = JobRunner(workers=1, on_event=on_event)
    seen = {}

    def work(handle):
        for _ in range(200):
            if handle.cancelled:
                seen["cancelled"] = True
                return "stopped early"
            time.sleep(0.01)
        return "ran to completion"

    try:
        job_id = runner.submit("long", work)
        time.sleep(0.1)
        assert runner.cancel(job_id) is True
        assert _wait_for(lambda: any(e.is_terminal for e in events))
        assert seen.get("cancelled") is True
    finally:
        runner.shutdown()


def test_shutdown_does_not_raise_with_jobs_still_queued():
    runner = JobRunner(workers=2)
    for i in range(5):
        runner.submit(f"job {i}", lambda handle: time.sleep(0.05))
    runner.shutdown(wait=True)
