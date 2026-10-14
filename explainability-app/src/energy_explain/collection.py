"""Wall-clock collector heartbeat contract; device clocks remain separate."""
from datetime import datetime, timezone
from typing import Literal

from pydantic import Field, model_validator

from .schemas import Contract, Identifier


class CollectionSite(Contract):
    status: Literal["validated", "blocked", "collected"]
    device_tick: datetime | None = None
    observed_at: datetime | None = None
    solver_status: Literal["candidate_only", "infeasible", "timeout", "failed", "blocked"] | None = None
    reason: Identifier | None = None
    error_type: Identifier | None = None
    failure_code: Literal['candidate_not_exportable','first_observation_mismatch','incomplete_input_horizon',
        'incomplete_solver_horizon','invalid_battery_parameters','invalid_identifier','invalid_input_kind',
        'invalid_numeric_value','invalid_timestamp','model_step_mismatch','negative_input_value',
        'negative_solver_power','nonfinite_numeric_value','optimizer_source_hash_mismatch',
        'solver_cost_mismatch','solver_timestamp_mismatch','timestamp_timezone_missing',
        'unsupported_command_mapping'] | None = None


class CollectionStatus(Contract):
    run_id: Identifier
    owner_id: Identifier
    state: Literal["running", "stopped", "degraded", "unknown"]
    reason: Identifier | None = None
    started_at: datetime
    updated_at: datetime
    last_success_at: datetime | None = None
    last_error_at: datetime | None = None
    last_error: Identifier | None = None
    error_type: Identifier | None = None
    poll_interval_seconds: float = Field(gt=0, le=3600)
    freshness_threshold_seconds: float = Field(gt=0, le=3600)
    heartbeat_expiry_seconds: float = Field(gt=0, le=3600)
    sites: dict[Identifier, CollectionSite] = Field(default_factory=dict, max_length=3)
    cycle: int = Field(default=0, ge=0)
    writes_performed: Literal[0] = 0

    @model_validator(mode="after")
    def clocks(self):
        for name in ("started_at", "updated_at", "last_success_at", "last_error_at"):
            value = getattr(self, name)
            if value is not None:
                if value.tzinfo is None or value.utcoffset() is None:
                    raise ValueError(f"{name} requires a timezone")
                setattr(self, name, value.astimezone(timezone.utc))
        if self.started_at > self.updated_at:
            raise ValueError("heartbeat predates collector start")
        if any(v is not None and v > self.updated_at for v in (self.last_success_at, self.last_error_at)):
            raise ValueError("collector outcome cannot postdate heartbeat")
        return self


def record_freshness(observed_at: str, status: dict | None, wall: datetime) -> str:
    observed = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
    age = (wall - observed).total_seconds()
    if age < -2:
        return "clock_unverified"
    if status is None:
        return "unknown"
    return "fresh" if age <= status["freshness_threshold_seconds"] else "stale"
