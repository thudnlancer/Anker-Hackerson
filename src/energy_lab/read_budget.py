"""Cooperative monotonic deadline for read-only HTTP calls and their telemetry."""
from contextvars import ContextVar
from dataclasses import dataclass, field
import time
import queue
import threading

from urllib3.util import Timeout

ACTIVE_READ_BUDGET = ContextVar("active_read_budget", default=None)
# At most this many bounded HTTP workers in flight in this process. Two slots
# let the acquisition round overlap the simulator read (sim.current + sim.prices,
# one shared SimulatorClient Session) with the HA snapshot read (a separate
# HAClient Session) without ever racing a single Session from two threads.
# A timed-out read still cannot pile up more background reads than this bound.
_READ_SLOT = threading.BoundedSemaphore(2)


@dataclass
class ReadBudget:
    seconds: float
    started: float = field(default_factory=time.monotonic)
    requests: list = field(default_factory=list)

    def remaining(self):
        remaining = self.seconds - (time.monotonic() - self.started)
        if remaining <= 0:
            raise TimeoutError("alignment_deadline_exceeded")
        return remaining


def timed_read(label, operation, timeout):
    """No retries here; never accept a response arriving after the round deadline.

    DNS/slow-drip can outlive socket timeouts. A daemon read worker bounds the
    caller wait; its result is discarded after deadline and the global slot
    stays occupied until that read exits. No control writes run in this worker.
    """
    budget = ACTIVE_READ_BUDGET.get()
    if budget is None:
        return operation(timeout)
    started = time.monotonic()
    record = {"request": label, "started_offset_seconds": started - budget.started}
    try:
        remaining = budget.remaining()
        record["timeout_seconds"] = min(float(timeout), remaining)
        request_timeout = Timeout(total=remaining, connect=min(float(timeout), remaining),
                                  read=min(float(timeout), remaining))
        if not _READ_SLOT.acquire(blocking=False):
            raise RuntimeError("previous_read_still_inflight")
        result_queue = queue.Queue(maxsize=1)

        def worker():
            try:
                result_queue.put((True, operation(request_timeout)))
            except Exception as exc:
                result_queue.put((False, exc))
            finally:
                _READ_SLOT.release()

        threading.Thread(target=worker, daemon=True, name="bounded-read").start()
        try:
            ok, result = result_queue.get(timeout=budget.remaining())
        except queue.Empty as exc:
            raise TimeoutError("alignment_deadline_exceeded") from exc
        if not ok:
            raise result
        budget.remaining()
        record["status"] = "ok"
        return result
    except Exception as exc:
        record["status"] = "error"
        record["error_type"] = type(exc).__name__
        raise
    finally:
        record["elapsed_seconds"] = time.monotonic() - started
        budget.requests.append(record)
