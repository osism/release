import yaml

from osism_drift import source
from osism_drift.config import Allowlist, Config, PluginCfg, Remote
from osism_drift.drift import ubuntu24_cis_level2 as plugin

TASKS = {
    "tasks/section_1/cis_1.1.1.x.yml": """
- name: "1.1.1.6 | PATCH | Ensure overlayfs kernel module is not available"
  tags: [level2-server, level2-workstation]
- name: "1.1.1.10 | PATCH | Ensure unused filesystems kernel modules are not available"
  tags: [level1-server, level1-workstation]
""",
    "tasks/section_5/cis_5.1.x.yml": """
- name: "5.1.8 | PATCH | Ensure sshd DisableForwarding is enabled"
  tags: [level2-server, level1-workstation]
- name: "5.2.x block"
  block:
    - name: "5.2.4 | PATCH | Ensure users must provide password for privilege escalation"
      tags: [level2-server]
""",
    "tasks/section_5/main.yml": "- name: include\n",
}

DEFAULTS = """---
ubtu24cis_rule_3_3_7: false
# BEGIN level2
ubtu24cis_rule_1_1_1_6: false
ubtu24cis_rule_9_9_9: false
# END level2
"""

BASE = "ansible_roles:\n  ubuntu24_cis: '1.7.0'\n"


def _cfg():
    return Config(
        remote=Remote("https://x/", "https://y/", "main", "osism"),
        base_dirs=(),
        release_version="latest",
        plugins={plugin.NAME: PluginCfg(enabled=True)},
        sources={},
    )


def _docs():
    return [yaml.safe_load(v) for k, v in TASKS.items() if "/cis_" in k]


def test_level2_toggles_takes_level2_server_only_rules_including_nested():
    assert plugin.level2_toggles(_docs()) == {
        "ubtu24cis_rule_1_1_1_6",
        "ubtu24cis_rule_5_1_8",
        "ubtu24cis_rule_5_2_4",
    }


def test_block_toggles_reads_only_between_markers():
    assert plugin.block_toggles(DEFAULTS) == {
        "ubtu24cis_rule_1_1_1_6",
        "ubtu24cis_rule_9_9_9",
    }


def _patch(monkeypatch, base=BASE, defaults=DEFAULTS):
    def read(repo, rel_path, config):
        assert (repo, rel_path) == ("release", "latest/base.yml")
        return base.encode()

    def read_optional(repo, rel_path, config):
        assert (repo, rel_path) == ("defaults", plugin.DEFAULTS_FILE)
        return None if defaults is None else defaults.encode()

    def list_dir_at_ref(repo, rel_path, ref, config, dirs_only=False, missing_ok=False):
        assert (repo, ref) == (plugin.ROLE_REPO, "1.7.0")
        if rel_path == "tasks":
            return ["section_1", "section_5", "main.yml"]
        prefix = rel_path + "/"
        return sorted({k[len(prefix) :] for k in TASKS if k.startswith(prefix)})

    def read_at_ref(repo, rel_path, ref, config, optional=False):
        assert (repo, ref) == (plugin.ROLE_REPO, "1.7.0")
        return TASKS[rel_path].encode()

    monkeypatch.setattr(source, "read", read)
    monkeypatch.setattr(source, "read_optional", read_optional)
    monkeypatch.setattr(source, "list_dir_at_ref", list_dir_at_ref)
    monkeypatch.setattr(source, "read_at_ref", read_at_ref)


def test_run_reports_missing_and_stale(monkeypatch):
    _patch(monkeypatch)
    drifts = {d.alias: d for d in plugin.run(_cfg(), Allowlist(()))}
    assert set(drifts) == {
        "ubtu24cis_rule_5_1_8",
        "ubtu24cis_rule_5_2_4",
        "ubtu24cis_rule_9_9_9",
    }
    assert drifts["ubtu24cis_rule_5_2_4"].expected == "false"
    assert drifts["ubtu24cis_rule_5_2_4"].found == ""
    assert drifts["ubtu24cis_rule_9_9_9"].expected == ""
    assert drifts["ubtu24cis_rule_9_9_9"].found == "false"
    assert "1.7.0" in drifts["ubtu24cis_rule_5_2_4"].expected_src


def test_run_without_pin_reports_nothing(monkeypatch):
    _patch(monkeypatch, base="ansible_roles: {}\n")
    assert plugin.run(_cfg(), Allowlist(())) == []


def test_run_without_defaults_file_reports_every_rule_missing(monkeypatch):
    _patch(monkeypatch, defaults=None)
    assert len(plugin.run(_cfg(), Allowlist(()))) == 3
