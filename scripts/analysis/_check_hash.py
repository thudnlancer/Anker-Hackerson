import hashlib
import re

src = open("src/energy_lab/explainability_evidence.py", encoding="utf-8").read()
hardcoded = re.findall(r'UPSTREAM_SOURCE_SHA256\s*=\s*"([0-9a-f]+)"', src)
print("hardcoded:", hardcoded)

raw = open("src/energy_lab/rolling.py", "rb").read()
print("local raw sha256:", hashlib.sha256(raw).hexdigest())
print("local LF-normalized sha256:", hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest())
print("CRLF count:", raw.count(b"\r\n"), "total \\n count:", raw.count(b"\n"))

# how does the code compute the hash?
m = re.search(r"def _upstream_source_sha256.*?(?=\n\n|\ndef )", src, re.S)
print("\n--- hash computation ---")
print(m.group(0) if m else "not found")
