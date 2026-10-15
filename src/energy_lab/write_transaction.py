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

    def gap(self, seconds):
        remaining = self.remaining()
        if remaining <= seconds:
            raise TimeoutError("insufficient_budget_for_mode_gap")
        time.sleep(seconds)
        self.remaining()
