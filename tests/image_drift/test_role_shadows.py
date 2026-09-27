from pathlib import Path

import pytest
from osism_drift.config import (
    Allowlist,
    AllowEntry,
    Config,
    PluginCfg,
    Remote,
    SourceCfg,
)
from osism_drift.drift import role_shadows

FIXT = Path(__file__).parent / "fixtures"


@pytest.fixture
def cfg(playbooks_base):
    return Config(
        remote=Remote("https://x/", "https://y/", "main", "osism"),
        base_dirs=(str(FIXT), str(playbooks_base)),
        release_version="latest",
        plugins={"role_shadows": PluginCfg(enabled=True)},
        sources={"ansible_playbooks": SourceCfg(branch="main")},
    )


def test_baseline_drift(cfg):
    drifts = role_shadows.run(cfg, Allowlist(()))
    by_alias = sorted((d.image, d.alias, d.found_src.split("/")[-3]) for d in drifts)
    assert by_alias == [
        ("adminer", "adminer", "adminer"),
        ("floatdemo", "floatdemo", "floatdemo"),
        ("mariadb", "ara_server_mariadb", "manager"),
        ("oteldemo", "oteldemo", "oteldemo"),
        ("redis", "manager_redis", "manager"),
        ("redis", "netbox_redis", "netbox"),
    ]


def test_role_without_defaults_skipped(cfg):
    drifts = role_shadows.run(cfg, Allowlist(()))
    assert all("cephclient" not in d.found_src for d in drifts)


def test_jinja_valued_tag_not_drift(cfg):
    drifts = role_shadows.run(cfg, Allowlist(()))
    assert not any(d.image == "ara_server" for d in drifts)


def test_allowlist_very_narrow_pins_to_role_file(cfg):
    src = "ansible-collection-services/roles/manager/defaults/main.yml"
    al = Allowlist(
        (
            AllowEntry(
                plugin="role_shadows",
                image="redis",
                alias="manager_redis",
                found_src=src,
                reason="ops",
            ),
        )
    )
    drifts = role_shadows.run(cfg, al)
    redis_drifts = [d for d in drifts if d.image == "redis"]
    allowlisted = [d for d in redis_drifts if d.allowlisted]
    not_allowlisted = [d for d in redis_drifts if not d.allowlisted]
    assert len(allowlisted) == 1 and allowlisted[0].alias == "manager_redis"
    assert len(not_allowlisted) == 1 and not_allowlisted[0].alias == "netbox_redis"


def _by_alias(drifts):
    return {d.alias: d for d in drifts}


def test_netbox_redis_is_live(cfg):
    """generics' manager template emits netbox_redis_tag, but `osism apply
    netbox` runs in infrastructure (manager-netbox.yml is in SKIP), which never
    loads that render: the role default deploys."""
    drifts = _by_alias(role_shadows.run(cfg, Allowlist(())))
    d = drifts["netbox_redis"]
    assert "LIVE" in d.summary
    assert "versions.yml.j2" in d.remediation


def test_dormant_aliases_are_dormant(cfg):
    """adminer: carried as adminer_tag in versions.yml.j2. oteldemo: carried as
    oteldemo_version. ara_server_mariadb, manager_redis: generics' manager
    template, and the manager role runs in the manager environment."""
    drifts = _by_alias(role_shadows.run(cfg, Allowlist(())))
    for alias in ("adminer", "oteldemo", "ara_server_mariadb", "manager_redis"):
        d = drifts[alias]
        assert "DORMANT" in d.summary, f"{alias}: expected DORMANT summary"
        assert "convenient" in d.remediation, f"{alias}: expected dormant remediation"


def test_floatdemo_is_live(cfg):
    """floatdemo has found='latest' and no release transport carries it.

    FLOATING class is removed; without an override the alias is LIVE.
    """
    drifts = _by_alias(role_shadows.run(cfg, Allowlist(())))
    d = drifts["floatdemo"]
    assert "LIVE" in d.summary
    assert "versions.yml.j2" in d.remediation


def test_stream_resolved_not_emitted(cfg):
    """osism_ansible is stream-resolved; not emitted even when role default disagrees."""
    drifts = _by_alias(role_shadows.run(cfg, Allowlist(())))
    assert "osism_ansible" not in drifts


def test_dormant_findings_are_advisory(cfg):
    drifts = _by_alias(role_shadows.run(cfg, Allowlist(())))
    for alias in ("adminer", "oteldemo", "ara_server_mariadb", "manager_redis"):
        assert drifts[alias].severity == "advisory", alias


def test_live_findings_are_actionable(cfg):
    drifts = _by_alias(role_shadows.run(cfg, Allowlist(())))
    for alias in ("netbox_redis", "floatdemo"):
        assert drifts[alias].severity == "actionable", alias


def test_generics_alias_outside_manager_environment_is_live(cfg, monkeypatch):
    """adminer is emitted by generics' manager template too; if versions.yml.j2
    stopped carrying it, that render would not reach `osism apply adminer`
    (infrastructure), so the finding must turn LIVE, not stay DORMANT."""
    monkeypatch.setattr(role_shadows.role_scan, "runner_pins", lambda config: {})
    drifts = _by_alias(role_shadows.run(cfg, Allowlist(())))
    assert "LIVE" in drifts["adminer"].summary


def test_equal_validations_pin_is_not_drift(cfg):
    """tempest_osism_tag resolves to the release key tempest (via
    versions.yml.j2) and matches it, so role_shadows reports nothing."""
    drifts = _by_alias(role_shadows.run(cfg, Allowlist(())))
    assert "tempest_osism" not in drifts
