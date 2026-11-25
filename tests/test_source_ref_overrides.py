"""Per-run ref overrides: a repo in Config.ref_overrides is read at that ref."""

import os
import subprocess

import pytest

from osism_drift import archive, source
from osism_drift.config import Config, Remote, SourceCfg
from osism_drift.http import SourceError


def _git(repo, *args):
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "-C",
            str(repo),
            *args,
        ],
        check=True,
        capture_output=True,
    )


def _cfg(base_dirs=(), **kw):
    return Config(
        remote=Remote(
            github_raw="https://raw.example/",
            github_api="https://api.example/",
            branch="main",
        ),
        release_version="x",
        plugins={},
        base_dirs=tuple(str(b) for b in base_dirs),
        **kw,
    )


def _repo(tmp_path, name="generics"):
    repo = tmp_path / name
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    return repo


def _commit(repo, msg="c"):
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", msg)


def test_override_reads_tag_from_git_objects(tmp_path):
    repo = _repo(tmp_path)
    (repo / "a.txt").write_text("tagged")
    _commit(repo)
    _git(repo, "tag", "v0.1")
    (repo / "a.txt").write_text("dirty")
    cfg = _cfg([tmp_path], ref_overrides={"generics": "refs/tags/v0.1"})
    assert source.read("generics", "a.txt", cfg) == b"tagged"
    assert source.current_ref("generics", cfg) == "refs/tags/v0.1"
    assert source._is_pinned("generics", cfg)
    assert source._pinned_refs("generics", cfg) == "refs/tags/v0.1 (override)"


def test_override_tag_absent_same_named_branch_raises(tmp_path):
    repo = _repo(tmp_path)
    (repo / "a.txt").write_text("x")
    _commit(repo)
    _git(repo, "branch", "v0.1")
    cfg = _cfg([tmp_path], ref_overrides={"generics": "refs/tags/v0.1"})
    with pytest.raises(SourceError):
        source.read("generics", "a.txt", cfg)


def test_override_remote_archive_url_uses_qualified_ref(monkeypatch):
    urls = []

    def fake(repo, ref, url):
        urls.append(url)
        raise SourceError("stop")

    monkeypatch.setattr(archive, "_fetch_archive_bytes", fake)
    cfg = _cfg(archive=True, ref_overrides={"generics": "refs/tags/v0.1"})
    with pytest.raises(SourceError):
        source.read("generics", "a.txt", cfg)
    assert urls and urls[0].endswith("/tarball/refs/tags/v0.1")


def test_no_override_keeps_configured_ref():
    cfg = _cfg(sources={"ansible_playbooks": SourceCfg(branch="main")})
    assert source.current_ref("ansible_playbooks", cfg) == "main"


def _symlink_repo(tmp_path, target):
    repo = _repo(tmp_path, "release")
    (repo / "latest").mkdir()
    (repo / "latest" / "ceph-reef.yml").write_text("playbooks_version: v1\n")
    os.symlink(target, repo / "latest" / "ceph_ansible.yml")
    _commit(repo)
    _git(repo, "tag", "v1")
    return _cfg([tmp_path], ref_overrides={"release": "refs/tags/v1"})


def test_git_object_read_follows_symlink(tmp_path):
    cfg = _symlink_repo(tmp_path, "ceph-reef.yml")
    got = source.read("release", "latest/ceph_ansible.yml", cfg)
    assert got == b"playbooks_version: v1\n"


def test_git_object_read_rejects_symlink_outside_repo(tmp_path):
    cfg = _symlink_repo(tmp_path, "../../x")
    with pytest.raises(SourceError):
        source.read("release", "latest/ceph_ansible.yml", cfg)
