from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Battery:
    capacity_kwh: float = 14.0
    soc: float = 50.0
    max_power_w: float = 5000.0
    charge_efficiency: float = 0.95

    def step(self, flow: str, power_w: float, hours: float = 1.0) -> float:
        power_w = max(0.0, min(self.max_power_w, float(power_w)))
        if flow == "charge":
            self.soc = min(100.0, self.soc + power_w * hours / 1000 * self.charge_efficiency / self.capacity_kwh * 100)
            return power_w
        if flow == "discharge":
            self.soc = max(0.0, self.soc - power_w * hours / 1000 / self.capacity_kwh * 100)
            return power_w
        return 0.0


def dispatch(import_price: float, export_price: float, solar_w: float, load_w: float, soc: float,
             future_import: list[float] | None = None, capacity_kwh: float = 14.0,
             max_power_w: float = 5000.0) -> tuple[str, float, str]:
    """Transparent rule baseline; it is deliberately conservative and explainable."""
    future_import = future_import or []
    low = min([import_price, *future_import])
    high = max([import_price, *future_import])
    surplus = solar_w - load_w
    if surplus > 100 and soc < 98:
        return "charge", min(max_power_w, surplus), "光伏余量可直接进入电池，优先避免购电或负价外送"
    if export_price < 0 and soc < 98 and surplus > 0:
        return "charge", min(max_power_w, surplus), "当前外送电价为负，避免向电网送电"
    if import_price <= low + 1e-9 and soc < 90:
        return "charge", max_power_w, "当前处于预测窗口低购电价，补充未来需求"
    if import_price >= high - 1e-9 and soc > 15:
        return "discharge", min(max_power_w, max(load_w, 0.0)), "当前购电价处于预测窗口高位，优先用电池供负载"
    return "idle", 0.0, "当前价格、光伏和 SOC 不支持有利的主动充放电"
