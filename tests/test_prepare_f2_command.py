import sys
import unittest
from copy import deepcopy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.experiments.prepare_f2_command import command_from_manifest


class F2CommandTests(unittest.TestCase):
    def manifest(self):
        return {"f2_authorized": False,
                "signature": {"f2_authorization": False, "integration_owner": "", "signed_at": ""},
                "parameters": {"solver_seconds": 2., "soc_min_percent": 0.,
                               "capacity_kwh": 14., "max_power_w": 5000., "mip_rel_gap": 1e-9,
                               "soc_max_percent": 100., "charge_efficiency": .95,
                               "discharge_efficiency": 1., "degradation_cost_eur_per_kwh": .02,
                               "successful_plan_idle": "zero_flow",
                               "terminal_price": {"rule": "current_visible_mean"},
                               "online_time_step": "official API",
                               "pv_forecast": {"online_scope": "persistence-only"}}}

    def test_unsigned_manifest_refuses_f2(self):
        with self.assertRaisesRegex(ValueError, "signature/F2 authorization"):
            command_from_manifest(self.manifest(), data=Path("data"), output=Path("out"), workers=2)

    def test_signed_hypothetical_command_explicitly_passes_frozen_parameters(self):
        manifest = deepcopy(self.manifest())
        manifest["f2_authorized"] = manifest["signature"]["f2_authorization"] = True
        manifest["signature"].update(integration_owner="test-only", signed_at="test-only")
        command = command_from_manifest(manifest, data=Path("data"), output=Path("out"), workers=2)
        for key, value in (("--solver-time-limit", "2.0"), ("--soc-min", "0.0"),
                           ("--soc-max", "100.0"), ("--charge-efficiency", "0.95"),
                           ("--discharge-efficiency", "1.0"), ("--wear", "0.02"),
                           ("--terminal-price-mode", "current_visible_mean"),
                           ("--hold-mode", "zero_flow"), ("--max-sites", "100")):
            self.assertEqual(command[command.index(key) + 1], value)


if __name__ == "__main__":
    unittest.main()
