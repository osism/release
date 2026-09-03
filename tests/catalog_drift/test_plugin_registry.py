from pathlib import Path

import yaml

from osism_drift.drift import CATALOG_PLUGINS, PLUGIN_GROUPS, REPORT_HEADERS

ROOT = Path(__file__).resolve().parents[2]
_CONFIG_PATH = ROOT / "src" / "drift-config.yml"


def test_catalog_role_missing_plugin_registered():
    assert "catalog_role_missing" in [p.NAME for p in CATALOG_PLUGINS]


def test_catalog_plugin_group_matches_registry():
    assert PLUGIN_GROUPS["catalog"] == CATALOG_PLUGINS


def test_catalog_report_header_exists():
    assert (
        isinstance(REPORT_HEADERS["catalog"], str) and REPORT_HEADERS["catalog"].strip()
    )


def test_catalog_role_missing_enabled_in_config():
    cfg = yaml.safe_load(_CONFIG_PATH.read_text())
    assert cfg["plugins"]["catalog_role_missing"]["enabled"] is True


def test_each_plugin_has_required_metadata():
    # driver.py:52 skips any plugin absent from or disabled in drift-config.yml,
    # so a plugin registered here but dropped from the config is silently
    # never run -- test_catalog_role_missing_enabled_in_config guards that gap;
    # this guards the plugin's own metadata contract report.py relies on.
    for p in CATALOG_PLUGINS:
        assert isinstance(p.NAME, str) and p.NAME
        assert isinstance(p.DESCRIPTION, str) and p.DESCRIPTION
        assert isinstance(p.INPUT_FILES, list) and p.INPUT_FILES
        assert isinstance(p.SUMMARY, str) and p.SUMMARY.strip(), p.NAME
        assert "{n}" in p.SUMMARY, p.NAME
        assert isinstance(p.REMEDIATION, str) and p.REMEDIATION.strip(), p.NAME
        assert callable(p.run)
