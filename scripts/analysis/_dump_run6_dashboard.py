import json
from pathlib import Path

d = json.loads(Path(r"D:\Anker黑客松\实验环境\outputs\local_mock_20261016_run6\ended_snapshot.json").read_text(encoding="utf-8"))
res = d["dashboard"]["results"]
print("=== results summary keys ===")
for k in ("count", "best", "verify", "official_count", "mock_count", "best_mock"):
    print(f"  {k}: {json.dumps(res.get(k), ensure_ascii=False)}")
print("\n=== runs ===")
runs = res.get("runs", [])
print(f"run count: {len(runs)}")
for i, r in enumerate(runs):
    print(f"idx{i}: {json.dumps(r, ensure_ascii=False)}")
