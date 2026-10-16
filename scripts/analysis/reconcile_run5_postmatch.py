"""run5 post-match reconciliation: timing (9.5s), freeze, per-site status."""
import csv
import json
from collections import Counter
from pathlib import Path

BASE = Path("outputs/local_mock_20261016_run5")


def fnum(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def main() -> None:
    rows = list(csv.DictReader(open(BASE / "audit.csv", encoding="utf-8")))

    # ---- timing ----
    elapsed = [fnum(r["round_elapsed_seconds"]) for r in rows]
    elapsed = [e for e in elapsed if e is not None]
    elapsed.sort()
    n = len(elapsed)
    import statistics
    print(f"=== TIMING (round_elapsed_seconds, n={n}) ===")
    print(f"  mean={statistics.mean(elapsed):.2f} p50={elapsed[n//2]:.2f} "
          f"p90={elapsed[int(n*0.9)]:.2f} p95={elapsed[int(n*0.95)]:.2f} max={elapsed[-1]:.2f}")
    over = sum(1 for e in elapsed if e > 9.5)
    over5 = sum(1 for e in elapsed if e > 5.0)
    print(f"  >9.5s (deadline): {over} ({100*over/n:.2f}%)   >5s: {over5} ({100*over5/n:.2f}%)")

    # alignment elapsed
    ael = [fnum(r["alignment_elapsed_seconds"]) for r in rows]
    ael = [a for a in ael if a is not None]
    ael.sort()
    print(f"  alignment_elapsed: p50={ael[len(ael)//2]:.2f} p90={ael[int(len(ael)*0.9)]:.2f} max={ael[-1]:.2f}")

    # ---- freeze / halt detection ----
    print("=== FREEZE / HALT / BLOCKED detection ===")
    halt_fields = ("status", "reason", "execution_safety_rejection", "guard_action")
    freeze_rows = []
    for r in rows:
        blob = " ".join(r.get(k, "") or "" for k in halt_fields).lower()
        if any(k in blob for k in ("halt", "blocked", "freeze", "delivery_unknown", "delivery_blocked")):
            freeze_rows.append(r)
    print(f"  rows with halt/blocked/freeze keywords: {len(freeze_rows)}")
    for r in freeze_rows[:5]:
        print(f"    site={r['site']} round={r['round_generation']} status={r['status']} reason={r['reason']}")

    # ---- per-site status distribution ----
    print("=== PER-SITE STATUS ===")
    for site in ("1", "2", "3"):
        srows = [r for r in rows if r["site"] == site]
        statuses = Counter(r["status"] for r in srows)
        reasons = Counter(r["reason"] for r in srows if r["reason"])
        attrs = Counter(r["attribution"] for r in srows)
        print(f"  site {site} (n={len(srows)}):")
        for st, c in statuses.most_common(8):
            print(f"    {st}: {c}")
        for rs, c in reasons.most_common(5):
            print(f"      reason[{rs}]: {c}")
        print(f"    attribution: {dict(attrs)}")

    # ---- fallback / limit_reached ----
    print("=== FALLBACK / LIMIT_REACHED ===")
    for site in ("1", "2", "3"):
        srows = [r for r in rows if r["site"] == site]
        fb = sum(1 for r in srows if "fallback" in (r["status"] or "").lower() or "fallback" in (r["reason"] or "").lower())
        lr = sum(1 for r in srows if "limit_reached" in (r["solver_status"] or "").lower() or "limit_reached" in (r["reason"] or "").lower())
        print(f"  site {site}: fallback={fb} limit_reached={lr} / {len(srows)}")


if __name__ == "__main__":
    main()
