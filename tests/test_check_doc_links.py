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


def test_ignored_generated_artifact_reference_is_allowed(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "outputs").mkdir()
    (tmp_path / "outputs" / "local.json").write_text("{}", encoding="utf-8")
    source = tmp_path / "docs" / "index.md"
    source.write_text("[generated](../outputs/local.json)", encoding="utf-8")
    assert broken_links(tmp_path, [Path("docs/index.md")]) == []


def test_links_cannot_escape_repository_root(tmp_path):
    (tmp_path / "docs").mkdir()
    source = tmp_path / "docs" / "index.md"
    source.write_text("[outside](../../outside.md)", encoding="utf-8")
    (tmp_path.parent / "outside.md").write_text("outside", encoding="utf-8")
    assert broken_links(tmp_path, [Path("docs/index.md")]) == [
        ("docs/index.md", "../../outside.md")]
