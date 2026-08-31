import subprocess

import pytest
from osism_drift.mirror_sync import worktree


def _git(d, *a):
    subprocess.run(["git", "-C", str(d), *a], check=True, capture_output=True)


def _defaults_repo(tmp_path):
    d = tmp_path / "defaults"
    (d / "all").mkdir(parents=True)
    _git(d, "init", "-q", "-b", "main")
    _git(d, "config", "user.email", "t@t")
    _git(d, "config", "user.name", "t")
    (d / "all" / "099-kolla.yml").write_text("osism: true\n")
    _git(d, "add", "-A")
    _git(d, "commit", "-qm", "base")
    return d


def test_default_path_leaf_is_named_defaults(tmp_path):
    got = worktree.default_path(tmp_path / "defaults", "2025.2")
    # The leaf MUST be "defaults": source._local_repo_dir resolves <base-dir>/<repo>,
    # so check-drift cannot address the synced tree otherwise.
    assert got.name == "defaults"
    assert got.parent.name == "sync-2025.2"


def test_create_yields_a_clean_tree_and_the_base_commit(tmp_path):
    d = _defaults_repo(tmp_path)
    wt = worktree.default_path(d, "2025.2")
    sha = worktree.create(d, wt, "main")
    assert len(sha) == 40
    assert (wt / "all" / "099-kolla.yml").exists()
    assert (
        subprocess.run(
            ["git", "-C", str(wt), "status", "--porcelain"],
            capture_output=True,
            text=True,
        ).stdout
        == ""
    )
    worktree.remove(d, wt)


def test_create_refuses_an_existing_path(tmp_path):
    d = _defaults_repo(tmp_path)
    wt = worktree.default_path(d, "2025.2")
    wt.mkdir(parents=True)
    with pytest.raises(worktree.WorktreeExists, match="already exists"):
        worktree.create(d, wt, "main")


def test_uncommitted_operator_work_is_not_consulted(tmp_path):
    d = _defaults_repo(tmp_path)
    (d / "all" / "099-kolla.yml").write_text("osism: LOCAL EDIT\n")
    wt = worktree.default_path(d, "2025.2")
    worktree.create(d, wt, "main")
    # The worktree comes from --base-ref, so the dirty checkout is invisible.
    assert "LOCAL EDIT" not in (wt / "all" / "099-kolla.yml").read_text()
    worktree.remove(d, wt)


def test_create_removes_the_worktree_when_head_cannot_be_read(tmp_path, monkeypatch):
    d = _defaults_repo(tmp_path)
    wt = worktree.default_path(d, "2025.2")
    real = worktree._git

    def fake(repo, *args):
        if args[:1] == ("rev-parse",):

            class R:
                returncode = 1
                stdout = b""
                stderr = b""

            return R()
        return real(repo, *args)

    monkeypatch.setattr(worktree, "_git", fake)
    with pytest.raises(worktree.CreateFailed, match="HEAD did not resolve"):
        worktree.create(d, wt, "main")
    assert not wt.exists()


def test_session_removes_on_exit_by_default(tmp_path):
    d = _defaults_repo(tmp_path)
    wt = worktree.default_path(d, "2025.2")
    with worktree.session(d, wt, "main") as (path, sha):
        assert path.exists() and len(sha) == 40
    assert not wt.exists()


def test_session_removes_on_exception_too(tmp_path):
    d = _defaults_repo(tmp_path)
    wt = worktree.default_path(d, "2025.2")
    with pytest.raises(RuntimeError):
        with worktree.session(d, wt, "main"):
            raise RuntimeError("boom")
    assert not wt.exists()


def test_session_keeps_when_asked(tmp_path):
    d = _defaults_repo(tmp_path)
    wt = worktree.default_path(d, "2025.2")
    with worktree.session(d, wt, "main", keep=True) as (path, _):
        assert path.exists()
    assert wt.exists()
    worktree.remove(d, wt)


def test_session_raises_when_cleanup_fails(tmp_path, monkeypatch):
    # A silently failed cleanup leaves the default path occupied, so the NEXT run
    # refuses with a confusing "already exists" instead of the real cause.
    d = _defaults_repo(tmp_path)
    wt = worktree.default_path(d, "2025.2")
    monkeypatch.setattr(worktree, "remove", lambda *a: False)
    with pytest.raises(worktree.CleanupFailed, match="could not be removed"):
        with worktree.session(d, wt, "main"):
            pass


def test_cleanup_failure_does_not_mask_the_original_error(tmp_path, monkeypatch):
    d = _defaults_repo(tmp_path)
    wt = worktree.default_path(d, "2025.2")
    monkeypatch.setattr(worktree, "remove", lambda *a: False)
    with pytest.raises(worktree.CleanupFailed) as ei:
        with worktree.session(d, wt, "main"):
            raise RuntimeError("original")
    assert isinstance(ei.value.__cause__, RuntimeError)
    assert str(ei.value.__cause__) == "original"


def test_every_failure_is_a_worktree_error(tmp_path):
    # The CLI catches the base class; a stray sibling exception would escape as
    # exit 1 with a traceback instead of the documented operational status.
    for exc in (worktree.WorktreeExists, worktree.CreateFailed, worktree.CleanupFailed):
        assert issubclass(exc, worktree.WorktreeError)
