import pytest
from osism_drift import source
from osism_drift.http import SourceError


def test_dir_read_returns_bytes(tmp_path):
    (tmp_path / "x").write_bytes(b"hello")
    assert source._dir_read(tmp_path, "x", "ctx") == b"hello"


def test_dir_read_missing_raises_with_where(tmp_path):
    with pytest.raises(SourceError, match="ctx"):
        source._dir_read(tmp_path, "missing", "ctx")


def test_dir_read_optional_present(tmp_path):
    (tmp_path / "x").write_bytes(b"data")
    assert source._dir_read_optional(tmp_path, "x") == b"data"


def test_dir_read_optional_absent_returns_none(tmp_path):
    assert source._dir_read_optional(tmp_path, "absent") is None


def test_dir_list_tree_sorted_repo_relative(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "b.yml").write_bytes(b"")
    (tmp_path / "a" / "c").mkdir()
    (tmp_path / "a" / "c" / "d.yml").write_bytes(b"")
    result = source._dir_list_tree(tmp_path, "a", "ctx")
    assert result == ["a/b.yml", "a/c/d.yml"]


def test_dir_list_tree_missing_raises(tmp_path):
    with pytest.raises(SourceError, match="ctx"):
        source._dir_list_tree(tmp_path, "nope", "ctx")


def test_dir_list_tree_missing_ok_returns_empty(tmp_path):
    assert source._dir_list_tree(tmp_path, "nope", "ctx", missing_ok=True) == []


def test_dir_list_returns_names(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "sub").mkdir()
    (tmp_path / "a" / "file.txt").write_bytes(b"")
    result = source._dir_list(tmp_path, "a", "ctx")
    assert set(result) == {"sub", "file.txt"}


def test_dir_list_dirs_only(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "sub").mkdir()
    (tmp_path / "a" / "file.txt").write_bytes(b"")
    result = source._dir_list(tmp_path, "a", "ctx", dirs_only=True)
    assert result == ["sub"]


def test_dir_list_missing_raises(tmp_path):
    with pytest.raises(SourceError, match="ctx"):
        source._dir_list(tmp_path, "nope", "ctx")
