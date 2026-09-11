"""Tests for the catalog_role_missing plugin: a catalog names a role no
runtime advertises at some supported release.

`_serve` stubs the DATA the plugin's own resolvers consume -- catalog.collections/
validators, playbooks.runtime_interface, and the four per-runtime *_files() the
osism-ansible/kolla-ansible/ceph-ansible/osism-kubernetes branches of
playbook_files() dispatch to -- rather than playbooks.resolves()/validate_resolves()
themselves. Those two functions are the plugin's own rule (mirroring `osism apply`
and `osism validate` respectively) and are left real, so a test failure here means
the plugin's aggregation/naming/ordering is wrong, not a hand-rolled resolver that
duplicates -- and could silently diverge from -- playbooks.py's own logic.
"""

from pathlib import Path

import pytest

from osism_drift import catalog
from osism_drift.config import Allowlist, AllowEntry, Config, PluginCfg, Remote
from osism_drift.drift import catalog_role_missing as plugin
from osism_drift.source import SourceError

FIXT = Path(__file__).parent / "fixtures"


@pytest.fixture
def cfg():
    return Config(
        remote=Remote("https://x/", "https://y/", "main", "osism"),
        base_dirs=(str(FIXT),),
        release_version="latest",
        plugins={"catalog_role_missing": PluginCfg(enabled=True)},
        sources={},
        releases=("A", "B"),
    )


@pytest.fixture
def cfg_no_releases(cfg, monkeypatch):
    # An empty releases tuple alone would make Config.releases fall through to
    # enablement.release_range's local-listing derivation (a real filesystem
    # walk this test has no reason to depend on); stubbing release_range
    # itself keeps the "empty range" case isolated to what the plugin does
    # with it, per the fail-loud contract under test here.
    monkeypatch.setattr(plugin.enablement, "release_range", lambda config: [])
    return Config(
        remote=cfg.remote,
        base_dirs=cfg.base_dirs,
        release_version=cfg.release_version,
        plugins=cfg.plugins,
        sources=cfg.sources,
        releases=(),
    )


def _serve(
    monkeypatch,
    *,
    collections=None,
    validators=None,
    iface=None,
    kolla_files=None,
    osism_files=None,
    ceph_files=None,
    kubernetes_files=None,
):
    """Stub the catalog/interface data _checks() reads, leaving
    playbooks.resolves()/validate_resolves() themselves real.

    `iface` is {release: {role, ...}} -- a plain set of role names is enough
    for playbooks.resolves()'s membership check (and its "ceph" special case,
    which probes "ceph-ceph" the same way); it does not need the real
    role->frozenset(environments) shape runtime_interface() returns, since no
    check here inspects an environment. Missing release keys default to no
    roles resolving, so a test only has to spell out the releases it cares
    about.
    """
    # A bare name is shorthand for an unbounded role, which is what every
    # role was before osism/python-osism#2688 and what most still are. Tests
    # about release bounds build catalog.CatalogRole values explicitly.
    coerced = {
        name: [
            r if isinstance(r, catalog.CatalogRole) else catalog.CatalogRole(r)
            for r in roles
        ]
        for name, roles in (collections or {}).items()
    }
    monkeypatch.setattr(plugin.catalog, "collections", lambda config: coerced)
    monkeypatch.setattr(plugin.catalog, "validators", lambda config: validators or {})
    # _interface_src() calls source.release_to_ref("kolla_ansible", ...) to
    # name the ref each release's finding was reconstructed at; stubbed here
    # since these tests' fake releases ("A", "B") have no real kolla-ansible
    # ref to resolve.
    monkeypatch.setattr(
        plugin.source, "release_to_ref", lambda repo, release, config: f"ref-{release}"
    )

    iface = iface or {}
    monkeypatch.setattr(
        plugin.playbooks,
        "runtime_interface",
        lambda release, config: iface.get(release, set()),
    )
    monkeypatch.setattr(
        plugin.playbooks,
        "kolla_files",
        lambda release, config: frozenset(kolla_files or ()),
    )
    monkeypatch.setattr(
        plugin.playbooks,
        "osism_files",
        lambda config: frozenset(osism_files or ()),
    )
    monkeypatch.setattr(
        plugin.playbooks,
        "ceph_files",
        lambda config: frozenset(ceph_files or ()),
    )
    monkeypatch.setattr(
        plugin.playbooks,
        "kubernetes_files",
        lambda config: frozenset(kubernetes_files or ()),
    )


def test_release_differential_is_one_entry_per_collection(cfg, monkeypatch):
    """redis resolves at A, not at B -> one entry, both release lists in `found`."""
    _serve(
        monkeypatch,
        collections={"nutshell": ["redis"], "cloudpod": ["redis"]},
        iface={"A": {"redis"}, "B": set()},
    )
    drifts = plugin.run(cfg, Allowlist(()))
    # Emitted order, not re-sorted by the test: proves the plugin's own
    # (alias, image) sort, which "cloudpod" < "nutshell" alone would not
    # distinguish from a (image, alias) sort since both share image "redis".
    assert [(d.alias, d.image) for d in drifts] == [
        ("cloudpod", "redis"),
        ("nutshell", "redis"),
    ]
    assert "unresolvable at B" in drifts[0].found
    assert "resolves at A" in drifts[0].found
    # The release lists must reach report.py's text-mode grouping key too --
    # not just the JSON-only expected/found fields.
    assert "unresolvable at B" in drifts[0].summary
    assert "resolves at A" in drifts[0].summary
    assert "MAP_ROLE2ROLE[cloudpod]" in drifts[0].found_src
    assert drifts[0].severity == "actionable"
    # expected_src must name the refs the interface was reconstructed at
    # (spec's `(reconstructed) @ <refs>` entry shape), not just say
    # "reconstructed" with no way to reproduce the comparison.
    assert "ref-A" in drifts[0].expected_src
    assert "ref-B" in drifts[0].expected_src


def test_dead_everywhere_gets_its_own_summary(cfg, monkeypatch):
    _serve(
        monkeypatch, collections={"nutshell": ["gone"]}, iface={"A": set(), "B": set()}
    )
    assert "no runtime has advertised" in plugin.run(cfg, Allowlist(()))[0].summary


def test_resolving_role_is_no_finding(cfg, monkeypatch):
    # A resolving validator alongside the resolving role: a regression that
    # emitted a validator finding unconditionally would still leave this
    # green without it.
    _serve(
        monkeypatch,
        collections={"nutshell": ["ok"]},
        validators={
            "ntp": {
                "runtime": "osism-ansible",
                "environment": "generic",
                "playbook": "validate-ntp",
            }
        },
        iface={"A": {"ok"}, "B": {"ok"}},
        osism_files={"generic-validate-ntp.yml"},
    )
    assert plugin.run(cfg, Allowlist(())) == []


def test_validator_finding_image_is_playbook_alias_is_key(cfg, monkeypatch):
    """`image` is the effective playbook name that gets checked (and that the
    report shows); `alias` is the VALIDATE_PLAYBOOKS key. Provenance and
    remediation are the validator branch's own, not the collections branch's
    (a validator entry is not a collection membership problem)."""
    _serve(
        monkeypatch,
        validators={
            "ntp": {
                "runtime": "osism-ansible",
                "environment": "generic",
                "playbook": "validate-ntp",
            }
        },
        osism_files=set(),
    )
    d = plugin.run(cfg, Allowlist(()))[0]
    assert d.image == "validate-ntp" and d.alias == "ntp"
    assert d.found_src == f"{plugin._ENUMS_SRC} VALIDATE_PLAYBOOKS[ntp]"
    assert "osism-ansible" in d.expected_src
    assert d.remediation == plugin._VALIDATOR_REMEDIATION


def test_validator_checked_against_its_own_runtime(cfg, monkeypatch):
    """A name only the wrong runtime ships is still a finding."""
    _serve(
        monkeypatch,
        validators={
            "ntp": {
                "runtime": "osism-ansible",
                "environment": "generic",
                "playbook": "validate-ntp",
            }
        },
        kolla_files={"kolla-validate-ntp.yml"},
        osism_files=set(),
    )
    drifts = plugin.run(cfg, Allowlist(()))
    assert [(d.image, d.alias, d.found) for d in drifts] == [
        ("validate-ntp", "ntp", "unresolvable at A, B"),
    ]


def test_duplicate_role_in_one_collection_is_one_finding(cfg, monkeypatch):
    """catalog.collections() can list the same role twice under one
    collection (Role() reachable at more than one depth); it must not
    surface as two identical findings."""
    _serve(
        monkeypatch,
        collections={"nutshell": ["redis", "redis"]},
        iface={"A": set(), "B": set()},
    )
    assert len(plugin.run(cfg, Allowlist(()))) == 1


def test_allowlist_is_applied(cfg, monkeypatch):
    _serve(monkeypatch, collections={"nutshell": ["redis"]}, iface={"A": set()})
    al = Allowlist(
        (
            AllowEntry(
                plugin="catalog_role_missing",
                image="redis",
                alias="nutshell",
                reason="tracked",
            ),
        )
    )
    assert plugin.run(cfg, al)[0].allowlisted


def test_empty_release_range_raises(cfg_no_releases):
    with pytest.raises(SourceError, match="empty supported release range"):
        plugin.run(cfg_no_releases, Allowlist(()))


# --- release-bounded roles -------------------------------------------------
#
# These use real catalog.CatalogRole values rather than the bare strings the
# older _serve() calls above pass, because the defect they guard against is
# precisely that the plugin consumed a role as an opaque name and checked it
# at every release regardless of the bounds attached to it. They also use real
# "YYYY.N" release identifiers rather than this file's "A"/"B" placeholders:
# a bound is a statement about release ordering, so testing it against strings
# that carry no order would test a fiction.


@pytest.fixture
def cfg_real(cfg):
    return Config(
        remote=cfg.remote,
        base_dirs=cfg.base_dirs,
        release_version=cfg.release_version,
        plugins=cfg.plugins,
        sources=cfg.sources,
        releases=("2024.1", "2025.1", "2025.2", "2026.1"),
    )


def _role(name, since=None, until=None):
    return catalog.CatalogRole(name, since, until)


def test_bounded_role_is_not_checked_outside_its_bounds(cfg_real, monkeypatch):
    """The redis -> valkey cut-over, as python-osism#2688 actually writes it.

    kolla replaced redis with valkey at 2025.2, so the catalog carries both
    with complementary bounds and each resolves exactly where it is deployed.
    Checking either at a release it is not deployed on asks whether a runtime
    advertises a role the collection would never request there -- the six
    false findings of 2026-09-11.
    """
    _serve(
        monkeypatch,
        collections={
            "nutshell": [
                _role("redis", until="2025.1"),
                _role("valkey", since="2025.2"),
            ]
        },
        iface={
            "2024.1": {"redis"},
            "2025.1": {"redis"},
            "2025.2": {"valkey"},
            "2026.1": {"valkey"},
        },
    )
    assert plugin.run(cfg_real, Allowlist(())) == []


def test_bounded_role_still_reports_inside_its_bounds(cfg_real, monkeypatch):
    """Narrowing the checked range must not become a way to miss real drift:
    a role deployed through 2025.2 that resolves nowhere in that range is
    still dead and must still report."""
    _serve(
        monkeypatch,
        collections={"nutshell": [_role("redis", until="2025.2")]},
        iface={},
    )
    drifts = plugin.run(cfg_real, Allowlist(()))
    assert [(d.alias, d.image) for d in drifts] == [("nutshell", "redis")]
    assert "no runtime has advertised" in drifts[0].summary


def test_bounded_role_reports_only_the_releases_it_is_deployed_on(
    cfg_real, monkeypatch
):
    """A role deployed from 2025.2 onwards and missing at 2026.1 reports
    2026.1 alone. The releases below its `since` are out of scope, and listing
    them would pad the finding with releases the collection never asks about
    -- which is what made the redis finding read as a real portability gap."""
    _serve(
        monkeypatch,
        collections={"nutshell": [_role("valkey", since="2025.2")]},
        iface={"2025.2": {"valkey"}},
    )
    drifts = plugin.run(cfg_real, Allowlist(()))
    assert len(drifts) == 1
    assert "unresolvable at 2026.1" in drifts[0].found
    assert "resolves at 2025.2" in drifts[0].found
    assert "2024.1" not in drifts[0].found
    assert "2025.1" not in drifts[0].found


def test_role_deployed_on_no_supported_release_is_a_finding(cfg_real, monkeypatch):
    """An empty admitted range is a dead catalog entry, not a pass. A role
    whose bounds exclude every supported release can never deploy, so
    checking nothing and reporting nothing would hide it -- the same
    under-report the reader's fail-loud contract rules out upstream."""
    _serve(
        monkeypatch,
        collections={"nutshell": [_role("ancient", until="2023.2")]},
        iface={r: {"ancient"} for r in cfg_real.releases},
    )
    drifts = plugin.run(cfg_real, Allowlist(()))
    assert len(drifts) == 1
    assert drifts[0].image == "ancient"
    # The bounds-dead summary, not the no-runtime-advertises-it one: nothing
    # is wrong with the runtime images here, so pointing the reader at them
    # would send them looking in the wrong repo.
    assert "bounds exclude every supported release" in drifts[0].summary
    assert "no runtime has advertised" not in drifts[0].summary


def test_unparseable_release_bound_raises(cfg_real, monkeypatch):
    """A bound the plugin cannot order against the release range stops the
    run. Silently treating it as unbounded would resurrect the exact defect
    this change fixes, one layer down."""
    _serve(
        monkeypatch,
        collections={"nutshell": [_role("redis", until="stable/queens")]},
        iface={},
    )
    with pytest.raises(SourceError, match="redis"):
        plugin.run(cfg_real, Allowlist(()))
