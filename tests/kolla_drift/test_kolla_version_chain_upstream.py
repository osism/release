from pathlib import Path
import pytest
from osism_drift.config import (
    Allowlist,
    AllowEntry,
    Config,
    Remote,
    PluginCfg,
    SourceCfg,
)
from osism_drift.drift import kolla_version_chain_upstream as plugin

FIXT = Path(__file__).parent / "fixtures"
DOCKER = ["foo", "ignored-svc", "newsvc", "off", "present_a"]


@pytest.fixture
def cfg(kolla_clone):
    # The fixture release range is A and B. retired ships only at the older A,
    # so it must not be flagged: the plugin reads the newest release.
    base = kolla_clone({"stable/A": DOCKER + ["retired"], "stable/B": DOCKER})
    return Config(
        remote=Remote("https://raw/", "https://api/", "main", "osism"),
        base_dirs=(str(base), str(FIXT)),
        release_version="latest",
        plugins={"kolla_version_chain_upstream": PluginCfg(enabled=True)},
        sources={"kolla": SourceCfg(owner="openstack")},
    )


def test_flags_services_without_template_key(cfg):
    drifts = plugin.run(cfg, Allowlist(()))
    images = sorted(d.image for d in drifts)
    assert images == ["ignored_svc", "newsvc"]  # present_a has a key
    assert all("macros" not in d.image for d in drifts)  # docker/macros.j2 excluded


def test_reads_the_newest_supported_release(cfg):
    drifts = plugin.run(cfg, Allowlist(()))
    assert drifts
    assert all(d.image != "retired" for d in drifts)
    assert all("@ stable/B" in d.expected_src for d in drifts)


def test_present_service_not_flagged(cfg):
    drifts = plugin.run(cfg, Allowlist(()))
    assert all(d.image != "present_a" for d in drifts)


def test_allowlist_marks_allowlisted(cfg):
    al = Allowlist(
        (
            AllowEntry(
                plugin="kolla_version_chain_upstream",
                image="ignored_svc",
                reason="variant, not a service",
            ),
        )
    )
    drifts = plugin.run(cfg, al)
    by = {d.image: d for d in drifts}
    assert by["ignored_svc"].allowlisted is True
    assert by["newsvc"].allowlisted is False
