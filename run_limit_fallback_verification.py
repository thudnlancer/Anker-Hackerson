"""C2 acceptance replay: 72 pilot cases + stress scenarios after the graded
fallback change. Read-only; frozen snapshots; no network or HA writes.

Quantifies: candidate availability (optimal / suboptimal-incumbent / explicit
self-use fallback), independent physical audit of every emitted plan
(exclusivity, power bounds, SOC bounds, energy/grid balance, SOP contract),
and mip_gap statistics for suboptimal candidates.
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from energy_lab import shadow  # noqa: E402
from run_irradiance_shadow import evaluate_pair_snapshot  # noqa: E402


class PlanRecorder:
    """Capture RollingPlans inside the live path without altering behavior.
    Plans are kept in call order; each snapshot produces one call per station
    in the same order as the results dict."""

    def __init__(self):
        self.sequence = []

    def __enter__(self):
        original = shadow.optimize_window

        def wrapper(times, *args, **kwargs):
            plan = original(times, *args, **kwargs)
            self.sequence.append(plan)
            return plan

        shadow.optimize_window = wrapper
        self._original = original
        return self

    def __exit__(self, *exc):
        shadow.optimize_window = self._original
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pilot-dir", default="outputs/irradiance_post_reboot_20261013")
    parser.add_argument("--budget", type=float, default=2.0)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    pilot = Path(args.pilot_dir)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=False)
    snapshots = sorted(pilot.glob("snapshot_*.json"))

    counts = {"optimal": 0, "suboptimal_incumbent": 0,
              "explicit_self_use_fallback": 0, "blocked_or_error": 0}
    gaps, audits_failed, rows = [], [], []
    pairs = []
    with PlanRecorder() as recorder:
        for snap_path in snapshots:
            snap = json.loads(snap_path.read_text(encoding="utf-8"))
            before = len(recorder.sequence)
            results, additions = evaluate_pair_snapshot(
                snap, pairs, now=snap["received_at"], solver_seconds=args.budget)
            pairs.extend(additions)
            plans_here = recorder.sequence[before:]
            assert len(plans_here) == len(results), "one solver call per station expected"
            it = int(snap_path.stem.split("_")[1])
            for (site, r), plan in zip(results.items(), plans_here):
                row = {"iteration": it, "site": site}
                if r.get("status") == "candidate_only":
                    key = "suboptimal_incumbent" if r.get("suboptimal_incumbent") else "optimal"
                    counts[key] += 1
                    if key == "suboptimal_incumbent":
                        gaps.append(r.get("mip_gap"))
                    # Audit against input-independent identities (exclusivity,
                    # power/SOC bounds, SOP import ban, energy dynamics) plus
                    # first-step grid balance against the observation.
                    state = snap["stations"][site]["state"]
                    pv_first = float(state["solar_power"])
                    load_first = float(state["home_load"])
                    p = plan.plan
                    c = p.charge_w.to_numpy(); d = p.discharge_w.to_numpy()
                    gi = p.grid_import_w.to_numpy(); ge = p.grid_export_w.to_numpy()
                    soc = p.soc_end.to_numpy()
                    energy = soc / 100 * 14.
                    previous = np.r_[float(state["soc"]) / 100 * 14., energy[:-1]]
                    viol = {
                        "simultaneous_charge_discharge_w": float(np.minimum(c, d).max()),
                        "simultaneous_import_export_w": float(np.minimum(gi, ge).max()),
                        "max_battery_power_w": float(max(c.max(), d.max())),
                        "min_soc_percent": float(soc.min()),
                        "max_soc_percent": float(soc.max()),
                        "max_energy_residual_kwh": float(np.abs(
                            energy - previous - (.95 * c - d) * .25 / 1000).max()),
                        "max_import_while_discharging_w": float(
                            gi[d > 1e-3].max() if (d > 1e-3).any() else 0.),
                    }
                    bad = (viol["simultaneous_charge_discharge_w"] > 1e-3
                           or viol["simultaneous_import_export_w"] > 1e-3
                           or viol["max_battery_power_w"] > 5000 + 1e-3
                           or viol["min_soc_percent"] < -1e-6
                           or viol["max_soc_percent"] > 100 + 1e-6
                           or viol["max_energy_residual_kwh"] > 1e-6
                           or viol["max_import_while_discharging_w"] > 1e-3)
                    # Grid balance at the observed first step.
                    grid0 = pv_first + gi[0] + d[0] - load_first - ge[0] - c[0]
                    viol["first_step_grid_residual_w"] = float(abs(grid0))
                    bad |= abs(grid0) > 1e-3
                    row.update({"class": key, "audit_pass": not bad, **viol})
                    if bad:
                        audits_failed.append(row)
                elif r.get("fallback") == "explicit_self_use_mode":
                    counts["explicit_self_use_fallback"] += 1
                    row["class"] = "explicit_self_use_fallback"
                    row["audit_pass"] = r.get("candidate") is None and \
                        r.get("attribution") == "non-optimizer-attributed"
                    if not row["audit_pass"]:
                        audits_failed.append(row)
                else:
                    counts["blocked_or_error"] += 1
                    row["class"] = "blocked_or_error"
                    row["audit_pass"] = True
                rows.append(row)

    total = sum(counts.values())
    emitted = counts["optimal"] + counts["suboptimal_incumbent"]
    report = {
        "pilot_dir": str(pilot), "solver_budget_s": args.budget,
        "total_station_calls": total,
        "availability": {**counts,
                         "candidates_emitted": emitted,
                         "emission_rate": emitted / total if total else None},
        "suboptimal_mip_gap": {"count": len(gaps),
                               "max": max(gaps) if gaps else None,
                               "mean": float(np.mean(gaps)) if gaps else None},
        "physical_audit": {"audited_candidate_calls": emitted,
                           "violations": len(audits_failed),
                           "failed_rows": audits_failed},
        "limits": ["Replay of frozen pilot snapshots through the exact live path "
                   "(causal calibration pairs), 2s per-station budget, read-only.",
                   "Physical audit checks exclusivity, power/SOC bounds, SOP import "
                   "ban while discharging, energy dynamics, and first-step grid "
                   "balance against the observation.",
                   "Suboptimal incumbents remain labeled; availability gain is NOT "
                   "achieved by relaxing mip_rel_gap (R8)."]}
    (output / "verification_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    import csv
    with (output / "rows.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=sorted({k for r in rows for k in r}))
        writer.writeheader()
        writer.writerows(rows)
    print(json.dumps(report["availability"], ensure_ascii=False))
    print("mip_gap:", report["suboptimal_mip_gap"])
    print("audit violations:", len(audits_failed))
    print("wrote", output)


if __name__ == "__main__":
    main()
