import subprocess

import pytest
import responses
from osism_drift import source
from osism_drift.config import Config, Remote, SourceCfg


def _git(d, *args):
    subprocess.run(["git", "-C", str(d), *args], check=True, capture_output=True)


def _rev(d, ref="HEAD"):
    return subprocess.run(
        ["git", "-C", str(d), "rev-parse", ref], capture_output=True, text=True
    ).stdout.strip()


def _upstream_clone(tmp_path):
    """A real git repo standing in for kolla-ansible, with two commits."""
    d = tmp_path / "kolla-ansible"
    d.mkdir()
    _git(d, "init", "-q", "-b", "main")
    _git(d, "config", "user.email", "t@t")
    _git(d, "config", "user.name", "t")
    (d / "a.txt").write_text("one\n")
    _git(d, "add", "a.txt")
    _git(d, "commit", "-qm", "first")
    first = _rev(d)
    (d / "a.txt").write_text("two\n")
    _git(d, "add", "a.txt")
    _git(d, "commit", "-qm", "second")
    second = _rev(d)
    _git(d, "branch", "stable/2025.2", second)
    return d, first, second


def _cfg(tmp_path=None):
    return Config(
        remote=Remote("https://raw/", "https://api/", "main", "osism"),
        release_version="latest",
        plugins={},
        sources={"kolla_ansible": SourceCfg(owner="openstack", branch="stable/2025.2")},
        base_dirs=(str(tmp_path),) if tmp_path else (),
    )


def test_resolve_commit_local_peels_branch_to_sha(tmp_path):
    _, _, second = _upstream_clone(tmp_path)
    got = source.resolve_commit("kolla_ansible", "stable/2025.2", _cfg(tmp_path))
    assert got == second
    assert len(got) == 40


def test_resolve_commit_local_accepts_a_sha(tmp_path):
    _, first, _ = _upstream_clone(tmp_path)
    assert source.resolve_commit("kolla_ansible", first, _cfg(tmp_path)) == first


def test_resolve_commit_prefers_a_remote_over_a_stale_local_branch(tmp_path):
    """A stale local branch of the same name must not win.

    Observed in a real checkout: refs/heads/stable/2025.2 sat five commits behind
    refs/remotes/origin/stable/2025.2, and sync-mirror consequently proposed
    reverting an upstream fix that had landed in between.
    """
    d, first, second = _upstream_clone(tmp_path)
    # A remote-tracking ref at the newer commit, a local head at the older one.
    _git(d, "update-ref", "refs/remotes/origin/stable/2025.2", second)
    _git(d, "branch", "-f", "stable/2025.2", first)
    got = source.resolve_commit("kolla_ansible", "stable/2025.2", _cfg(tmp_path))
    assert got == second, "resolved the stale local head instead of the remote"


def test_resolve_commit_falls_back_to_a_local_head(tmp_path):
    # With no remote carrying the ref, a local head is the only answer available.
    d, first, _ = _upstream_clone(tmp_path)
    _git(d, "branch", "-f", "local-only", first)
    assert source.resolve_commit("kolla_ansible", "local-only", _cfg(tmp_path)) == first


def test_resolve_commit_unknown_ref_raises(tmp_path):
    _upstream_clone(tmp_path)
    with pytest.raises(source.SourceError, match="cannot resolve"):
        source.resolve_commit("kolla_ansible", "stable/nope", _cfg(tmp_path))


def test_is_ancestor_local(tmp_path):
    _, first, second = _upstream_clone(tmp_path)
    cfg = _cfg(tmp_path)
    assert source.is_ancestor("kolla_ansible", first, second, cfg) is True
    assert source.is_ancestor("kolla_ansible", second, first, cfg) is False


def test_is_ancestor_raises_on_a_missing_descendant(tmp_path):
    # git exits 128 here, not 1. Mapping every non-zero to False would report
    # "not an ancestor" for what is really an operational failure.
    _, first, _ = _upstream_clone(tmp_path)
    with pytest.raises(source.SourceError, match="not present"):
        source.is_ancestor("kolla_ansible", first, "0" * 40, _cfg(tmp_path))


def test_is_ancestor_raises_when_the_ancestor_object_is_absent(tmp_path):
    _, _, second = _upstream_clone(tmp_path)
    with pytest.raises(source.SourceError, match="not present"):
        source.is_ancestor("kolla_ansible", "0" * 40, second, _cfg(tmp_path))


def test_merge_base_local(tmp_path):
    d, first, second = _upstream_clone(tmp_path)
    _git(d, "checkout", "-q", "-b", "side", first)
    (d / "b.txt").write_text("side\n")
    _git(d, "add", "b.txt")
    _git(d, "commit", "-qm", "side")
    side = _rev(d)
    assert source.merge_base("kolla_ansible", second, side, _cfg(tmp_path)) == first


def test_local_checkout_returns_none_when_remote():
    assert source.local_checkout("kolla_ansible", _cfg()) is None


def test_local_checkout_returns_the_dir_when_local(tmp_path):
    d, _, _ = _upstream_clone(tmp_path)
    assert source.local_checkout("kolla_ansible", _cfg(tmp_path)) == d


@responses.activate
def test_resolve_commit_remote_reads_sha_from_commits_api():
    responses.add(
        responses.GET,
        "https://api/openstack/kolla-ansible/commits/stable/2025.2",
        json={"sha": "a" * 40},
        status=200,
    )
    assert source.resolve_commit("kolla_ansible", "stable/2025.2", _cfg()) == "a" * 40


@responses.activate
def test_resolve_commit_rejects_a_non_sha_from_the_api():
    # resolve_commit documents a 40-hex return; callers pin refs from it, so an
    # abbreviated or malformed id must not escape its own boundary.
    responses.add(
        responses.GET,
        "https://api/openstack/kolla-ansible/commits/stable/2025.2",
        json={"sha": "abc123"},
        status=200,
    )
    with pytest.raises(source.SourceError, match="not a 40-hex commit"):
        source.resolve_commit("kolla_ansible", "stable/2025.2", _cfg())


@responses.activate
def test_is_ancestor_remote_uses_compare_status():
    base, head = "b" * 40, "c" * 40
    responses.add(
        responses.GET,
        f"https://api/openstack/kolla-ansible/compare/{base}...{head}",
        json={"status": "ahead"},
        status=200,
    )
    assert source.is_ancestor("kolla_ansible", base, head, _cfg()) is True


@responses.activate
def test_merge_base_remote_reads_compare_payload():
    a, b = "1" * 40, "2" * 40
    responses.add(
        responses.GET,
        f"https://api/openstack/kolla-ansible/compare/{a}...{b}",
        json={"status": "diverged", "merge_base_commit": {"sha": "3" * 40}},
        status=200,
    )
    assert source.merge_base("kolla_ansible", a, b, _cfg()) == "3" * 40
