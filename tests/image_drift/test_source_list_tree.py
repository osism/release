"""Unit tests for source.list_tree recursive enumeration helper."""

import pytest
from osism_drift import source
from osism_drift.config import Config, Remote


def _cfg(base_dir):
    return Config(
        remote=Remote("https://x/", "https://y/", "main", "osism"),
        base_dirs=(str(base_dir),),
        release_version="latest",
        plugins={},
        sources={},
    )


def test_list_tree_returns_nested_paths(tmp_path):
    repo_dir = tmp_path / "testrepo"
    (repo_dir / "dir" / "sub").mkdir(parents=True)
    (repo_dir / "dir" / "top.txt").write_text("top")
    (repo_dir / "dir" / "sub" / "nested.txt").write_text("nested")

    cfg = _cfg(tmp_path)
    result = source.list_tree("testrepo", "dir", cfg)

    assert "dir/top.txt" in result
    assert "dir/sub/nested.txt" in result


def test_list_tree_absent_raises_by_default(tmp_path):
    repo_dir = tmp_path / "testrepo"
    repo_dir.mkdir()

    cfg = _cfg(tmp_path)
    with pytest.raises(source.SourceError):
        source.list_tree("testrepo", "nonexistent", cfg)


def test_list_tree_absent_missing_ok_returns_empty(tmp_path):
    repo_dir = tmp_path / "testrepo"
    repo_dir.mkdir()

    cfg = _cfg(tmp_path)
    result = source.list_tree("testrepo", "nonexistent", cfg, missing_ok=True)
    assert result == []
