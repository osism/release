"""source.tree_dir: one local directory per (repo, ref), whatever the backend."""

import pytest

from osism_drift import source
from osism_drift.source import SourceError


def _files(root):
    return sorted(
        p.relative_to(root).as_posix()
        for p in root.rglob("*")
        if p.is_file() and ".git" not in p.relative_to(root).parts
    )


def test_unpinned_local_repo_at_configured_ref_is_the_working_tree(tmp_path, make_cfg):
    (tmp_path / "widget").mkdir()
    (tmp_path / "widget" / "a.yml").write_text("x: 1\n")
    cfg = make_cfg(tmp_path)
    assert source.tree_dir("widget", None, cfg) == tmp_path / "widget"


def test_unpinned_local_git_checkout_is_read_at_an_explicit_ref(
    tmp_path, git_repo, make_cfg
):
    git_repo("widget", {"v1": {"old.yml": "x: 1\n"}, "v2": {"new.yml": "y: 2\n"}})
    cfg = make_cfg(tmp_path)
    root = source.tree_dir("widget", "v1", cfg)
    assert _files(root) == ["old.yml"]
    assert (root / "old.yml").read_text() == "x: 1\n"


def test_pinned_local_repo_at_configured_ref_reads_the_pin_not_the_worktree(
    tmp_path, git_repo, make_cfg
):
    git_repo("widget", {"v1": {"old.yml": "x: 1\n"}, "v2": {"new.yml": "y: 2\n"}})
    cfg = make_cfg(tmp_path, pinned={"widget": "v1"})
    assert _files(source.tree_dir("widget", None, cfg)) == ["old.yml"]


def test_single_top_level_directory_is_not_mistaken_for_the_root(
    tmp_path, git_repo, make_cfg
):
    # archive._extract_snapshot unwraps a lone top-level dir (GitHub tarballs
    # have one); git archive gets --prefix=tree/ so that dir is never the repo's own.
    git_repo("widget", {"v1": {"roles/a/defaults/main.yml": "x: 1\n"}})
    cfg = make_cfg(tmp_path)
    assert _files(source.tree_dir("widget", "v1", cfg)) == ["roles/a/defaults/main.yml"]


def test_explicit_ref_on_a_non_git_dir_fails_in_a_local_only_run(tmp_path, make_cfg):
    (tmp_path / "widget").mkdir()
    cfg = make_cfg(tmp_path)
    with pytest.raises(SourceError, match="not a git checkout"):
        source.tree_dir("widget", "v1", cfg)


def test_unknown_ref_fails(tmp_path, git_repo, make_cfg):
    git_repo("widget", {"v1": {"a.yml": "x: 1\n"}})
    cfg = make_cfg(tmp_path)
    with pytest.raises(SourceError, match="not found"):
        source.tree_dir("widget", "v9", cfg)


def test_extraction_is_memoized(tmp_path, git_repo, make_cfg):
    git_repo("widget", {"v1": {"a.yml": "x: 1\n"}})
    cfg = make_cfg(tmp_path)
    assert source.tree_dir("widget", "v1", cfg) is source.tree_dir("widget", "v1", cfg)
