import re
from pathlib import Path

old = Path(r"C:\Users\10200\AppData\Local\Temp\store_76399f0.py").read_text(encoding="utf-8")
cur = Path("explainability-app/src/energy_explain/store.py").read_text(encoding="utf-8")

for name, src in (("OLD-76399f0", old), ("CURRENT", cur)):
    print(f"=== {name} tables ===")
    for m in re.finditer(r'"(CREATE TABLE[^"]*?)"', src, re.S):
        print(m.group(1)[:150].replace("\n", " "), "\n---")
