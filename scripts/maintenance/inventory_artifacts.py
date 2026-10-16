"""Read-only inventory for output retention decisions; never deletes files."""
from __future__ import annotations

from datetime import datetime, timezone
import argparse
import hashlib
import json
from pathlib import Path


PROTECTED = {
    "local_mock_20261016",
    "local_mock_20261016_run2",
    "local_mock_20261016_run3",
    "local_mock_20261016_postmerge_readonly",
    "local_mock_20261016_o7_readonly_20261016",
}


def digest(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            sha.update(block)
    return sha.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    output = root / "outputs"
    if not output.is_dir():
        raise SystemExit("outputs directory not found")
    entries = []
    for path in sorted(output.iterdir()):
        files = sorted(item for item in path.rglob("*") if item.is_file()) if path.is_dir() else [path]
        bytes_total = sum(item.stat().st_size for item in files)
        latest = max((item.stat().st_mtime for item in files), default=path.stat().st_mtime)
        protected = path.name in PROTECTED or any(part in PROTECTED for part in path.parts)
        entries.append({"path": path.relative_to(root).as_posix(), "kind": "directory" if path.is_dir() else "file",
                        "file_count": len(files), "bytes": bytes_total,
                        "latest_modified_utc": datetime.fromtimestamp(latest, timezone.utc).isoformat(),
                        "retention": "protected_online_evidence" if protected else "review_required"})
    manifest = {"generated_at_utc": datetime.now(timezone.utc).isoformat(),
                "root": str(root), "entry_count": len(entries), "entries": entries,
                "protected_files": {
                    path.relative_to(root).as_posix(): digest(path)
                    for name in sorted(PROTECTED) for path in (output / name).rglob("*") if path.is_file()
                }}
    target = args.output.resolve()
    if not target.is_relative_to(root / "archive"):
        raise SystemExit("inventory output must be inside archive")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"entry_count": len(entries), "protected_file_count": len(manifest["protected_files"]),
                      "output": str(target)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
