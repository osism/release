from pathlib import Path

import pytest
from osism_drift.config import Allowlist, AllowEntry, Config, PluginCfg, Remote
from osism_drift.drift import role_unpinned

FIXT = Path(__file__).parent / "fixtures"


@pytest.fixture
def cfg():
    return Config(
        remote=Remote("https://x/", "https://y/", "main", "osism"),
        base_dirs=(str(FIXT),),
        release_version="latest",
        plugins={"role_unpinned": PluginCfg(enabled=True)},
        sources={},
    )


def _by_alias(drifts):
    return {d.alias: d for d in drifts}


def test_ciinternal_detected(cfg):
    """Identity Class B: ciinternal is absent from base.yml — emitted with expected=''."""
    drifts = _by_alias(role_unpinned.run(cfg, Allowlist(())))
    assert "ciinternal" in drifts
    d = drifts["ciinternal"]
    assert d.expected == ""
    assert d.image == "ciinternal"
    assert "roles/ciinternal/defaults/main.yml" in d.found_src


def test_widget_detected_with_release_key(cfg):
    """Non-identity Class B: widget→gadget alias; image must be gadget (release_key)."""
    drifts = _by_alias(role_unpinned.run(cfg, Allowlist(())))
    assert "widget" in drifts
    d = drifts["widget"]
    assert d.image == "gadget"
    assert d.alias == "widget"
    assert d.expected == ""


def test_adminer_not_emitted(cfg):
    """adminer resolves to a key present in base.yml — must not appear in role_unpinned."""
    drifts = _by_alias(role_unpinned.run(cfg, Allowlist(())))
    assert "adminer" not in drifts


def test_stream_resolved_not_emitted(cfg):
    """ceph_ansible resolves to ceph_version at deploy; absent from base.yml but not drift."""
    drifts = _by_alias(role_unpinned.run(cfg, Allowlist(())))
    assert "ceph_ansible" not in drifts


def test_allowlist_marks_ciinternal_allowlisted(cfg):
    al = Allowlist(
        (
            AllowEntry(
                plugin="role_unpinned",
                image="ciinternal",
                alias=None,
                found_src=None,
                reason="intentional",
            ),
        )
    )
    drifts = _by_alias(role_unpinned.run(cfg, al))
    assert "ciinternal" in drifts
    assert drifts["ciinternal"].allowlisted
