"""Auditable, explicitly enabled HA service submission; no device ACK is implied.

The SOP control is three separate service calls, not an atomic transaction. In
particular, changing the mode or flow can briefly expose an old power setpoint.
The caller owns contest, freshness and capability checks, and can repeat them
through ``before_step`` immediately before every HTTP call. This module never
authenticates, retries, compensates a partial submission, or reads telemetry.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import re
import time
from typing import Callable, Literal
from urllib.parse import urlsplit

import requests

from .config import canonical_suffix, is_placeholder
from .ha_client import HAClient

_IDENTIFIER = re.compile(r"^[A-Za-z0-9_.:-]{1,120}$")
_ENTITY_SUFFIX = re.compile(r"^anker_solix_solarbank_max_ac_[a-z0-9_-]+$")
_SELECT_VALUES = frozenset({"third_party_control", "self_consumption", "charge", "discharge"})


def _aware(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps require an explicit timezone")
    return value.astimezone(timezone.utc)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _json_value(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    return value


@dataclass(frozen=True)
class DispatchCommand:
    run_id: str
    site_id: str
    decision_id: str
    instruction_id: str
    suffix: str
    issued_device_time: datetime
    flow: Literal["charge", "discharge"]
    target_w: float
    semantics: Literal["battery_charge_target", "grid_export_target"]

    def __post_init__(self) -> None:
        for name in ("run_id", "site_id", "decision_id", "instruction_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
                raise ValueError("invalid " + name)
        suffix = canonical_suffix(self.suffix)
        if not _ENTITY_SUFFIX.fullmatch(suffix):
            raise ValueError("invalid station suffix")
        object.__setattr__(self, "suffix", suffix)
        object.__setattr__(self, "issued_device_time", _aware(self.issued_device_time))
        if self.flow not in {"charge", "discharge"}:
            raise ValueError("dispatch supports only explicit charge/discharge commands")
        expected = "battery_charge_target" if self.flow == "charge" else "grid_export_target"
        if self.semantics != expected:
            raise ValueError("command flow and target semantics disagree")
        if (isinstance(self.target_w, bool) or not isinstance(self.target_w, (int, float))
                or not math.isfinite(self.target_w) or not 0 <= self.target_w <= 5000):
            raise ValueError("target_w must be finite and within the SOP 0..5000 W limit")
        # Reject out-of-range commands rather than silently clamp a saved plan.
        object.__setattr__(self, "target_w", float(self.target_w))


@dataclass(frozen=True)
class ServiceStep:
    index: int
    domain: str
    service: str
    entity_id: str
    field: str
    value: str | float


@dataclass(frozen=True)
class ServiceStepReceipt:
    step: ServiceStep
    started_wall_time: datetime
    completed_wall_time: datetime
    request_sent: bool
    outcome: Literal["accepted", "rejected", "unknown", "not_sent"]
    http_status: int | None = None
    error_type: str | None = None
    changed_entities_count: int | None = None
    target_entity_returned: bool | None = None
    target_state: str | float | None = None
    response_matches_requested_state: bool | None = None


@dataclass(frozen=True)
class DispatchReceipt:
    command: DispatchCommand
    issued_wall_time: datetime
    completed_wall_time: datetime
    status: Literal["not_sent", "blocked", "accepted", "partial_failure", "failed"]
    steps: tuple[ServiceStepReceipt, ...]
    services_expected: int = 3

    @property
    def services_attempted(self) -> int:
        return sum(step.request_sent for step in self.steps)

    @property
    def services_completed(self) -> int:
        return sum(step.outcome == "accepted" for step in self.steps)

    @property
    def uncertain_side_effects(self) -> bool:
        """A failed transport/protocol step might already have changed HA."""
        return any(step.request_sent and step.outcome == "unknown" for step in self.steps)

    def to_dict(self) -> dict:
        data = _json_value(asdict(self))
        data.update(services_attempted=self.services_attempted,
                    services_completed=self.services_completed,
                    uncertain_side_effects=self.uncertain_side_effects,
                    evidence_scope="ha_service_submission")
        return data

    def as_submission_receipt(self, *, source_ref: str = "energy_lab.ha_dispatch:sop-v1") -> dict:
        """Export only a complete accepted submission to the explanation API.

        This is sender-generated association evidence. A later coherent telemetry
        observation is still required by ``energy_explain.execution``.
        """
        if (self.status != "accepted" or self.services_expected != 3
                or tuple(item.step for item in self.steps) != _steps(self.command)
                or len(self.steps) != self.services_expected
                or self.services_completed != self.services_expected
                or any(not step.request_sent or step.http_status is None
                       or not 200 <= step.http_status < 300 for step in self.steps)):
            raise ValueError("only a complete accepted submission can be exported")
        if not isinstance(source_ref, str) or not source_ref.strip() or len(source_ref) > 500:
            raise ValueError("source_ref must be nonempty and at most 500 characters")
        command = self.command
        identity = [command.run_id, command.site_id, command.decision_id, command.instruction_id]
        event_id = "submission-" + hashlib.sha256(json.dumps(identity).encode()).hexdigest()[:40]
        return {"run_id": command.run_id, "site_id": command.site_id,
                "decision_id": command.decision_id, "instruction_id": command.instruction_id,
                "event_id": event_id, "issued_device_time": command.issued_device_time.isoformat(),
                "issued_wall_time": self.issued_wall_time.isoformat(),
                "completed_wall_time": self.completed_wall_time.isoformat(),
                "mode": "third_party_control", "flow": command.flow,
                "target_kw": command.target_w / 1000, "semantics": command.semantics,
                "source_ref": source_ref, "sender_status": "accepted",
                "raw_receipt": {"http_status": self.steps[-1].http_status,
                                "services_expected": self.services_expected,
                                "services_completed": self.services_completed}}


def _steps(command: DispatchCommand) -> tuple[ServiceStep, ...]:
    suffix = command.suffix
    return (
        ServiceStep(0, "select", "select_option",
                    f"select.{suffix}_operating_mode_device_runs_by_command_in_third_party_controlled",
                    "option", "third_party_control"),
        ServiceStep(1, "select", "select_option", f"select.{suffix}_grid_flow", "option", command.flow),
        ServiceStep(2, "number", "set_value", f"number.{suffix}_power_control", "value", command.target_w),
    )


def _response_evidence(body, step: ServiceStep) -> dict:
    if not isinstance(body, list) or any(not isinstance(item, dict) for item in body):
        raise ValueError("HA service response must be a JSON list of state objects")
    matching = [item for item in body if item.get("entity_id") == step.entity_id]
    evidence = {"changed_entities_count": len(body), "target_entity_returned": bool(matching),
                "target_state": None, "response_matches_requested_state": None}
    # An unchanged service call may return []; this is still a submitted HTTP
    # request, not proof that telemetry has changed. Never save arbitrary state
    # text, other entities, response bodies, context objects or auth headers.
    if matching:
        raw_state = matching[-1].get("state")
        if step.domain == "select":
            value = raw_state if isinstance(raw_state, str) and raw_state in _SELECT_VALUES else None
        else:
            try:
                value = float(raw_state) if not isinstance(raw_state, bool) else None
            except (ValueError, TypeError, OverflowError):
                value = None
            if value is not None and not math.isfinite(value):
                value = None
        evidence["target_state"] = value
        evidence["response_matches_requested_state"] = (
            value == step.value if step.domain == "select" else
            value is not None and abs(value - step.value) <= 0.001)
    return evidence


def dispatch_command(client: HAClient, command: DispatchCommand, *, live: bool = False,
                     before_step: Callable[[DispatchCommand, int], bool] | None = None,
                     mode_gap_seconds: float = 1.0, post=None, clock=None, sleep=None) -> DispatchReceipt:
    """Submit one instruction once, stopping at the first failed/blocked step.

    ``live=True`` explicitly enables three real service calls. A guard must
    return exactly ``True`` to allow its step; errors are recorded by class only.
    HTTP 2xx with a well-formed HA response is submission acceptance, irrespective
    of whether the response includes a changed entity. All device confirmation is
    external. ``post``, ``clock`` and ``sleep`` allow deterministic offline tests.
    """
    if type(live) is not bool:
        raise ValueError("live must be an explicit boolean")
    if not isinstance(command, DispatchCommand):
        raise TypeError("command must be a DispatchCommand")
    if (isinstance(mode_gap_seconds, bool) or not isinstance(mode_gap_seconds, (int, float))
            or not math.isfinite(mode_gap_seconds) or mode_gap_seconds < 0):
        raise ValueError("mode_gap_seconds must be finite and nonnegative")
    clock = clock or _now
    issued = _aware(clock())
    if not live:
        return DispatchReceipt(command, issued, issued, "not_sent", ())
    base = urlsplit(client.base)
    if (base.scheme not in {"http", "https"} or not base.hostname or base.username or base.password
            or base.query or base.fragment or is_placeholder(client.token)):
        raise ValueError("HA connection requires an HTTP(S) base and a separate token")
    post = post or requests.post
    sleep = sleep or time.sleep
    receipts = []
    for step in _steps(command):
        if step.index == 1:
            sleep(mode_gap_seconds)
        started = _aware(clock())
        if before_step is not None:
            try:
                allowed = before_step(command, step.index) is True
                guard_error = None if allowed else "guard_rejected"
            except Exception as exc:
                allowed, guard_error = False, type(exc).__name__
            if not allowed:
                receipts.append(ServiceStepReceipt(step, started, _aware(clock()), False, "not_sent",
                                                   error_type=guard_error))
                break
        try:
            response = post(f"{client.base}/api/services/{step.domain}/{step.service}",
                            headers=client.headers, json={"entity_id": step.entity_id, step.field: step.value},
                            timeout=client.timeout, allow_redirects=False)
        except Exception as exc:
            receipts.append(ServiceStepReceipt(step, started, _aware(clock()), True, "unknown",
                                               error_type=type(exc).__name__))
            break
        status = response.status_code
        if type(status) is not int or not 100 <= status <= 599:
            receipts.append(ServiceStepReceipt(step, started, _aware(clock()), True, "unknown",
                                               error_type="invalid_http_status"))
            break
        if not 200 <= status < 300:
            # A server error may occur after a service has partly changed state.
            # Preserve the response status without claiming that no write landed.
            outcome = "unknown" if status >= 500 else "rejected"
            receipts.append(ServiceStepReceipt(step, started, _aware(clock()), True, outcome,
                                               http_status=status, error_type="http_error"))
            break
        try:
            evidence = _response_evidence(response.json(), step)
        except Exception as exc:
            receipts.append(ServiceStepReceipt(step, started, _aware(clock()), True, "unknown",
                                               http_status=status, error_type=type(exc).__name__))
            break
        receipts.append(ServiceStepReceipt(step, started, _aware(clock()), True, "accepted",
                                           http_status=status, **evidence))
    accepted = sum(item.outcome == "accepted" for item in receipts)
    status = ("accepted" if accepted == 3 else "partial_failure" if accepted else
              "blocked" if receipts and not receipts[-1].request_sent else "failed")
    return DispatchReceipt(command, issued, receipts[-1].completed_wall_time, status, tuple(receipts))
