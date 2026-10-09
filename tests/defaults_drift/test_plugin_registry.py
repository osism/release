from pathlib import Path

import yaml

from osism_drift.drift import DEFAULTS_PLUGINS, PLUGIN_GROUPS, REPORT_HEADERS

ROOT = Path(__file__).resolve().parents[2]
_CONFIG_PATH = ROOT / "src" / "drift-config.yml"


def test_defaults_orphan_registered():
    assert [p.NAME for p in DEFAULTS_PLUGINS] == ["defaults_orphan"]


def test_defaults_group_matches_registry():
    assert PLUGIN_GROUPS["defaults"] == DEFAULTS_PLUGINS


def test_defaults_report_header_exists():
    assert (
        isinstance(REPORT_HEADERS["defaults"], str)
        and REPORT_HEADERS["defaults"].strip()
    )


def test_defaults_orphan_enabled_in_config():
    cfg = yaml.safe_load(_CONFIG_PATH.read_text())
    assert cfg["plugins"]["defaults_orphan"]["enabled"] is True


def test_each_plugin_has_required_metadata():
    for p in DEFAULTS_PLUGINS:
        assert isinstance(p.NAME, str) and p.NAME
        assert isinstance(p.DESCRIPTION, str) and p.DESCRIPTION
        assert isinstance(p.INPUT_FILES, list) and p.INPUT_FILES
        assert "{n}" in p.SUMMARY
        assert isinstance(p.REMEDIATION, str) and p.REMEDIATION.strip()
        assert callable(p.run)
