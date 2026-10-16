from pathlib import Path

from scripts.maintenance.check_doc_links import broken_links


def test_local_links_resolve_relative_to_source_and_ignore_external(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "target.md").write_text("ok", encoding="utf-8")
    source = tmp_path / "docs" / "index.md"
    source.write_text("[ok](target.md#part) [web](https://example.org) "
                      "[missing](gone.md)", encoding="utf-8")
    assert broken_links(tmp_path, [Path("docs/index.md")]) == [
        ("docs/index.md", "gone.md")]
