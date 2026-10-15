"""Bounded, single-slot solve scheduling; worker threads never receive HA clients."""
from __future__ import annotations

import queue
import threading
import time

_SOLVE_SLOT = threading.BoundedSemaphore(1)


def solve_until(deadline: float, operation):
    """Return (candidate, reason, elapsed); late candidates are never returned.

    A timed-out daemon solver may finish later, but holds the single slot until
    then. Subsequent sites/rounds refuse to start another solve meanwhile.
    """
    started = time.monotonic()
    remaining = deadline - started
    if remaining <= 0:
        return None, "round_deadline_exceeded", 0.0
    if not _SOLVE_SLOT.acquire(blocking=False):
        return None, "previous_solver_still_running", time.monotonic() - started
    result = queue.Queue(maxsize=1)

    def worker():
        try:
            result.put((True, operation()))
        except Exception as exc:
            result.put((False, exc))
        finally:
            _SOLVE_SLOT.release()

    threading.Thread(target=worker, daemon=True, name="bounded-milp-solve").start()
    try:
        ok, value = result.get(timeout=remaining)
    except queue.Empty:
        return None, "solver_deadline_exceeded", time.monotonic() - started
    elapsed = time.monotonic() - started
    if time.monotonic() >= deadline:
        return None, "solver_deadline_exceeded", elapsed
    if not ok:
        raise value
    return value, None, elapsed
