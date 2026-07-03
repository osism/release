from pathlib import Path

from osism_drift.config import load_config
from osism_drift.drift import IMAGE_PLUGINS, KOLLA_PLUGINS, PLUGIN_GROUPS


def test_registry_is_a_list():
    assert isinstance(KOLLA_PLUGINS, list)


def test_each_plugin_has_required_metadata():
    for p in KOLLA_PLUGINS:
        assert isinstance(p.NAME, str) and p.NAME
        assert isinstance(p.DESCRIPTION, str) and p.DESCRIPTION
        assert isinstance(p.INPUT_FILES, list) and p.INPUT_FILES
        assert callable(p.run)


def test_kolla_inner_plugin_registered():
    assert "kolla_version_chain_inner" in [p.NAME for p in KOLLA_PLUGINS]


def test_kolla_upstream_plugin_registered():
    assert "kolla_version_chain_upstream" in [p.NAME for p in KOLLA_PLUGINS]


def test_kolla_inventory_plugin_registered():
    assert "kolla_inventory" in [p.NAME for p in KOLLA_PLUGINS]


def test_kolla_enablement_build_plugin_registered():
    assert "kolla_enablement_build" in [p.NAME for p in KOLLA_PLUGINS]


def test_kolla_enablement_orphan_plugin_registered():
    assert "kolla_enablement_orphan" in [p.NAME for p in KOLLA_PLUGINS]


def test_kolla_secrets_orphan_plugin_registered():
    assert "kolla_secrets_orphan" in [p.NAME for p in KOLLA_PLUGINS]


def test_kolla_orphan_config_plugin_registered():
    assert "kolla_orphan_config" in [p.NAME for p in KOLLA_PLUGINS]


def test_kolla_groupvars_missing_plugin_registered():
    assert "kolla_groupvars_missing" in [p.NAME for p in KOLLA_PLUGINS]


def test_every_plugin_has_summary_and_remediation():
    for p in KOLLA_PLUGINS:
        assert isinstance(p.SUMMARY, str) and p.SUMMARY.strip(), p.NAME
        assert "{n}" in p.SUMMARY, p.NAME
        assert isinstance(p.REMEDIATION, str) and p.REMEDIATION.strip(), p.NAME


def test_default_config_enables_every_registered_plugin():
    # The driver only runs a plugin that is present and enabled in the config, so
    # a plugin registered in KOLLA_PLUGINS but missing from the default config would
    # silently never run. Guard against that gap.
    cfg_path = Path(__file__).resolve().parents[2] / "src" / "drift-config.yml"
    config = load_config(cfg_path)
    for p in KOLLA_PLUGINS:
        entry = config.plugins.get(p.NAME)
        assert entry is not None, f"{p.NAME} missing from default config"
        assert entry.enabled, f"{p.NAME} not enabled in default config"


def test_plugins_in_lifecycle_order():
    assert [p.NAME for p in KOLLA_PLUGINS] == [
        "kolla_enablement_orphan",
        "kolla_groupvars_missing",
        "kolla_orphan_config",
        "kolla_image_orphan",
        "kolla_secrets_orphan",
        "kolla_enablement_build",
        "kolla_version_chain_upstream",
        "kolla_version_chain_inner",
        "kolla_inventory",
    ]


def test_plugin_groups_match_registries_and_are_disjoint():
    assert PLUGIN_GROUPS["kolla"] == KOLLA_PLUGINS
    assert set(p.NAME for p in IMAGE_PLUGINS).isdisjoint(p.NAME for p in KOLLA_PLUGINS)
