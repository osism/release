from pathlib import Path
from types import SimpleNamespace

import pytest

from osism_drift import driver, release_pins, source
from osism_drift.model import DriftEntry
from osism_drift.source import SourceError

SHA = "0123456789abcdef0123456789abcdef01234567"
GATE_REFS = {
    "ansible_playbooks": "refs/tags/v0.20261125.0",
    "python_osism": "refs/tags/v0.20261124.0",
}
ALLOWLIST_PATH = "src/drift-allowlist.yml"


def _entry(name, *, blocks_release, image="role_x"):
    return DriftEntry(
        plugin=name,
        image=image,
        alias=None,
        expected="present",
        found="missing",
        expected_src="python-osism/catalog",
        found_src="ansible-playbooks/roles",
        blocks_release=blocks_release,
    )


def _plugin(name, entries, *, gate=True, seen=None):
    def run(config, allowlist, verbose=False):
        del verbose
        if seen is not None:
            seen.append(dict(config.ref_overrides))
        return [allowlist.apply(e) for e in entries]

    return SimpleNamespace(
        NAME=name,
        DESCRIPTION=f"{name} description",
        INPUT_FILES=[],
        SUMMARY="{n} drift",
        REMEDIATION=f"fix {name}",
        RELEASE_GATE=gate,
        run=run,
    )


def _write_config(tmp_path, names):
    cfg = tmp_path / "drift-config.yml"
    plugins = "".join(f"  {n}: {{enabled: true}}\n" for n in names)
    cfg.write_text(
        "remote:\n"
        "  github_raw: https://raw.githubusercontent.com/\n"
        "  github_api: https://api.github.com/repos/\n"
        "  default_owner: osism\n"
        "  branch: main\n"
        "release_version: latest\n"
        "plugins:\n" + plugins,
        encoding="utf-8",
    )
    return cfg


def _run(tmp_path, plugins, *args):
    cfg = _write_config(tmp_path, [p.NAME for p in plugins])
    return driver.run(
        ["--config", str(cfg), "--group", "catalog", "-q", *args],
        description="test driver",
        default_config=Path("missing-config.yml"),
        default_allowlist=tmp_path / "default-allowlist.yml",
        plugin_groups={"catalog": plugins},
        report_headers={"catalog": "catalog header"},
    )


def _patch_gate(monkeypatch, allowlist_text="allow: []\n", reads=None):
    monkeypatch.setattr(release_pins, "gate_refs", lambda config: dict(GATE_REFS))

    def fake_read(repo, rel_path, config):
        if reads is not None:
            reads.append((repo, rel_path, dict(config.ref_overrides)))
        assert (repo, rel_path) == ("release", ALLOWLIST_PATH)
        return allowlist_text.encode()

    monkeypatch.setattr(source, "read", fake_read)


def test_gate_blocks_on_blocking_finding(tmp_path, monkeypatch, capsys):
    _patch_gate(monkeypatch)
    plugin = _plugin("gate_plugin", [_entry("gate_plugin", blocks_release=True)])
    assert _run(tmp_path, [plugin], "--release-gate", SHA) == 1
    assert "role_x" in capsys.readouterr().out


def test_gate_ignores_non_blocking_finding(tmp_path, monkeypatch, capsys):
    _patch_gate(monkeypatch)
    plugin = _plugin("gate_plugin", [_entry("gate_plugin", blocks_release=False)])
    assert _run(tmp_path, [plugin], "--release-gate", SHA) == 0
    # The report still prints the finding.
    assert "role_x" in capsys.readouterr().out


def test_gate_honours_allowlist_from_release_commit(tmp_path, monkeypatch):
    reads = []
    _patch_gate(
        monkeypatch,
        allowlist_text=(
            "allow:\n"
            "  - plugin: gate_plugin\n"
            "    image: role_x\n"
            "    reason: intended\n"
        ),
        reads=reads,
    )
    plugin = _plugin("gate_plugin", [_entry("gate_plugin", blocks_release=True)])
    assert _run(tmp_path, [plugin], "--release-gate", SHA) == 0
    assert len(reads) == 1
    assert reads[0][2]["release"] == SHA


def test_gate_ignores_stale_allowlist_entries(tmp_path, monkeypatch, capsys):
    _patch_gate(
        monkeypatch,
        allowlist_text=(
            "allow:\n"
            "  - plugin: gate_plugin\n"
            "    image: role_gone\n"
            "    reason: fixed long ago\n"
        ),
    )
    plugin = _plugin("gate_plugin", [])
    assert _run(tmp_path, [plugin], "--release-gate", SHA) == 0
    assert "STALE ALLOWLIST" not in capsys.readouterr().out


def test_gate_sets_pin_overrides(tmp_path, monkeypatch):
    _patch_gate(monkeypatch)
    seen = []
    plugin = _plugin("gate_plugin", [], seen=seen)
    assert _run(tmp_path, [plugin], "--release-gate", SHA) == 0
    assert seen == [{"release": SHA, **GATE_REFS}]


def test_gate_refuses_non_gate_plugin(tmp_path, monkeypatch, capsys):
    _patch_gate(monkeypatch)
    seen = []
    gate = _plugin("gate_plugin", [], seen=seen)
    other = _plugin("other_plugin", [], gate=False, seen=seen)
    assert _run(tmp_path, [gate, other], "--release-gate", SHA) == 2
    err = capsys.readouterr().err
    assert "other_plugin" in err
    assert "gate_plugin" not in err
    assert seen == []


def test_gate_with_no_selected_plugin_exits_2(tmp_path, monkeypatch, capsys):
    reads = []
    _patch_gate(monkeypatch, reads=reads)
    plugin = _plugin("gate_plugin", [])
    cfg = _write_config(tmp_path, [plugin.NAME])
    cfg.write_text(
        cfg.read_text().replace("enabled: true", "enabled: false"), encoding="utf-8"
    )
    code = driver.run(
        ["--config", str(cfg), "--group", "catalog", "-q", "--release-gate", SHA],
        description="test driver",
        default_config=Path("missing-config.yml"),
        default_allowlist=tmp_path / "default-allowlist.yml",
        plugin_groups={"catalog": [plugin]},
        report_headers={"catalog": "catalog header"},
    )
    assert code == 2
    assert "selected no plugin" in capsys.readouterr().err
    assert reads == []


def test_gate_refuses_plugin_without_gate_attribute(tmp_path, monkeypatch, capsys):
    _patch_gate(monkeypatch)
    plugin = _plugin("plain_plugin", [])
    del plugin.RELEASE_GATE
    assert _run(tmp_path, [plugin], "--release-gate", SHA) == 2
    assert "plain_plugin" in capsys.readouterr().err


def test_gate_source_error_exits_2(tmp_path, monkeypatch, capsys):
    _patch_gate(monkeypatch)

    def boom(config):
        raise SourceError("defaults_version missing from release latest/base.yml")

    monkeypatch.setattr(release_pins, "gate_refs", boom)
    seen = []
    plugin = _plugin("gate_plugin", [], seen=seen)
    assert _run(tmp_path, [plugin], "--release-gate", SHA) == 2
    assert "defaults_version missing" in capsys.readouterr().err
    assert seen == []


def test_gate_rejects_explicit_allowlist(tmp_path, monkeypatch, capsys):
    _patch_gate(monkeypatch)
    allowlist = tmp_path / "a.yml"
    allowlist.write_text("allow: []\n", encoding="utf-8")
    plugin = _plugin("gate_plugin", [])
    rc = _run(tmp_path, [plugin], "--release-gate", SHA, "--allowlist", str(allowlist))
    assert rc == 2
    assert "--allowlist" in capsys.readouterr().err


def test_gate_no_allowlist_reads_nothing(tmp_path, monkeypatch):
    reads = []
    _patch_gate(monkeypatch, reads=reads)
    plugin = _plugin("gate_plugin", [_entry("gate_plugin", blocks_release=True)])
    assert _run(tmp_path, [plugin], "--release-gate", SHA, "--no-allowlist") == 1
    assert reads == []


def test_without_gate_stale_still_fails(tmp_path, capsys):
    allowlist = tmp_path / "default-allowlist.yml"
    allowlist.write_text(
        "allow:\n"
        "  - plugin: gate_plugin\n"
        "    image: role_gone\n"
        "    reason: fixed long ago\n",
        encoding="utf-8",
    )
    plugin = _plugin("gate_plugin", [])
    # No --allowlist: the default path is resolved after parsing.
    assert _run(tmp_path, [plugin]) == 1
    assert "STALE ALLOWLIST" in capsys.readouterr().out


def test_help_shows_default_allowlist(tmp_path, monkeypatch, capsys):
    # Run from tmp_path so the default shows as a short relative path.
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as exc:
        _run(tmp_path, [_plugin("gate_plugin", [])], "--help")
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "(default: default-allowlist.yml)" in out
    assert "--release-gate" in out


@pytest.mark.parametrize("value", ["", "  "])
def test_gate_rejects_empty_sha(tmp_path, monkeypatch, capsys, value):
    reads = []
    _patch_gate(monkeypatch, reads=reads)
    seen = []
    plugin = _plugin("gate_plugin", [], seen=seen)
    assert _run(tmp_path, [plugin], "--release-gate", value) == 2
    assert "--release-gate" in capsys.readouterr().err
    assert seen == []
    assert reads == []
