"""Independent single-concurrency worker with recoverable leases and hard timeouts."""
from __future__ import annotations

import argparse
import multiprocessing as mp
import os
import threading
import math
import time

from .providers import ModelOutcome, run_comparison
from .store import Conflict, Store


def compute_child(sender, decision: dict, first_charge_kw: float):
    try:
        sender.send({"status":"ready","result":run_comparison(decision,first_charge_kw),"error":None})
    except ModelOutcome as exc:
        sender.send({"status":exc.status,"result":None,"error":str(exc)})
    except Exception as exc:
        sender.send({"status":"failed","result":None,"error":f"{type(exc).__name__}: {exc}"})
    finally:
        sender.close()


def timed_child(sender, decision, power, timeout, target):
    # Still enforces a bound if the parent is killed or a native solver ignores signals.
    watchdog = threading.Timer(timeout,lambda: os._exit(124))
    watchdog.daemon = True
    watchdog.start()
    try: target(sender,decision,power)
    finally: watchdog.cancel()


def stop_child(process):
    if process.is_alive():
        process.terminate(); process.join(timeout=.5)
    if process.is_alive():
        process.kill(); process.join(timeout=1)
    if process.is_alive():
        raise RuntimeError("child process could not be stopped; lease retained")


def process_one(store: Store, timeout: float = 30, target=compute_child) -> dict | None:
    if not math.isfinite(timeout) or timeout <= 0: raise ValueError("timeout must be finite and positive")
    job = store.claim_job(lease_seconds=timeout+10)
    if not job: return None
    ctx = mp.get_context("spawn")
    receiver, sender = ctx.Pipe(duplex=False)
    process = ctx.Process(target=timed_child,args=(sender,store.get_decision(job["decision_id"]),job["first_charge_kw"],timeout,target))
    try:
        process.start()
        sender.close()
        deadline = time.monotonic()+timeout
        outcome = None
        while time.monotonic() < deadline:
            if receiver.poll(min(.1,max(0,deadline-time.monotonic()))):
                try: outcome = receiver.recv()
                except EOFError: pass
                break
            if not process.is_alive():
                if receiver.poll(0):
                    try: outcome = receiver.recv()
                    except EOFError: pass
                break
        if outcome is None:
            expired = time.monotonic() >= deadline or process.exitcode == 124
            outcome = {"status":"timeout" if expired else "failed", "result":None,
                       "error":"反事实计算超过时间限制。" if expired else "计算进程退出且未返回结果。"}
        process.join(timeout=.5)
        stop_child(process)
        return store.finish_job(job["id"],attempt=job["attempts"],**outcome)
    except Conflict:
        # A newer recovered attempt owns the row; this process must never overwrite it.
        return store.get_job(job["id"])
    except Exception as exc:
        stop_child(process)
        return store.finish_job(job["id"],"failed",error=f"worker failure: {type(exc).__name__}: {exc}",attempt=job["attempts"])
    finally:
        sender.close(); receiver.close()
        stop_child(process)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db",default="data/energy.sqlite")
    parser.add_argument("--once",action="store_true")
    parser.add_argument("--timeout",type=float,default=30)
    parser.add_argument("--poll",type=float,default=.5)
    args = parser.parse_args(argv)
    store = Store(args.db)
    if not all(math.isfinite(v) and v > 0 for v in (args.poll,args.timeout)):
        parser.error("poll and timeout must be finite and positive")
    try:
        while True:
            job = process_one(store,args.timeout)
            if job: print(f"{job['id']} {job['status']}",flush=True)
            if args.once: break
            if not job: time.sleep(args.poll)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
