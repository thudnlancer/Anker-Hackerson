"""Spawn-compatible, standard-library-only worker failure fixtures."""
import time


def hang(sender, decision, first_charge_kw):
    time.sleep(10)


def crash(sender, decision, first_charge_kw):
    sender.close()


def infeasible(sender, decision, first_charge_kw):
    sender.send({"status": "infeasible", "result": None, "error": "fixture: no feasible plan"})
    sender.close()
