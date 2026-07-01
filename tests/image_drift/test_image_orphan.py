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


def test_non_text_files_are_not_read(cfg, monkeypatch):
    """Only .yml/.yaml/.j2 files can hold a {{ x_image }} ref; every other file
    in the listed trees must be skipped (one remote GET each in remote mode)."""
    read_paths = []
    real = image_orphan.source.read_optional

    def spy(repo, path, config):
        read_paths.append(path)
        return real(repo, path, config)

    monkeypatch.setattr(image_orphan.source, "read_optional", spy)
    image_orphan.run(cfg, Allowlist(()))
    assert read_paths, "expected some consumer files to be read"
    assert all(p.endswith((".yml", ".yaml", ".j2")) for p in read_paths)


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
