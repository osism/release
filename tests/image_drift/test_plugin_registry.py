from pathlib import Path

import yaml

from osism_drift.drift import IMAGE_PLUGINS, PLUGIN_GROUPS

ROOT = Path(__file__).resolve().parents[2]
_CONFIG_PATH = ROOT / "src" / "drift-config.yml"


def test_image_plugins_registered():
    names = [p.NAME for p in IMAGE_PLUGINS]
    assert "release_vs_manager" in names
    assert "role_shadows" in names
    assert "role_unpinned" in names
    assert "rolling_pin" in names
    assert "image_orphan" in names


def test_image_plugin_group_matches_registry():
    assert PLUGIN_GROUPS["image"] == IMAGE_PLUGINS


def test_role_unpinned_enabled_in_config():
    cfg = yaml.safe_load(_CONFIG_PATH.read_text())
    assert cfg["plugins"]["role_unpinned"]["enabled"] is True


def test_image_orphan_enabled_in_config():
    cfg = yaml.safe_load(_CONFIG_PATH.read_text())
    assert cfg["plugins"]["image_orphan"]["enabled"] is True


def test_rolling_pin_enabled_in_config():
    cfg = yaml.safe_load(_CONFIG_PATH.read_text())
    assert cfg["plugins"]["rolling_pin"]["enabled"] is True


def test_each_plugin_has_required_metadata():
    for p in IMAGE_PLUGINS:
        assert isinstance(p.NAME, str) and p.NAME
        assert isinstance(p.DESCRIPTION, str) and p.DESCRIPTION
        assert isinstance(p.INPUT_FILES, list) and p.INPUT_FILES
        assert isinstance(p.SUMMARY, str) and p.SUMMARY.strip(), p.NAME
        assert "{n}" in p.SUMMARY, p.NAME
        assert isinstance(p.REMEDIATION, str) and p.REMEDIATION.strip(), p.NAME
        assert callable(p.run)
