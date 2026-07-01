from pathlib import Path
import pytest
from osism_drift.config import Allowlist, AllowEntry, Config, Remote, PluginCfg
from osism_drift.drift import kolla_version_chain_upstream as plugin

FIXT = Path(__file__).parent / "fixtures"


@pytest.fixture
def cfg():
    # kolla is NOT pinned here, so list_dir reads the local fixture dir (offline).
    return Config(
        remote=Remote("https://raw/", "https://api/", "main", "osism"),
        base_dirs=(str(FIXT),),
        release_version="latest",
        plugins={"kolla_version_chain_upstream": PluginCfg(enabled=True)},
        sources={},
    )


def test_flags_services_without_template_key(cfg):
    drifts = plugin.run(cfg, Allowlist(()))
    images = sorted(d.image for d in drifts)
    assert images == ["ignored_svc", "newsvc"]  # present_a has a key
    assert all("macros" not in d.image for d in drifts)  # docker/macros.j2 excluded


def test_expected_src_uses_configured_ref(cfg):
    # cfg leaves kolla unpinned, so the ref is the remote default branch (main).
    # The label must reflect that, proving it is config-derived rather than a
    # hardcoded stable/2025.2.
    drifts = plugin.run(cfg, Allowlist(()))
    assert drifts
    assert all("@ main" in d.expected_src for d in drifts)
    assert all("2025.2" not in d.expected_src for d in drifts)


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
