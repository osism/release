from pathlib import Path

import pytest
from osism_drift.config import Allowlist, AllowEntry, Config, PluginCfg, Remote
from osism_drift.drift import role_shadows

FIXT = Path(__file__).parent / "fixtures"


@pytest.fixture
def cfg():
    return Config(
        remote=Remote("https://x/", "https://y/", "main", "osism"),
        base_dirs=(str(FIXT),),
        release_version="latest",
        plugins={"role_shadows": PluginCfg(enabled=True)},
        sources={},
    )


def test_baseline_drift(cfg):
    drifts = role_shadows.run(cfg, Allowlist(()))
    by_alias = sorted((d.image, d.alias, d.found_src.split("/")[-3]) for d in drifts)
    assert by_alias == [
        ("adminer", "adminer", "adminer"),
        ("floatdemo", "floatdemo", "floatdemo"),
        ("mariadb", "ara_server_mariadb", "manager"),
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
    drifts = _by_alias(role_shadows.run(cfg, Allowlist(())))
    d = drifts["netbox_redis"]
    assert "LIVE" in d.summary
    assert "images.yml.j2" in d.remediation


def test_dormant_aliases_are_dormant(cfg):
    drifts = _by_alias(role_shadows.run(cfg, Allowlist(())))
    for alias in ("adminer", "ara_server_mariadb", "manager_redis"):
        d = drifts[alias]
        assert "DORMANT" in d.summary, f"{alias}: expected DORMANT summary"
        assert "convenient" in d.remediation, f"{alias}: expected dormant remediation"


def test_floatdemo_is_live(cfg):
    """floatdemo has found='latest' and is absent from the override template.

    FLOATING class is removed; without an override the alias is LIVE.
    """
    drifts = _by_alias(role_shadows.run(cfg, Allowlist(())))
    d = drifts["floatdemo"]
    assert "LIVE" in d.summary
    assert "images.yml.j2" in d.remediation


def test_stream_resolved_not_emitted(cfg):
    """osism_ansible is stream-resolved; not emitted even when role default disagrees."""
    drifts = _by_alias(role_shadows.run(cfg, Allowlist(())))
    assert "osism_ansible" not in drifts
