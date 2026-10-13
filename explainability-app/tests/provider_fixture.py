"""Registration test double, limited to explicit synthetic test inputs."""
from energy_explain.providers import DemoMilpProvider


class RegisteredTestProvider:
    def supports(self, decision):
        return (decision["source_kind"] == "external"
                and decision["model_version"] == "qa-registration-fixture-v1"
                and decision["policy_version"] == "test-policy-v1"
                and all(source == "synthetic" for source in decision["snapshot"]["sources"].values()))

    def compare(self, decision, first_charge_kw):
        model = DemoMilpProvider()
        baseline = model.solve(decision["snapshot"], decision["action"]["power_kw"])
        alternative = model.solve(decision["snapshot"], first_charge_kw)
        return {"baseline": baseline, "alternative": alternative,
                "delta_eur": alternative["cost"]["objective_eur"] - baseline["cost"]["objective_eur"],
                "assumptions": ["Explicit synthetic registration test double, never actual telemetry."]}


def register(register_provider):
    register_provider("qa-registration-fixture-v1", RegisteredTestProvider())
