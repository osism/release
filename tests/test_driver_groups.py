import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from osism_drift import driver, http
from osism_drift.http import SourceError
from osism_drift.drift import (
    PLUGIN_GROUPS as REAL_GROUPS,
    REPORT_HEADERS as REAL_HEADERS,
)
from osism_drift.model import DriftEntry


def _plugin(name):
    def run(config, allowlist, verbose=False):
        del config, verbose
        entry = DriftEntry(
            plugin=name,
            image=f"{name}_image",
            alias=f"{name}_alias",
            expected="1",
            found="2",
            expected_src="release/latest/base.yml",
            found_src=f"{name}/source.yml",
        )
        return [allowlist.apply(entry)]

    return SimpleNamespace(
        NAME=name,
        DESCRIPTION=f"{name} description",
        INPUT_FILES=[("repo", f"{name}.yml")],
        SUMMARY="{n} drift",
        REMEDIATION=f"fix {name}",
        run=run,
    )


IMAGE = _plugin("fake_image")
KOLLA = _plugin("fake_kolla")
PLUGIN_GROUPS = {"image": [IMAGE], "kolla": [KOLLA]}
REPORT_HEADERS = {"image": "image header", "kolla": "kolla header"}


def _write_runtime(tmp_path):
    cfg = tmp_path / "drift-config.yml"
    cfg.write_text(
        """
remote:
  github_raw: https://raw.githubusercontent.com/
  github_api: https://api.github.com/repos/
  default_owner: osism
  branch: main
release_version: latest
plugins:
  fake_image: {enabled: true}
  fake_kolla: {enabled: true}
""",
        encoding="utf-8",
    )
    allowlist = tmp_path / "drift-allowlist.yml"
    allowlist.write_text("allow: []\n", encoding="utf-8")
    return cfg, allowlist


def _run(tmp_path, *args):
    cfg, allowlist = _write_runtime(tmp_path)
    return driver.run(
        ["--config", str(cfg), "--allowlist", str(allowlist), *args],
        description="test driver",
        default_config=Path("missing-config.yml"),
        default_allowlist=Path("missing-allowlist.yml"),
        plugin_groups=PLUGIN_GROUPS,
        report_headers=REPORT_HEADERS,
    )


def test_group_image_runs_only_image_plugins(tmp_path, capsys):
    assert _run(tmp_path, "--group", "image", "-q") == 1
    out = capsys.readouterr().out
    assert "image header" in out
    assert "fake_image" in out
    assert "fake_kolla" not in out


def test_group_kolla_runs_only_kolla_plugins(tmp_path, capsys):
    assert _run(tmp_path, "--group", "kolla", "-q") == 1
    out = capsys.readouterr().out
    assert "kolla header" in out
    assert "fake_kolla" in out
    assert "fake_image" not in out


def test_group_all_runs_both_groups(tmp_path, capsys):
    assert _run(tmp_path, "--group", "all", "-q") == 1
    out = capsys.readouterr().out
    assert out.index("image header") < out.index("kolla header")
    assert "fake_image" in out
    assert "fake_kolla" in out


def test_archive_mode_preamble_not_misleading(tmp_path, capsys):
    # Default (archive) remote run: the heads-up must not claim slow per-file API
    # reads -- archive mode makes a handful of tarball fetches, not dozens.
    assert _run(tmp_path, "--group", "all") == 1
    err = capsys.readouterr().err
    assert "several minutes" not in err
    assert "archive" in err.lower()


def test_use_raw_get_preamble_warns_slow(tmp_path, capsys):
    # --use-raw-get restores the per-file API path, where the slow warning is apt.
    assert _run(tmp_path, "--group", "all", "--use-raw-get") == 1
    err = capsys.readouterr().err
    assert "several minutes" in err


def test_missing_group_errors(tmp_path):
    # --group is required: argparse aborts with exit 2 (no run).
    with pytest.raises(SystemExit) as exc:
        _run(tmp_path)
    assert exc.value.code == 2


def _run_demo(*args, groups=PLUGIN_GROUPS, headers=REPORT_HEADERS):
    # Deliberately point defaults at missing files: --demo must not read them.
    return driver.run(
        [*args],
        description="test driver",
        default_config=Path("missing-config.yml"),
        default_allowlist=Path("missing-allowlist.yml"),
        plugin_groups=groups,
        report_headers=headers,
    )


def test_demo_exits_0_without_reading_config_or_allowlist(capsys):
    assert _run_demo("--group", "image", "--demo", "-q") == 0
    out = capsys.readouterr().out
    assert "fake_image" in out


def test_demo_renders_every_real_plugin(capsys):
    assert (
        _run_demo(
            "--group", "all", "--demo", "-q", groups=REAL_GROUPS, headers=REAL_HEADERS
        )
        == 0
    )
    out = capsys.readouterr().out
    for group in REAL_GROUPS.values():
        for plugin in group:
            assert plugin.NAME in out, plugin.NAME


def test_demo_json_emits_entries(capsys):
    assert _run_demo("--group", "image", "--demo", "--format", "json", "-q") == 0
    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.strip()]
    assert lines
    obj = json.loads(lines[0])
    assert obj["plugin"] == "fake_image"


def test_help_lists_plugin_groups(tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        _run(tmp_path, "--help")
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "usage:" in out
    assert "--group" in out
    # Concise epilog: one line per group with its plugin names.
    assert "Plugins by group:" in out
    assert "image: fake_image" in out
    assert "kolla: fake_kolla" in out


def _plugin_that_requests(name, raise_after=False):
    def run(config, allowlist, verbose=False):
        del config, allowlist, verbose
        http._count_url("https://api.github.com/repos/osism/release/contents/latest")
        if raise_after:
            raise SourceError("boom")
        return []

    return SimpleNamespace(
        NAME=name,
        DESCRIPTION=f"{name} description",
        INPUT_FILES=[("repo", f"{name}.yml")],
        SUMMARY="{n} drift",
        REMEDIATION=f"fix {name}",
        run=run,
    )


def _run_with(tmp_path, plugin, *args):
    cfg = tmp_path / "drift-config.yml"
    cfg.write_text(
        """
remote:
  github_raw: https://raw.githubusercontent.com/
  github_api: https://api.github.com/repos/
  default_owner: osism
  branch: main
release_version: latest
plugins:
  %s: {enabled: true}
""" % plugin.NAME,
        encoding="utf-8",
    )
    allowlist = tmp_path / "drift-allowlist.yml"
    allowlist.write_text("allow: []\n", encoding="utf-8")
    return driver.run(
        ["--config", str(cfg), "--allowlist", str(allowlist), *args],
        description="test driver",
        default_config=Path("missing-config.yml"),
        default_allowlist=Path("missing-allowlist.yml"),
        plugin_groups={"image": [plugin]},
        report_headers={"image": "image header"},
    )


def test_request_summary_is_printed_even_when_quiet(tmp_path, capsys):
    assert (
        _run_with(tmp_path, _plugin_that_requests("counting"), "--group", "image", "-q")
        == 0
    )
    err = capsys.readouterr().err
    assert "HTTP requests: api.github.com 1" in err


def test_request_summary_is_printed_after_a_source_error(tmp_path, capsys):
    plugin = _plugin_that_requests("failing", raise_after=True)
    assert _run_with(tmp_path, plugin, "--group", "image", "-q") == 2
    err = capsys.readouterr().err
    assert "source error: boom" in err
    assert "HTTP requests: api.github.com 1" in err


def test_request_summary_counts_one_run_only(tmp_path, capsys):
    plugin = _plugin_that_requests("counting")
    _run_with(tmp_path, plugin, "--group", "image", "-q")
    _run_with(tmp_path, plugin, "--group", "image", "-q")
    err = capsys.readouterr().err
    assert err.count("HTTP requests: api.github.com 1") == 2
