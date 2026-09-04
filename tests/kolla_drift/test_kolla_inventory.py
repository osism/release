from pathlib import Path
import pytest
import responses
from osism_drift.config import (
    Allowlist,
    AllowEntry,
    Config,
    PluginCfg,
    Remote,
    SourceCfg,
)
from osism_drift.drift import kolla_inventory as plugin

FIXT = Path(__file__).parent / "fixtures"
API = "https://api.github.com/repos"
RAW = "https://raw.githubusercontent.com"

# FIXT/generics/inventory/{50,51}-kolla define: control, nova:children, compute.
LOCAL_GROUPS = "control, nova:children, compute"


def _cfg(releases=("A", "B"), base_dirs=(str(FIXT),)):
    return Config(
        remote=Remote(f"{RAW}/", f"{API}/", "main", "osism"),
        base_dirs=base_dirs,
        # FIXT/kolla-ansible is a plain dir, not a git checkout, and the repo is
        # pinned -> it cannot be served from named refs locally, so it falls to
        # the mocked remote.
        remote_fallback=True,
        release_version="latest",
        plugins={"kolla_inventory": PluginCfg(enabled=True)},
        sources={"kolla_ansible": SourceCfg(owner="openstack", branch="stable/2025.2")},
        releases=releases,
    )


def _mock_release(release, multinode):
    """Serve upstream multinode for one release at its probed stable/ ref."""
    ref = f"stable/{release}"
    responses.add(
        responses.GET, f"{API}/openstack/kolla-ansible/commits/{ref}", status=200
    )
    responses.add(
        responses.GET,
        f"{RAW}/openstack/kolla-ansible/{ref}/ansible/inventory/multinode",
        body=multinode.encode(),
        status=200,
    )


@responses.activate
def test_flags_upstream_only_groups_across_the_release_range():
    # The finding set is the union over the range: neither release alone holds
    # all three, and a single-ref check would report only one release's share.
    _mock_release(
        "A", "[cyborg:children]\ncyborg-agent\n\n[cyborg-agent:children]\ncompute\n"
    )
    _mock_release("B", "[ironic-dnsmasq:children]\ncontrol\n")
    drifts = plugin.run(_cfg(), Allowlist(()))
    assert sorted(d.image for d in drifts) == [
        "cyborg-agent:children",
        "cyborg:children",
        "ironic-dnsmasq:children",
    ]


@responses.activate
def test_entry_names_only_the_releases_that_want_the_group():
    _mock_release("A", "[control]\nctl01\n")  # nothing missing
    _mock_release("B", "[ironic-dnsmasq:children]\ncontrol\n")
    drifts = plugin.run(_cfg(), Allowlist(()))
    d = next(d for d in drifts if d.image == "ironic-dnsmasq:children")
    assert "B (stable/B)" in d.expected
    assert d.expected_src.endswith("multinode @ B (stable/B)")
    assert "A (stable/A)" not in d.expected_src


@responses.activate
def test_group_renamed_upstream_is_flagged_at_the_release_that_renamed_it(tmp_path):
    # The 2026.1 kolla-toolbox -> kolla_toolbox rename: OSISM ships one inventory
    # for every supported release, so the old spelling matching an older release
    # must not hide the new spelling the newer release selects on. A check pinned
    # to one ref sees at most one of the two.
    inv = tmp_path / "generics" / "inventory"
    inv.mkdir(parents=True)
    (inv / "50-kolla").write_text("[control]\nctl01\n")
    (inv / "51-kolla").write_text("[kolla-toolbox:children]\ncontrol\n")
    _mock_release("A", "[kolla-toolbox:children]\ncontrol\n")
    _mock_release("B", "[kolla_toolbox:children]\ncontrol\n")
    drifts = plugin.run(_cfg(base_dirs=(str(tmp_path), str(FIXT))), Allowlist(()))
    assert [d.image for d in drifts] == ["kolla_toolbox:children"]
    assert "B (stable/B)" in drifts[0].expected


@responses.activate
def test_members_come_from_the_last_release_that_wants_the_group():
    # Members tell a maintainer what to add, so they must describe the newest
    # release's shape, not a stale earlier one.
    _mock_release("A", "[ironic-dnsmasq:children]\nnetwork\n")
    _mock_release("B", "[ironic-dnsmasq:children]\ncontrol\n")
    drifts = plugin.run(_cfg(), Allowlist(()))
    assert len(drifts) == 1
    assert "members: control" in drifts[0].expected
    assert "network" not in drifts[0].expected


@responses.activate
def test_red_entry_carries_members():
    _mock_release("A", "[control]\nctl01\n")
    _mock_release("B", "[ironic-dnsmasq:children]\ncontrol\n")
    drifts = plugin.run(_cfg(), Allowlist(()))
    red = next(d for d in drifts if d.image == "ironic-dnsmasq:children")
    assert "members:" in red.expected
    assert "control" in red.expected


@responses.activate
def test_exact_and_prefix_allowlist_match():
    # The entry key stays the bare group name across the range change, so the
    # shipped allowlist keeps matching; a per-release key would strand all of it.
    _mock_release(
        "A", "[cyborg:children]\ncyborg-agent\n\n[cyborg-agent:children]\ncompute\n"
    )
    _mock_release("B", "[ironic-dnsmasq:children]\ncontrol\n")
    al = Allowlist(
        (
            AllowEntry(
                plugin="kolla_inventory",
                image="cyborg",
                reason="not deployed",
                match="prefix",
            ),
        )
    )
    drifts = plugin.run(_cfg(), al)
    by = {d.image: d for d in drifts}
    assert by["cyborg:children"].allowlisted is True  # prefix matches the service group
    assert (
        by["cyborg-agent:children"].allowlisted is True
    )  # prefix covers the sub-group
    assert by["ironic-dnsmasq:children"].allowlisted is False


def test_empty_release_range_raises(tmp_path):
    from osism_drift.source import SourceError

    # An empty range reads no multinode at all, so every missing group would go
    # unreported: the detector must fail loud instead of reporting clean.
    empty = tmp_path / "release" / "latest"
    empty.mkdir(parents=True)
    cfg = _cfg(releases=(), base_dirs=(str(tmp_path), str(FIXT)))
    with pytest.raises(SourceError, match="empty supported release range"):
        plugin.run(cfg, Allowlist(()))


def test_declares_the_release_repo_it_reads(tmp_path):
    from osism_drift.source import SourceError

    # The release range is read from the release manifests, so `release` must be
    # in INPUT_FILES: the driver's pre-flight resolution keys off that list, and
    # an undeclared repo turns a clean "not found under any --base-dir" into a
    # failure mid-comparison.
    assert (
        "release",
        "latest/openstack-*.yml (supported release range)",
    ) in plugin.INPUT_FILES

    inv = tmp_path / "generics" / "inventory"
    inv.mkdir(parents=True)
    (inv / "50-kolla").write_text("[control]\nctl01\n")
    (inv / "51-kolla").write_text("[control]\nctl01\n")
    # remote_fallback stays off so the missing repo raises offline instead of
    # reaching for the network.
    cfg = Config(
        remote=Remote(f"{RAW}/", f"{API}/", "main", "osism"),
        base_dirs=(str(tmp_path),),
        release_version="latest",
        plugins={"kolla_inventory": PluginCfg(enabled=True)},
        sources={},
        releases=(),
    )
    with pytest.raises(SourceError, match="repo 'release' not found"):
        plugin.run(cfg, Allowlist(()))
