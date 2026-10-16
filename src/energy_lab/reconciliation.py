"""Durable, fail-closed journal for non-atomic HA command delivery.

Only one runner may own this file. Stop it before operator clearance. A crash
after the pre-POST checkpoint deliberately leaves the site quarantined.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import threading
import uuid


def _now():
    return datetime.now(timezone.utc).isoformat()


class ReconciliationStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Parallel delivery worker threads checkpoint concurrently for distinct
        # sites; the journal read-modify-write must stay serialized.
        self._lock = threading.Lock()
        self._data = self._read()

    def _read(self):
        if not self.path.exists():
            return {"version": 1, "pending": {}, "clearances": []}
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if data.get("version") != 1 or not isinstance(data.get("pending"), dict) or not isinstance(data.get("clearances"), list):
            raise ValueError("invalid_reconciliation_state")
        return data

    def _save(self):
        temp = self.path.with_name(self.path.name + f".{os.getpid()}.{uuid.uuid4().hex}.tmp")
        try:
            with temp.open("x", encoding="utf-8") as stream:
                json.dump(self._data, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, self.path)
            if os.name != "nt":
                descriptor = os.open(self.path.parent, os.O_RDONLY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
        finally:
            temp.unlink(missing_ok=True)

    def pending(self):
        with self._lock:
            self._data = self._read()
            return dict(self._data["pending"])

    def checkpoint(self, site, suffix, device_time, steps):
        # A successful durable checkpoint MUST precede the corresponding POST.
        with self._lock:
            if site not in self._data["pending"]:
                self._data["pending"][site] = {
                    "incident_id": uuid.uuid4().hex, "site": site, "suffix": suffix,
                    "device_time": device_time, "created_at": _now(),
                    "reason": "delivery_in_progress_or_unknown"}
            self._data["pending"][site]["steps"] = [dict(step) for step in steps]
            self._save()

    def complete(self, site):
        with self._lock:
            if site in self._data["pending"]:
                del self._data["pending"][site]
                self._save()

    def clear(self, site, incident_id, operator, evidence_path, reason):
        with self._lock:
            self._data = self._read()
            pending = self._data["pending"].get(site)
            if pending is None or pending["incident_id"] != incident_id:
                raise ValueError("incident_id_mismatch_or_not_pending")
            if not operator.strip() or not reason.strip():
                raise ValueError("operator_and_reason_required")
            evidence = Path(evidence_path)
            if not evidence.is_file():
                raise ValueError("readback_evidence_file_required")
            self._data["clearances"].append({"incident_id": incident_id, "site": site,
                                             "operator": operator, "reason": reason,
                                             "evidence": evidence.as_posix(), "cleared_at": _now()})
            del self._data["pending"][site]
            self._save()
