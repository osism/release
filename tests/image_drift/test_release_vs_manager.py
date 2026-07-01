from pathlib import Path

import pytest
from osism_drift.config import Allowlist, AllowEntry, Config, PluginCfg, Remote
from osism_drift.drift import release_vs_manager

FIXT = Path(__file__).parent / "fixtures"


@pytest.fixture
def cfg():
    return Config(
        remote=Remote("https://x/", "https://y/", "main", "osism"),
        base_dirs=(str(FIXT),),
        release_version="latest",
        plugins={"release_vs_manager": PluginCfg(enabled=True)},
        sources={},
    )


def test_baseline_drift(cfg):
    drifts = release_vs_manager.run(cfg, Allowlist(()))
    keys = sorted((d.image, d.alias) for d in drifts)
    assert keys == [
        ("ara_server", "ara_server"),
        ("osism_ansible", "osism_ansible"),
        ("redis", "manager_redis"),
        ("redis", "netbox_redis"),
    ]


def test_allowlist_broad_suppresses_all_redis(cfg):
    al = Allowlist(
        (AllowEntry(plugin="release_vs_manager", image="redis", reason="x"),)
    )
    drifts = release_vs_manager.run(cfg, al)
    assert all(d.image != "redis" or d.allowlisted for d in drifts)
    assert sum(1 for d in drifts if d.image == "redis" and d.allowlisted) == 2


def test_allowlist_narrow_suppresses_one_alias_only(cfg):
    al = Allowlist(
        (
            AllowEntry(
                plugin="release_vs_manager",
                image="redis",
                alias="manager_redis",
                reason="x",
            ),
        )
    )
    drifts = release_vs_manager.run(cfg, al)
    allowlisted = [d for d in drifts if d.allowlisted]
    not_allowlisted = [d for d in drifts if not d.allowlisted]
    assert any(d.alias == "manager_redis" for d in allowlisted)
    assert any(d.alias == "netbox_redis" for d in not_allowlisted)


def test_latest_override_emitted_with_found_latest(cfg):
    drifts = release_vs_manager.run(cfg, Allowlist(()))
    osism_a = [d for d in drifts if d.image == "osism_ansible"]
    assert len(osism_a) == 1
    assert osism_a[0].found == "latest"
    assert osism_a[0].expected == "0.20260322.0"


def test_disabled_image_not_in_release_skipped(cfg):
    drifts = release_vs_manager.run(cfg, Allowlist(()))
    images = {d.image for d in drifts}
    assert "nonexistent_image" not in images
