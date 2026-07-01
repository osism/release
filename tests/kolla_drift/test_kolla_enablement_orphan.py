from pathlib import Path
import pytest
import responses
from osism_drift.config import (
    Config,
    Remote,
    PluginCfg,
    SourceCfg,
    Allowlist,
    AllowEntry,
)
from osism_drift.drift import kolla_enablement_orphan as plugin

FIXT = Path(__file__).parent / "fixtures"
API = "https://api.github.com/repos"
RAW = "https://raw.githubusercontent.com"


@pytest.fixture
def cfg():
    return Config(
        remote=Remote(f"{RAW}/", f"{API}/", "main", "osism"),
        base_dirs=(str(FIXT),),
        remote_fallback=True,  # FIXT/kolla-ansible is a plain dir, not a git repo -> remote
        release_version="latest",
        plugins={"kolla_enablement_orphan": PluginCfg(enabled=True)},
        sources={"kolla_ansible": SourceCfg(owner="openstack", branch="stable/2025.2")},
        releases=("A", "B"),
    )


def _mock_release(ref, defines):
    # release_to_ref probes stable/<r> first (200), then read_at_ref the
    # monolithic all.yml. Distinct refs -> distinct URLs, no ordering reliance.
    body = ("".join(f'enable_{k}: "no"\n' for k in sorted(defines))).encode()
    responses.add(
        responses.GET, f"{API}/openstack/kolla-ansible/commits/{ref}", status=200
    )
    responses.add(
        responses.GET,
        f"{RAW}/openstack/kolla-ansible/{ref}/ansible/group_vars/all.yml",
        body=body,
        status=200,
    )


def _mock_upstream(defines):
    # Same definitions at both releases A and B.
    for ref in ("stable/A", "stable/B"):
        _mock_release(ref, defines)


@responses.activate
def test_truthy_scope_flags_absent_truthy_services(cfg):
    _mock_upstream({"foo", "bar"})
    drifts = plugin._run(cfg, Allowlist(()), "truthy")
    assert sorted(d.image for d in drifts) == ["feat", "multi_word"]
    assert all(not d.allowlisted for d in drifts)


@responses.activate
def test_explicit_scope_also_flags_dead_no_flags(cfg):
    _mock_upstream({"foo", "bar"})
    drifts = plugin._run(cfg, Allowlist(()), "explicit")
    assert sorted(d.image for d in drifts) == ["feat", "multi_word", "off"]


@responses.activate
def test_present_upstream_is_not_orphan(cfg):
    # "multi-word" (hyphen) upstream must match the underscore OSISM id via canon.
    _mock_upstream({"foo", "bar", "feat", "multi-word"})
    drifts = plugin._run(cfg, Allowlist(()), "truthy")
    assert drifts == []


@responses.activate
def test_union_across_releases_not_intersection(cfg):
    # feat is defined upstream ONLY at release A, multi-word ONLY at B. The union
    # over the range covers both, so neither is an orphan. This pins the union
    # semantics: an impl that reads only one release, only the last, or the
    # intersection would wrongly flag the one missing from its chosen release.
    _mock_release("stable/A", {"foo", "bar", "feat"})
    _mock_release("stable/B", {"foo", "bar", "multi-word"})
    drifts = plugin._run(cfg, Allowlist(()), "truthy")
    assert drifts == []


@responses.activate
def test_allowlist_marks_allowlisted(cfg):
    _mock_upstream({"foo", "bar"})
    al = Allowlist(
        (
            AllowEntry(
                plugin="kolla_enablement_orphan", image="feat", reason="OSISM-invented"
            ),
        )
    )
    drifts = plugin._run(cfg, al, "truthy")
    feat = [d for d in drifts if d.image == "feat"][0]
    assert feat.allowlisted is True


@responses.activate
def test_run_uses_explicit_default(cfg):
    _mock_upstream({"foo", "bar"})
    drifts = plugin.run(cfg, Allowlist(()))  # public run() -> SCOPE == "explicit"
    # explicit scope also flags dead enable_X: "no" flags, so "off" is included.
    assert sorted(d.image for d in drifts) == ["feat", "multi_word", "off"]


def test_empty_release_range_raises(tmp_path):
    from osism_drift.source import SourceError

    empty = tmp_path / "release" / "latest"
    empty.mkdir(parents=True)
    c = Config(
        remote=Remote(f"{RAW}/", f"{API}/", "main", "osism"),
        base_dirs=(str(tmp_path), str(FIXT)),
        release_version="latest",
        plugins={},
        sources={},
        releases=(),
    )
    with pytest.raises(SourceError, match="empty supported release range"):
        plugin._run(c, Allowlist(()), "truthy")
