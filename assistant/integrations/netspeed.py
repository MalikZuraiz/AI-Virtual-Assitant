"""Internet speed test, without adding a speedtest dependency.

Uses Cloudflare's public speed endpoints (``speed.cloudflare.com``) - free,
keyless, anycast so the nearest edge answers, and they are the same endpoints
Cloudflare's own web speed test uses. That avoids pulling in ``speedtest-cli``,
which scrapes Ookla's server list and breaks whenever that changes.

Measured honestly: latency is the median of several small round trips (not
the best one), and throughput is measured over the transfer itself with an
escalating payload so a fast line is not under-reported by a too-small
download.
"""
from __future__ import annotations

import logging
import statistics
import time
from dataclasses import dataclass
from typing import Callable

import requests

logger = logging.getLogger("assistant.netspeed")

DOWN_URL = "https://speed.cloudflare.com/__down"
UP_URL = "https://speed.cloudflare.com/__up"
TRACE_URL = "https://speed.cloudflare.com/cdn-cgi/trace"

#: Escalating download sizes, in bytes. Stops early once a run takes long
#: enough to be a trustworthy sample.
DOWN_SIZES = (1_000_000, 10_000_000, 25_000_000)
MIN_SECONDS = 2.0
UPLOAD_BYTES = 2_000_000


@dataclass
class SpeedResult:
    download_mbps: float = 0.0
    upload_mbps: float = 0.0
    latency_ms: float = 0.0
    jitter_ms: float = 0.0
    server: str = ""
    error: str = ""

    def summary(self) -> str:
        if self.error:
            return f"Speed test failed: {self.error}"
        lines = [
            f"Download  {self.download_mbps:6.1f} Mbps",
            f"Upload    {self.upload_mbps:6.1f} Mbps",
            f"Ping      {self.latency_ms:6.0f} ms   (jitter {self.jitter_ms:.0f} ms)",
        ]
        if self.server:
            lines.append(f"Via Cloudflare {self.server}")
        return "\n".join(lines)


def _mbps(byte_count: int, seconds: float) -> float:
    if seconds <= 0:
        return 0.0
    return (byte_count * 8) / seconds / 1_000_000


def measure_latency(session: requests.Session, samples: int = 6) -> tuple[float, float]:
    """Median round-trip and jitter, in milliseconds."""
    timings: list[float] = []
    for _ in range(samples):
        started = time.perf_counter()
        try:
            session.get(DOWN_URL, params={"bytes": 1}, timeout=10).content
        except requests.RequestException:
            continue
        timings.append((time.perf_counter() - started) * 1000)
    if not timings:
        return 0.0, 0.0
    jitter = statistics.pstdev(timings) if len(timings) > 1 else 0.0
    return statistics.median(timings), jitter


def measure_download(session: requests.Session, on_progress: Callable[[str], None] | None = None) -> float:
    best = 0.0
    for size in DOWN_SIZES:
        if on_progress:
            on_progress(f"Downloading {size // 1_000_000 or 1} MB...")
        started = time.perf_counter()
        received = 0
        try:
            with session.get(DOWN_URL, params={"bytes": size}, stream=True, timeout=60) as response:
                response.raise_for_status()
                for chunk in response.iter_content(chunk_size=65536):
                    received += len(chunk)
        except requests.RequestException as exc:
            logger.info("Download sample failed: %s", exc)
            break
        elapsed = time.perf_counter() - started
        best = max(best, _mbps(received, elapsed))
        # A sample long enough to be meaningful - no need to pull more data.
        if elapsed >= MIN_SECONDS:
            break
    return best


def measure_upload(session: requests.Session, on_progress: Callable[[str], None] | None = None) -> float:
    if on_progress:
        on_progress(f"Uploading {UPLOAD_BYTES // 1_000_000} MB...")
    payload = b"0" * UPLOAD_BYTES
    started = time.perf_counter()
    try:
        session.post(UP_URL, data=payload, timeout=60)
    except requests.RequestException as exc:
        logger.info("Upload sample failed: %s", exc)
        return 0.0
    return _mbps(len(payload), time.perf_counter() - started)


def _server(session: requests.Session) -> str:
    try:
        text = session.get(TRACE_URL, timeout=8).text
    except requests.RequestException:
        return ""
    fields = dict(
        line.split("=", 1) for line in text.splitlines() if "=" in line
    )
    return fields.get("colo", "")


def run_speed_test(on_progress: Callable[[str], None] | None = None) -> SpeedResult:
    """Full test. Takes roughly 10-25 seconds depending on the line."""
    result = SpeedResult()
    session = requests.Session()
    try:
        if on_progress:
            on_progress("Measuring latency...")
        result.latency_ms, result.jitter_ms = measure_latency(session)
        if result.latency_ms == 0:
            result.error = "couldn't reach the test server - are you online?"
            return result
        result.server = _server(session)
        result.download_mbps = measure_download(session, on_progress)
        result.upload_mbps = measure_upload(session, on_progress)
    except Exception as exc:  # noqa: BLE001 - reported, never raised into chat
        logger.exception("Speed test failed")
        result.error = str(exc)
    finally:
        session.close()
    return result
