from pathlib import Path
import pytest
from osism_drift.config import Allowlist, AllowEntry, Config, Remote, PluginCfg
from osism_drift.drift import kolla_inventory as plugin

FIXT = Path(__file__).parent / "fixtures"


@pytest.fixture
def cfg():
    # kolla_ansible is NOT pinned here, so it reads the local fixture (offline).
    return Config(
        remote=Remote("https://raw/", "https://api/", "main", "osism"),
        base_dirs=(str(FIXT),),
        release_version="latest",
        plugins={"kolla_inventory": PluginCfg(enabled=True)},
        sources={},
    )


def test_flags_upstream_only_groups(cfg):
    drifts = plugin.run(cfg, Allowlist(()))
    assert sorted(d.image for d in drifts) == [
        "cyborg-agent:children",
        "cyborg:children",
        "ironic-dnsmasq:children",
    ]


def test_expected_src_uses_configured_ref(cfg):
    # kolla_ansible is unpinned here, so the ref is the remote default branch
    # (main) — the label must be config-derived, not a hardcoded stable/2025.2.
    drifts = plugin.run(cfg, Allowlist(()))
    assert drifts
    assert all(d.expected_src.endswith("@ main") for d in drifts)
    assert all("2025.2" not in d.expected_src for d in drifts)


def test_red_entry_carries_members(cfg):
    drifts = plugin.run(cfg, Allowlist(()))
    red = next(d for d in drifts if d.image == "ironic-dnsmasq:children")
    assert "members:" in red.expected
    assert "control" in red.expected


def test_exact_and_prefix_allowlist_match(cfg):
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
    drifts = plugin.run(cfg, al)
    by = {d.image: d for d in drifts}
    assert by["cyborg:children"].allowlisted is True  # prefix matches the service group
    assert (
        by["cyborg-agent:children"].allowlisted is True
    )  # prefix covers the sub-group
    assert by["ironic-dnsmasq:children"].allowlisted is False
