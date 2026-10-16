"""Debug explainability-app failures on Linux: terminal price value + network call origin."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "explainability-app", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

import requests

calls = []


def spy(name):
    def inner(*args, **kwargs):
        import traceback
        calls.append(name)
        print(f"NETWORK CALL: requests.{name}")
        traceback.print_stack(limit=12)
        raise AssertionError("network blocked")
    return inner


for n in ("get", "post", "request", "Session"):
    setattr(requests, n, spy(n))

from energy_lab.shadow import evaluate_snapshot, daily_points

prices = {day: {"date": date, "points": [
    {"start": f"{date}T{hour:02d}:00:00Z", "import_price": 0.3, "export_price": 0.1}
    for hour in range(24)]}
    for day, date in (("today", "2031-01-01"), ("tomorrow", "2031-01-02"))}
state = {"soc": "45", "solar_power": "400", "home_load": "1000",
         "device_time": "2031-01-01T00:15:00+00:00",
         "device_time_last_changed": "2026-10-13T00:00:04Z"}
snapshot = {"received_at": "2026-10-13T00:00:05Z", "prices": prices,
            "stations": {"1": {"state": state, "received_at": "2026-10-13T00:00:05Z",
                               "weather": {"unavailable": True}}}}

records = []
result = evaluate_snapshot(snapshot, now=snapshot["received_at"], solver_seconds=10,
                           evidence_sink=records.append,
                           evidence_identity={"run_id": "unit-shared", "input_kind": "synthetic"})
print("station status:", result["stations"]["1"]["status"])
print("records:", len(records))
if records:
    rec = records[0]
    tp = rec["snapshot"]["terminal_eur_kwh"]
    print("terminal_eur_kwh:", repr(tp), tp == 0.3)
    buy = rec["snapshot"]["import_eur_kwh"]
    print("buy types:", {type(x).__name__ for x in buy}, "unique values:", {repr(x) for x in buy})
    print("sum(buy)/96:", repr(sum(buy) / 96))

# now the live_control fixture path
print("\n=== live_control plan path ===")
from pathlib import Path
import tempfile
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from energy_explain.live_control import ControlJournal, MILPController
from energy_explain.store import Store

STATIONS = {str(site): {"suffix": f"anker_solix_solarbank_max_ac_unit_{site}"} for site in range(1, 4)}
DEVICE = datetime(2031, 1, 1, 0, 45, tzinfo=timezone.utc)


def snap(wall):
    p = {}
    for day, date in (("today", "2031-01-01"), ("tomorrow", "2031-01-02")):
        p[day] = {"date": date, "points": [
            {"start": f"{date}T{hour:02d}:00:00Z", "import_price": .3, "export_price": .1}
            for hour in range(24)]}
    return {"prices": p, "stations": {site: {
        "received_at": wall.isoformat(), "weather": {"unavailable": True},
        "state": {"soc": 50, "solar_power": 0, "home_load": 1000,
                  "device_time": DEVICE.isoformat(), "device_time_last_changed": wall.isoformat(),
                  "token": "NEVER-PERSIST-SNAPSHOT-SECRET"}}
        for site in STATIONS}}


with tempfile.TemporaryDirectory() as tmp:
    tmp_path = Path(tmp)
    wall = datetime.now(timezone.utc) - timedelta(seconds=1)
    clock = SimpleNamespace(now=wall + timedelta(milliseconds=100))
    store = Store(tmp_path / "decisions.sqlite")
    journal = ControlJournal(tmp_path / "control.sqlite", resource_key="http://unit.test:8123",
                             lock_directory=tmp_path / "locks")
    controller = MILPController(run_id="control-run", store=store, journal=journal, stations=STATIONS,
                                clock=lambda: clock.now, solver_seconds=10)
    try:
        planned = controller.plan(snap(wall))
        print("planned decisions:", len(planned["decisions"]))
    except AssertionError as exc:
        print("NETWORK ATTEMPTED:", exc)
