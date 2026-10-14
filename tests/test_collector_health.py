from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from energy_lab.collector_health import NodeHealth, classify_cluster, evaluate_node


class EvaluateNodeTests(unittest.TestCase):
    def eval(self, state, age, streak=0):
        return evaluate_node(NodeHealth(state, streak), age)

    def test_healthy_stays_when_fresh(self):
        health, event = self.eval("HEALTHY", 5)
        self.assertEqual(health.state, "HEALTHY")
        self.assertIsNone(event)

    def test_healthy_to_stale_then_dead(self):
        health, event = self.eval("HEALTHY", 20)
        self.assertEqual((health.state, event["to"]), ("STALE", "STALE"))
        health, event = evaluate_node(health, 90)
        self.assertEqual((health.state, event["to"]), ("DEAD", "DEAD"))

    def test_healthy_to_dead_directly(self):
        health, event = self.eval("HEALTHY", 120)
        self.assertEqual(health.state, "DEAD")
        self.assertEqual(event["from"], "HEALTHY")

    def test_missing_heartbeat_counts_as_dead(self):
        health, _ = self.eval("HEALTHY", None)
        self.assertEqual(health.state, "DEAD")
        health, _ = self.eval("STALE", None)
        self.assertEqual(health.state, "DEAD")

    def test_stale_recovers_directly_to_healthy(self):
        health, event = self.eval("STALE", 3)
        self.assertEqual((health.state, event["to"]), ("HEALTHY", "HEALTHY"))

    def test_dead_enters_recovering_then_needs_streak(self):
        health, event = self.eval("DEAD", 2)
        self.assertEqual((health.state, health.fresh_streak), ("RECOVERING", 1))
        health, event = evaluate_node(health, 1)
        self.assertEqual((health.state, health.fresh_streak), ("RECOVERING", 2))
        health, event = evaluate_node(health, 1)
        self.assertEqual(health.state, "HEALTHY")
        self.assertEqual(event["reason"], "recovery_confirmed")

    def test_recovering_relapses_to_dead(self):
        health, event = self.eval("RECOVERING", 100, streak=2)
        self.assertEqual((health.state, event["reason"]), ("DEAD", "relapsed_during_recovery"))

    def test_invalid_params_raise(self):
        with self.assertRaises(ValueError):
            evaluate_node(NodeHealth(), 5, stale_after=60, dead_after=15)
        with self.assertRaises(ValueError):
            NodeHealth("BOGUS")


class ClassifyClusterTests(unittest.TestCase):
    def test_all_erroring_is_upstream(self):
        self.assertEqual(classify_cluster({"nodeA": 3, "nodeB": 1}), "cluster_upstream_suspected")

    def test_partial_errors(self):
        self.assertEqual(classify_cluster({"nodeA": 0, "nodeB": 2}), "partial_node_errors")

    def test_all_clear_and_empty(self):
        self.assertEqual(classify_cluster({"nodeA": 0}), "all_clear")
        self.assertEqual(classify_cluster({}), "no_nodes")


if __name__ == "__main__":
    unittest.main()
