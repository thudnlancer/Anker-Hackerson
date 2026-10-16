import json
from pathlib import Path

log = Path(r"D:\Anker黑客松\实验环境\outputs\local_mock_20261016_run6\runner.log")
raw = log.read_bytes()
enc = "utf-16" if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-8"
lines = raw.decode(enc).splitlines()

target = None
for ln in lines:
    try:
        obj = json.loads(ln)
    except Exception:
        continue
    if obj.get("event") == "site_result" and obj.get("round_generation") == 6 and obj.get("site") == "1":
        target = obj
        break

if target is None:
    print("not found")
else:
    # print full metadata + operations
    print("=== metadata ===")
    for k in ("alignment_budget_seconds", "alignment_elapsed_seconds", "alignment_attempts",
              "alignment_stop_reason", "alignment_rejection_reason", "serial_solver_worst_seconds",
              "serial_tick_budget_floor_seconds", "serial_tick_fits_poll", "status"):
        print(f"  {k}: {target.get(k)}")
    print("\n=== alignment_requests ===")
    for rq in target.get("alignment_requests", []):
        print(f"  {rq}")
    print("\n=== alignment_operations ===")
    for op in target.get("alignment_operations", []):
        print(f"  attempt={op.get('attempt')} op={op.get('operation')} "
              f"status={op.get('status')} err={op.get('error_type')} "
              f"offset={op.get('started_offset_seconds'):.2f}s elapsed={op.get('elapsed_seconds'):.3f}s")
