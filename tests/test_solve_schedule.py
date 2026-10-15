import time
import threading
import unittest
from energy_lab.solve_schedule import solve_until


class SolveScheduleTests(unittest.TestCase):
    def test_slow_result_is_discarded_at_deadline(self):
        deadline = time.monotonic() + .03
        result, reason, elapsed = solve_until(deadline, lambda: (time.sleep(.15), "late")[1])
        self.assertIsNone(result)
        self.assertEqual(reason, "solver_deadline_exceeded")
        self.assertLess(elapsed, .12)
        time.sleep(.16)

    def test_second_solver_cannot_overlap(self):
        entered, release = threading.Event(), threading.Event()
        result = []
        def occupied():
            entered.set()
            release.wait(1)
            return "first"
        first = threading.Thread(target=lambda: result.append(
            solve_until(time.monotonic() + .5, occupied)))
        first.start()
        self.assertTrue(entered.wait(.2))
        try:
            second, reason, _ = solve_until(time.monotonic() + .2, lambda: "overlap")
            self.assertIsNone(second)
            self.assertEqual(reason, "previous_solver_still_running")
        finally:
            release.set()
            first.join(1)
        self.assertFalse(first.is_alive())
        self.assertEqual(result[0][:2], ("first", None))


if __name__ == "__main__":
    unittest.main()
