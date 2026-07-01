"""End-to-end tests for check-drift.py image group via subprocess."""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "src" / "check-drift.py"
FIXT = Path(__file__).parent / "fixtures"

_CFG_BODY = """\
remote:
  github_raw: https://raw.githubusercontent.com/
  github_api: https://api.github.com/repos/
  branch: main
  default_owner: osism
release_version: latest
plugins:
  release_vs_manager:
    enabled: true
  role_shadows:
    enabled: true
"""

_AL_BODY = """\
allow:
  - plugin: release_vs_manager
    image: osism_ansible
    reason: "rolling-release image; rendered as 'latest' by design"
"""


def _run(*args, cfg, al):
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--config",
            str(cfg),
            "--allowlist",
            str(al),
            "--group",
            "image",
            *args,
        ],
        capture_output=True,
        text=True,
    )


def _setup(tmp_path):
    cfg = tmp_path / "config.yml"
    al = tmp_path / "allowlist.yml"
    cfg.write_text(_CFG_BODY)
    al.write_text(_AL_BODY)
    return cfg, al


def test_help_lists_plugins(tmp_path):
    cfg, al = _setup(tmp_path)
    r = _run("--help", cfg=cfg, al=al)
    assert r.returncode == 0
    assert "release_vs_manager" in r.stdout
    assert "role_shadows" in r.stdout
    assert "Plugins by group:" in r.stdout


def test_default_run_exits_1_with_drifting_images(tmp_path):
    cfg, al = _setup(tmp_path)
    r = _run("--base-dir", str(FIXT), "-q", cfg=cfg, al=al)
    assert r.returncode == 1
    assert "ara_server" in r.stdout
    assert "adminer" in r.stdout


def test_allowlisted_osism_ansible_hidden_by_default(tmp_path):
    cfg, al = _setup(tmp_path)
    r = _run("--base-dir", str(FIXT), cfg=cfg, al=al)
    assert r.returncode == 1
    # osism_ansible is allowlisted → does not appear in the grouped body
    body_lines = [ln for ln in r.stdout.splitlines() if "osism_ansible" in ln]
    assert (
        not body_lines
    ), f"osism_ansible should not appear in actionable output: {r.stdout}"


def test_no_allowlist_surfaces_osism_ansible(tmp_path):
    cfg, al = _setup(tmp_path)
    r = _run("--base-dir", str(FIXT), "--no-allowlist", "-q", cfg=cfg, al=al)
    assert r.returncode == 1
    assert "osism_ansible" in r.stdout


def test_format_json_keys(tmp_path):
    cfg, al = _setup(tmp_path)
    r = _run("--base-dir", str(FIXT), "--format", "json", "-q", cfg=cfg, al=al)
    assert r.returncode == 1
    lines = [ln for ln in r.stdout.splitlines() if ln.strip()]
    assert lines, "expected at least one JSON line"
    obj = json.loads(lines[0])
    for key in (
        "plugin",
        "image",
        "alias",
        "expected",
        "found",
        "expected_src",
        "found_src",
    ):
        assert key in obj, f"missing key {key!r}"


def test_plugin_filter_runs_only_role_shadows(tmp_path):
    cfg, al = _setup(tmp_path)
    r = _run("--base-dir", str(FIXT), "--plugin", "role_shadows", "-q", cfg=cfg, al=al)
    assert r.returncode == 1
    assert "release_vs_manager" not in r.stdout
    assert "role_shadows" in r.stdout or "adminer" in r.stdout


def test_summary_line_counts(tmp_path):
    cfg, al = _setup(tmp_path)
    r = _run("--base-dir", str(FIXT), cfg=cfg, al=al)
    assert r.returncode == 1
    summary = next(
        (ln for ln in r.stdout.splitlines() if ln.startswith("Summary:")), None
    )
    assert summary is not None, f"no Summary: line in output:\n{r.stdout}"
    # 9 total findings, 1 allowlisted → 8 to act on, 0 stale
    assert "8 to act on" in summary, summary
    assert "1 allowlisted" in summary, summary


def test_missing_group_errors(tmp_path):
    cfg, al = _setup(tmp_path)
    # No --group: argparse rejects it as a required argument (exit 2).
    r = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--config",
            str(cfg),
            "--allowlist",
            str(al),
        ],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 2
    assert "required" in r.stderr and "--group" in r.stderr
