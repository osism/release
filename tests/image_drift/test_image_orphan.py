from pathlib import Path

import pytest
from osism_drift.config import Allowlist, AllowEntry, Config, PluginCfg, Remote
from osism_drift.drift import image_orphan

FIXT = Path(__file__).parent / "fixtures"


@pytest.fixture
def cfg():
    return Config(
        remote=Remote("https://x/", "https://y/", "main", "osism"),
        base_dirs=(str(FIXT),),
        release_version="latest",
        plugins={"image_orphan": PluginCfg(enabled=True)},
        sources={},
    )


def _by_alias(drifts):
    return {d.alias: d for d in drifts}


def test_orphan_alias_detected(cfg):
    """widget is emitted but no role references {{ widget_image }} — must appear."""
    drifts = _by_alias(image_orphan.run(cfg, Allowlist(())))
    assert "widget" in drifts
    d = drifts["widget"]
    assert d.found_src == "generics/environments/manager/images.yml"


def test_consumed_alias_not_detected(cfg):
    """adminer has {{ adminer_image }} in its role defaults — must not appear."""
    drifts = _by_alias(image_orphan.run(cfg, Allowlist(())))
    assert "adminer" not in drifts


def test_playbook_consumed_alias_not_detected(cfg):
    """pbonly is emitted and consumed only in ansible-playbooks-manager
    (not in any role) — the playbooks-manager scan must keep it out."""
    drifts = _by_alias(image_orphan.run(cfg, Allowlist(())))
    assert "pbonly" not in drifts


def test_consumer_in_an_unfiltered_file_is_found(cfg):
    """shellcons is emitted and its only {{ shellcons_image }} consumer lives
    in roles/shellcons/files/entrypoint.sh — not a .yml/.yaml/.j2 file. The
    scan must read it. Skipping such files to save reads would report a
    deployed image as an orphan, and this plugin's remediation is to delete
    the image definition."""
    drifts = _by_alias(image_orphan.run(cfg, Allowlist(())))
    assert "shellcons" not in drifts


def test_allowlist_suppresses_orphan(cfg):
    al = Allowlist(
        (
            AllowEntry(
                plugin="image_orphan",
                image="widget",
                alias=None,
                found_src=None,
                reason="intentional",
            ),
        )
    )
    drifts = _by_alias(image_orphan.run(cfg, al))
    assert "widget" in drifts
    assert drifts["widget"].allowlisted
