"""Synchronous write deadline/ledger. Never retry or background an HA POST."""
from contextvars import ContextVar
from dataclasses import dataclass, field
import time

ACTIVE_WRITE = ContextVar("active_ha_write", default=None)


@dataclass
class WriteTransaction:
    deadline: float
    current: object = None
    steps: list = field(default_factory=list)
    store: object = None
    site: str = ""
    suffix: str = ""
    device_time: str = ""

    def checkpoint(self, steps):
        if self.store is not None:
            self.store.checkpoint(self.site, self.suffix, self.device_time, steps)

    def remaining(self):
        if self.current is not None and not self.current():
            raise TimeoutError("superseded_write_transaction")
        value = self.deadline - time.monotonic()
        if value <= 0:
            raise TimeoutError("write_deadline_exceeded")
        return value

    def reserve(self, seconds):
        """Raise TimeoutError unless at least ``seconds`` remain before the deadline.

        Used to refuse a delivery *before* its first POST: starting a multi-step
        write with an insufficiently small budget would otherwise time out part
        way through and leave an uncertain (partially applied) command that has
        to be quarantined. Refusing up front is fail-closed and leaves no
        uncertainty.
        """
        remaining = self.remaining()
        if remaining < seconds:
            raise TimeoutError("insufficient_budget_for_delivery")
        return remaining

    def gap(self, seconds):
        remaining = self.remaining()
        if remaining <= seconds:
            raise TimeoutError("insufficient_budget_for_mode_gap")
        time.sleep(seconds)
        self.remaining()
