"""Versioned ingestion contract. Power kW, energy kWh, currency EUR."""
from __future__ import annotations

from datetime import datetime, timezone
from math import isclose
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator, model_serializer

NonNegative = Annotated[float, Field(ge=0)]
Positive = Annotated[float, Field(gt=0)]
Identifier = Annotated[str, Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9_.:-]+$")]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Units(Contract):
    power: Literal["kW"] = "kW"
    energy: Literal["kWh"] = "kWh"
    price: Literal["EUR/kWh"] = "EUR/kWh"
    cost: Literal["EUR"] = "EUR"


class Snapshot(Contract):
    device_time: datetime
    observed_at: datetime
    soc_percent: Annotated[float, Field(ge=0, le=100)]
    pv_kw: NonNegative
    load_kw: NonNegative
    capacity_kwh: Positive
    max_charge_kw: NonNegative
    max_discharge_kw: NonNegative
    step_hours: Annotated[float, Field(gt=0, le=24)] = 1
    soc_min_percent: Annotated[float, Field(ge=0, le=100)] = 0
    soc_max_percent: Annotated[float, Field(ge=0, le=100)] = 100
    charge_efficiency: Annotated[float, Field(gt=0, le=1)] = 0.95
    discharge_efficiency: Annotated[float, Field(gt=0, le=1)] = 1
    pv_forecast_kw: Annotated[list[NonNegative], Field(max_length=96)] = Field(default_factory=list)
    load_forecast_kw: Annotated[list[NonNegative], Field(max_length=96)] = Field(default_factory=list)
    import_eur_kwh: Annotated[list[float], Field(max_length=96)] = Field(default_factory=list)
    export_eur_kwh: Annotated[list[float], Field(max_length=96)] = Field(default_factory=list)
    terminal_eur_kwh: float = 0
    degradation_eur_kwh: NonNegative = 0
    sources: dict[str, Literal["observed", "predicted", "synthetic", "assumption", "unknown"]]
    history_mode: Literal["none", "live_history", "frozen_baseline"] = "none"
    cache_tail: datetime | None = None
    forecast_status: Literal["ready", "fallback", "unavailable"]
    fallback_reason: Annotated[str, Field(max_length=1000)] | None = None
    units: Units = Field(default_factory=Units)

    @model_validator(mode="after")
    def consistent(self):
        for field in ("device_time", "observed_at", "cache_tail"):
            value = getattr(self, field)
            if value is not None:
                if value.tzinfo is None or value.utcoffset() is None:
                    raise ValueError(f"{field} requires an explicit timezone")
                setattr(self, field, value.astimezone(timezone.utc))
        lengths = {len(seq) for seq in (self.pv_forecast_kw, self.load_forecast_kw,
                    self.import_eur_kwh, self.export_eur_kwh) if seq}
        if len(lengths) > 1:
            raise ValueError("available horizon arrays must have matching lengths")
        if not self.soc_min_percent <= self.soc_percent <= self.soc_max_percent:
            raise ValueError("initial SOC must lie within the saved model bounds")
        if not {"pv", "load", "pv_forecast", "load_forecast"}.issubset(self.sources):
            raise ValueError("sources must describe pv, load and both forecasts")
        if self.forecast_status != "ready" and not self.fallback_reason:
            raise ValueError("fallback/unavailable forecasts require a reason")
        if self.cache_tail is not None and self.cache_tail > self.device_time:
            raise ValueError("cache tail cannot be later than the decision device time")
        return self


class Action(Contract):
    kind: Literal["charge", "discharge", "hold"]
    power_kw: NonNegative
    control_semantics: Literal["battery_charge", "grid_charge", "grid_export_target", "self_consumption", "external"]

    @model_validator(mode="after")
    def semantics(self):
        expected = {"charge": {"battery_charge", "grid_charge", "external"},
                    "discharge": {"grid_export_target", "external"},
                    "hold": {"self_consumption", "external"}}
        if self.control_semantics not in expected[self.kind]:
            raise ValueError("action kind and control semantics disagree")
        return self


class Cost(Contract):
    purchase_eur: float
    export_revenue_eur: float
    degradation_eur: NonNegative
    terminal_credit_eur: float
    objective_eur: float

    @model_validator(mode="after")
    def reconciles(self):
        total = self.purchase_eur - self.export_revenue_eur + self.degradation_eur - self.terminal_credit_eur
        if not isclose(total, self.objective_eur, abs_tol=1e-6, rel_tol=1e-7):
            raise ValueError("cost components do not reconcile with objective_eur")
        return self


class FirstStep(Contract):
    charge_kw: NonNegative
    discharge_kw: NonNegative
    grid_import_kw: NonNegative
    grid_export_kw: NonNegative


class ReplayContext(Contract):
    upstream_source_sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    command_contract: Literal["battery", "sop"]
    first_step: FirstStep


class Provenance(Contract):
    origin: Literal["external_adapter", "historical_log", "model_validation"]
    input_kind: Literal["observed", "mixed", "synthetic", "incomplete"]
    references: Annotated[list[str], Field(max_length=20)] = Field(default_factory=list)
    note: Annotated[str, Field(max_length=2000)] = ""


class DecisionIn(Contract):
    schema_version: Literal[1] = 1
    run_id: Identifier
    site_id: Identifier
    decision_id: Identifier
    source_kind: Literal["demo", "external"]
    snapshot: Snapshot
    action: Action
    model_version: Annotated[str, Field(min_length=1, max_length=200)]
    policy_version: Annotated[str, Field(min_length=1, max_length=200)]
    solver_status: Literal["optimal", "feasible", "fallback", "failed", "unknown"]
    cost: Cost | None = None
    replay_context: ReplayContext | None = None
    provenance: Provenance | None = None

    @model_validator(mode="after")
    def explicit_replay_inputs(self):
        if self.replay_context:
            required = {"step_hours","soc_min_percent","soc_max_percent","charge_efficiency","discharge_efficiency",
                        "terminal_eur_kwh","degradation_eur_kwh","pv_forecast_kw","load_forecast_kw",
                        "import_eur_kwh","export_eur_kwh"}
            missing = required - self.snapshot.model_fields_set
            if missing: raise ValueError("replay requires explicit saved model inputs: "+", ".join(sorted(missing)))
        return self

    @model_serializer(mode="wrap")
    def compatible_serialization(self, handler):
        data = handler(self)
        # Keep previous schema-v1 records byte-identical when these optional fields are absent.
        for field in ("replay_context", "provenance"):
            if data.get(field) is None: data.pop(field, None)
        return data


class ExecutionEvent(Contract):
    event_id: Identifier
    instruction_id: Identifier
    kind: Literal["submitted", "confirmed", "failed"]
    issued_at: datetime  # DEVICE clock; wall-clock receipt is saved separately.
    observed_at: datetime | None = None  # Wall clock, not comparable to accelerated device time.
    device_time: datetime | None = None
    observed_mode: Annotated[str, Field(max_length=120)] | None = None
    observed_power_kw: NonNegative | None = None
    note: Annotated[str, Field(max_length=1000)] = ""

    @model_validator(mode="after")
    def timed(self):
        for field in ("issued_at", "observed_at", "device_time"):
            value = getattr(self, field)
            if value is not None:
                if value.tzinfo is None or value.utcoffset() is None:
                    raise ValueError(f"{field} requires a timezone")
                setattr(self, field, value.astimezone(timezone.utc))
        if self.kind == "confirmed":
            if self.device_time is None or self.observed_at is None or self.observed_mode is None or self.observed_power_kw is None:
                raise ValueError("confirmation requires observed_at, device_time, mode and power")
            if self.device_time < self.issued_at:
                raise ValueError("readback device_time predates instruction issued_at")
        return self


class SettlementIn(Contract):
    event_id: Identifier
    status: Literal["pending", "complete"]
    source_kind: Literal["demo", "official", "simulator_mock"]
    captured_at: datetime
    window_start: datetime
    window_end: datetime
    saving_settled_percent: float | None = None
    saving_settled_eur: float | None = None
    raw_fields: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def timed(self):
        for field in ("captured_at", "window_start", "window_end"):
            value = getattr(self, field)
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{field} requires a timezone")
            setattr(self, field, value.astimezone(timezone.utc))
        if self.window_end <= self.window_start:
            raise ValueError("settlement window must be increasing")
        if self.status == "pending" and (self.saving_settled_eur is not None or self.saving_settled_percent is not None):
            raise ValueError("pending settlement cannot report achieved savings")
        return self


class Intervention(Contract):
    first_charge_kw: NonNegative


class Question(Contract):
    decision_id: Annotated[str, Field(min_length=1, max_length=120)]
    question: Annotated[str, Field(min_length=1, max_length=500)]
