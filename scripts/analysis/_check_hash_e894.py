import hashlib
import re
import subprocess


def show(commit, path):
    r = subprocess.run(["git", "show", f"{commit}:{path}"], capture_output=True)
    return r.stdout if r.returncode == 0 else None


for c in ["e894172"]:
    r = show(c, "src/energy_lab/rolling.py")
    print(c, "rolling.py sha:", hashlib.sha256(r).hexdigest()[:16] if r else None)
    for t in [
        "tests/test_explainability_bridge.py",
        "tests/test_explainability_stream.py",
        "tests/test_stream_opening_recovery.py",
        "src/energy_lab/explainability_evidence.py",
    ]:
        print(" ", t, "exists" if show(c, t) else "MISSING")
    ev = show(c, "src/energy_lab/explainability_evidence.py")
    if ev:
        m = re.findall(rb'UPSTREAM_SOURCE_SHA256\s*=\s*"([0-9a-f]+)"', ev)
        print("  hardcoded hash at", c, ":", [x.decode()[:16] for x in m])
