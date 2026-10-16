"""Check local Markdown links in Git-tracked project documents."""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import subprocess
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parents[2]
LINK = re.compile(r"\[[^\]]+\]\(([^)]+)\)")


def broken_links(root: Path, files: list[Path]) -> list[tuple[str, str]]:
    broken = []
    for relative in files:
        source = root / relative
        for match in LINK.finditer(source.read_text(encoding="utf-8")):
            raw = match.group(1).strip().split(" ", 1)[0].strip("<>")
            if not raw or raw.startswith(("#", "http:", "https:", "mailto:", "data:")):
                continue
            target = unquote(raw.split("#", 1)[0])
            if not target or not (source.parent / target).exists():
                broken.append((relative.as_posix(), raw))
    return broken


def tracked_markdown(root: Path) -> list[Path]:
    result = subprocess.run(["git", "-C", str(root), "ls-files", "-z", "--", "*.md"],
                            check=True, capture_output=True)
    return [Path(part.decode("utf-8")) for part in result.stdout.split(b"\0") if part]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    failures = broken_links(args.root, tracked_markdown(args.root))
    for source, target in failures:
        print(f"{source}: {target}")
    print(f"broken_local_markdown_links={len(failures)}")
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
