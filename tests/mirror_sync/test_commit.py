"""--commit: the mechanical step after every judgement has been made."""

import subprocess

import pytest

from osism_drift.mirror_sync import commit


def _repo(path):
    def git(*args, **kw):
        return subprocess.run(
            ["git", "-C", str(path), *args], capture_output=True, check=True, **kw
        )

    path.mkdir(parents=True, exist_ok=True)
    git("init", "-q", "-b", "main")
    git("config", "user.name", "A Tester")
    git("config", "user.email", "tester@example.invalid")
    git("config", "commit.gpgsign", "false")
    (path / "keep.txt").write_text("x\n")
    git("add", "keep.txt")
    git("commit", "-q", "-m", "base", env={"PATH": "/usr/bin:/bin", "HOME": str(path)})
    return git


def test_commit_branches_stages_and_returns_the_sha(tmp_path):
    tree = tmp_path / "wt"
    git = _repo(tree)
    (tree / "written.yml").write_text("a: 1\n")
    sha = commit.commit(
        tree, "sync-mirror/2026.1", "2026.1", "PROVENANCE\n", ["written.yml"]
    )
    assert len(sha) == 40
    assert git("branch", "--show-current").stdout.decode().strip() == (
        "sync-mirror/2026.1"
    )
    body = git("log", "-1", "--format=%B").stdout.decode()
    assert body.startswith("kolla: re-sync the mirror layer for 2026.1\n")
    assert "PROVENANCE" in body
    assert "Signed-off-by: A Tester <tester@example.invalid>" in body
    assert git("status", "--porcelain").stdout.decode().strip() == ""


def test_commit_stages_a_deletion(tmp_path):
    """A path the plan deleted has to reach the commit as a deletion."""
    tree = tmp_path / "wt"
    git = _repo(tree)
    (tree / "gone.yml").write_text("a: 1\n")
    git("add", "gone.yml")
    git("commit", "-q", "-m", "add gone")
    (tree / "gone.yml").unlink()
    commit.commit(tree, "b", "2026.1", "P\n", ["gone.yml"])
    files = git("show", "--stat", "--format=", "HEAD").stdout.decode()
    assert "gone.yml" in files
    assert git("status", "--porcelain").stdout.decode().strip() == ""


def test_an_existing_branch_is_refused_not_moved(tmp_path):
    tree = tmp_path / "wt"
    git = _repo(tree)
    git("branch", "taken")
    (tree / "written.yml").write_text("a: 1\n")
    with pytest.raises(commit.BranchExists):
        commit.commit(tree, "taken", "2026.1", "P\n", ["written.yml"])


def test_signoff_requires_an_identity(tmp_path, monkeypatch):
    """Without it the eventual PR fails the DCO gate, so refuse early.

    The lookup is stubbed rather than unset in the repo: `git config user.email`
    falls back to the global file, so a --local --unset proves nothing here.
    """

    class _Empty:
        stdout = b"\n"
        returncode = 0

    monkeypatch.setattr(commit, "_git", lambda *a, **k: _Empty())
    with pytest.raises(commit.CommitError, match="user.name and user.email"):
        commit.signoff(tmp_path)


def test_default_branch_is_named_for_the_target():
    assert commit.default_branch("2026.1") == "sync-mirror/2026.1"
