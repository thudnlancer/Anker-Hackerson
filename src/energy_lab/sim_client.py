from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import requests
from .read_budget import timed_read


@dataclass
class SimulatorClient:
    base: str
    username: str
    password: str
    timeout: float = 10.0

    def __post_init__(self) -> None:
        self.base = self.base.rstrip("/")
        self.token: str | None = None

    def login(self) -> str:
        def read(timeout):
            r = requests.post(
                f"{self.base}/api/auth/login",
                json={"username": self.username, "password": self.password},
                timeout=timeout,
            )
            r.raise_for_status()
            return r.json()
        data = timed_read("sim.auth/login", read, self.timeout)
        if not data.get("token"):
            raise RuntimeError(f"simulator login did not return token: {data}")
        self.token = data["token"]
        return self.token

    def get(self, path: str, **params: Any) -> Any:
        if not self.token:
            self.login()
        def read(timeout):
            r = requests.get(
                f"{self.base}{path}",
                params=params or None,
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=timeout,
            )
            r.raise_for_status()
            return r.json()
        return timed_read(f"sim{path}", read, self.timeout)

    def prices(self) -> Any:
        return self.get("/api/price")

    def current_price(self) -> Any:
        return self.get("/api/price/current")

    def weather(self, day: str = "today", site: int = 1) -> Any:
        return self.get(f"/api/weather/{day}", site=site)

    def contest_history(self, site: int = 1) -> Any:
        return self.get("/api/contest/history", site=site)
