from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import requests


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
        r = requests.post(
            f"{self.base}/api/auth/login",
            json={"username": self.username, "password": self.password},
            timeout=self.timeout,
        )
        r.raise_for_status()
        data = r.json()
        if not data.get("token"):
            raise RuntimeError(f"simulator login did not return token: {data}")
        self.token = data["token"]
        return self.token

    def get(self, path: str, **params: Any) -> Any:
        if not self.token:
            self.login()
        r = requests.get(
            f"{self.base}{path}",
            params=params or None,
            headers={"Authorization": f"Bearer {self.token}"},
            timeout=self.timeout,
        )
        r.raise_for_status()
        return r.json()

    def prices(self) -> Any:
        return self.get("/api/price")

    def weather(self, day: str = "today", site: int = 1) -> Any:
        return self.get(f"/api/weather/{day}", site=site)

    def contest_history(self, site: int = 1) -> Any:
        return self.get("/api/contest/history", site=site)
