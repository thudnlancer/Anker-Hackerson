"""Capture native Python exit status without PowerShell stderr conversion."""
import json
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timezone


def main():
    arguments = sys.argv[1:]
    script_name = "run_strategy_comparison.py"
    if "--script" in arguments:
        pos = arguments.index("--script")
        script_name = arguments[pos + 1]
        if script_name not in {"run_strategy_comparison.py", "run_stress_scenarios.py",
                               "run_self_consumption_comparison.py", "run_holdout_validation.py"}:
            raise SystemExit("Unsupported experiment script")
        del arguments[pos:pos + 2]
    if "--output" not in arguments:
        raise SystemExit("Pass an explicit --output directory")
    output = Path(arguments[arguments.index("--output") + 1])
    if (output / "strategy_comparison_summary.json").exists() or (output / "summary.json").exists():
        raise SystemExit("Completed output exists; choose a new directory")
    output.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, "-u", str(Path(__file__).with_name(script_name)), *arguments]
    started = datetime.now(timezone.utc).isoformat()
    with (output / "console.log").open("ab") as log:
        result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=False)
    status = {"started_utc": started, "finished_utc": datetime.now(timezone.utc).isoformat(),
              "returncode": result.returncode, "script": script_name, "arguments": arguments}
    with (output / "process_status.jsonl").open("a", encoding="utf-8") as status_log:
        status_log.write(json.dumps(status) + "\n")
    print(json.dumps(status))
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
