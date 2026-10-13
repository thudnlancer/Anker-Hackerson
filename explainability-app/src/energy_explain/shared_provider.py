"""Use the team's canonical optimizer. Never silently substitute the frozen historical copy."""
from __future__ import annotations

import hashlib
from pathlib import Path

from .providers import ModelOutcome
from .recovered_provider import RecoveredMilpProvider

MODEL_VERSION = "shared-milp-6e2b1ca5-cf1"
SOURCE_SHA256 = "6e2b1ca5e372f9d6a70bc5322ac3f699b417e4ab336df2a25c8a3add84b62d11"


class SharedMilpProvider(RecoveredMilpProvider):
    model_version = MODEL_VERSION
    source_sha256 = SOURCE_SHA256
    source_description = "直接调用共同仓库 energy_lab.rolling 的 MILP；共享源码哈希与记录绑定。"
    deployment_note = "本次反事实计算不下发 HA、不生成结算；实际执行与官方收益依据独立记录，云端部署和硬件回读需对应验收。"

    def optimizer(self):
        try:
            from energy_lab.rolling import optimize_window
        except ImportError as exc:
            raise ModelOutcome("unsupported", "共同策略源码未在当前 Python 路径中；请设置仓库 src 到 PYTHONPATH。") from exc
        return optimize_window

    def verify_source(self):
        import inspect
        path = Path(inspect.getfile(self.optimizer()))
        if hashlib.sha256(path.read_bytes()).hexdigest() != SOURCE_SHA256:
            raise ModelOutcome("failed", "共同策略源码已变更，与记录锁定的模型版本不一致。")

    def supports(self, decision):
        if not super().supports(decision): return False
        try:
            self.verify_source()
            return True
        except ModelOutcome:
            return False
