"""Fix broken markdown links introduced by the merge; report replacement counts."""
import re
from pathlib import Path

FIXES = [
    ("docs/decisions/RUN5_DISPATCH_DEVIATION.md",
     r"\[run5 赛后利润归因\]\(\.\./experiments/run5_profit_attribution\.md\)（若存在）",
     "run5 赛后利润归因（文档未落盘）", 1),
    ("docs/integration/EXPLAINABILITY_COLLABORATION.md",
     r"\]\(explainability-app/docs/continuous-readonly-integration\.md\)",
     "](../../explainability-app/docs/continuous-readonly-integration.md)", 1),
    ("docs/integration/EXPLAINABILITY_COLLABORATION.md",
     r"\]\(explainability-app/docs/milp-control-integration\.md\)",
     "](../../explainability-app/docs/milp-control-integration.md)", 1),
    ("explainability-app/README.md",
     r"\]\(\.\./EXPLAINABILITY_COLLABORATION\.md\)",
     "](../docs/integration/EXPLAINABILITY_COLLABORATION.md)", 1),
    ("explainability-app/README.md",
     r"shared-milp-6e2b1ca5-cf1",
     "shared-milp-603de45c-cf1", 1),
    ("explainability-app/docs/continuous-readonly-integration.md",
     r"\]\(\.\./\.\./EXPLAINABILITY_COLLABORATION\.md",
     "](../../docs/integration/EXPLAINABILITY_COLLABORATION.md", 2),
    ("explainability-app/docs/milp-control-integration.md",
     r"\]\(\.\./\.\./EXPLAINABILITY_COLLABORATION\.md",
     "](../../docs/integration/EXPLAINABILITY_COLLABORATION.md", 1),
]

for path, pattern, replacement, expected in FIXES:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    new, count = re.subn(pattern, replacement, text)
    status = "OK" if count == expected else f"EXPECTED {expected}"
    print(f"{path}: replaced {count} ({status})")
    if count:
        p.write_text(new, encoding="utf-8", newline="")
