"""Isolated tmp_path trees on purpose: the shared fixtures/defaults/all
directory is read whole by several other plugins, so a file with a version gate
added there would change their results too.
"""

import pytest
from osism_drift.config import Allowlist, AllowEntry, Config, PluginCfg, Remote
from osism_drift.drift import kolla_version_gate_orphan as plugin

SUPPORTED = ("2024.1", "2025.1")


def _tree(tmp_path, files, releases=SUPPORTED):
    """Write `files` into <tmp>/defaults/all and return a Config reading it."""
    d = tmp_path / "defaults" / "all"
    d.mkdir(parents=True)
    for name, text in files.items():
        (d / name).write_text(text)
    return Config(
        remote=Remote("https://raw/", "https://api/", "main", "osism"),
        base_dirs=(str(tmp_path),),
        release_version="latest",
        plugins={plugin.NAME: PluginCfg(enabled=True)},
        releases=releases,
    )


def _run(tmp_path, files, allowlist=None, releases=SUPPORTED):
    cfg = _tree(tmp_path, files, releases)
    return plugin.run(cfg, allowlist or Allowlist(()))


# --- version gates -----------------------------------------------------------


def test_flags_a_gate_naming_a_release_outside_the_supported_range(tmp_path):
    drifts = _run(
        tmp_path,
        {
            "002-images.yml": "mariadb_image: \"{{ 'a' if openstack_version in "
            "['victoria'] else 'b' }}\"\n"
        },
    )
    assert len(drifts) == 1
    d = drifts[0]
    assert (d.plugin, d.image, d.found) == (plugin.NAME, "mariadb_image", "victoria")
    assert d.found_src == "osism/defaults all/002-images.yml"
    assert d.expected == "2024.1, 2025.1"


def test_silent_when_every_named_release_is_supported(tmp_path):
    drifts = _run(
        tmp_path,
        {
            "099.yml": "enable_redis: \"{{ 'yes' if openstack_version in "
            "['2024.1', '2025.1'] else 'no' }}\"\n"
        },
    )
    assert drifts == []


def test_reports_only_the_dead_releases_of_a_mixed_gate(tmp_path):
    """A gate can name both a supported and a retired release."""
    drifts = _run(
        tmp_path,
        {
            "099.yml": "enable_x: \"{{ 'y' if openstack_version in "
            "['2024.1', '2023.2'] else 'n' }}\"\n"
        },
    )
    assert [d.found for d in drifts] == ["2023.2"]


def test_flags_a_not_in_gate_too(tmp_path):
    drifts = _run(
        tmp_path,
        {
            "099.yml": "p: \"{{ 'a' if openstack_version not in ['2023.2'] else 'b' }}\"\n"
        },
    )
    assert [d.image for d in drifts] == ["p"]


def test_ignores_files_that_are_not_yaml(tmp_path):
    drifts = _run(
        tmp_path,
        {"README.md": "openstack_version in ['victoria']\n"},
    )
    assert drifts == []


# --- per-release compat files ------------------------------------------------


def test_flags_a_per_release_file_for_a_retired_release(tmp_path):
    """The file's own header says to delete it once the release leaves the range."""
    drifts = _run(tmp_path, {"010-2023.2.yml": "swift_rsync_port: '10873'\n"})
    assert len(drifts) == 1
    d = drifts[0]
    assert (d.image, d.found) == ("010-2023.2.yml", "2023.2")
    assert d.found_src == "osism/defaults all/"
    assert d.summary == plugin.FILE_SUMMARY


def test_silent_for_a_per_release_file_of_a_supported_release(tmp_path):
    assert _run(tmp_path, {"010-2024.1.yml": "k: v\n"}) == []


def test_a_retired_per_release_file_can_also_carry_a_stale_gate(tmp_path):
    """Both kinds are reported; they call for different actions."""
    drifts = _run(
        tmp_path,
        {
            "010-2023.2.yml": "e: \"{{ 'a' if openstack_version in ['2023.2'] "
            "else 'b' }}\"\n"
        },
    )
    assert sorted(d.image for d in drifts) == ["010-2023.2.yml", "e"]


# --- severity and allowlist --------------------------------------------------


def test_findings_are_advisory_so_the_nightly_job_stays_green(tmp_path):
    """A gate on an unsupported release may still serve upgrades from it."""
    drifts = _run(
        tmp_path,
        {"099.yml": "g: \"{{ 'a' if openstack_version in ['victoria'] else 'b' }}\"\n"},
    )
    assert [d.severity for d in drifts] == ["advisory"]


def test_a_deliberate_gate_can_be_allowlisted(tmp_path):
    allowlist = Allowlist(
        (AllowEntry(plugin=plugin.NAME, image="g", reason="needed for upgrades"),)
    )
    drifts = _run(
        tmp_path,
        {"099.yml": "g: \"{{ 'a' if openstack_version in ['victoria'] else 'b' }}\"\n"},
        allowlist=allowlist,
    )
    assert [d.allowlisted for d in drifts] == [True]


# --- pure helper -------------------------------------------------------------


def test_stale_releases_keeps_order_and_drops_supported():
    assert plugin.stale_releases(
        ["2025.1", "victoria", "2024.1", "2023.2"], {"2024.1", "2025.1"}
    ) == ["victoria", "2023.2"]


# --- report ------------------------------------------------------------------


def test_report_names_the_variable_the_release_and_the_file(tmp_path):
    from osism_drift import report

    drifts = _run(
        tmp_path,
        {
            "002-images.yml": "mariadb_image: \"{{ 'a' if openstack_version in "
            "['victoria'] else 'b' }}\"\n"
        },
    )
    text = "\n".join(report.format_text(drifts, [plugin]))
    assert "mariadb_image" in text
    assert "002-images.yml" in text
    assert "outside the supported range" in " ".join(text.split())


def test_gate_and_file_findings_render_as_separate_blocks(tmp_path):
    """Editing an expression and deleting a file are different actions."""
    from osism_drift import report

    drifts = _run(
        tmp_path,
        {
            "010-2023.2.yml": "k: v\n",
            "099.yml": "g: \"{{ 'a' if openstack_version in ['victoria'] "
            "else 'b' }}\"\n",
        },
    )
    text = "\n".join(report.format_text(drifts, [plugin]))
    assert text.count(plugin.NAME) == 2
    assert "delete the file" in " ".join(text.split())


@pytest.fixture
def registered():
    from osism_drift.drift import KOLLA_PLUGINS

    return [p.NAME for p in KOLLA_PLUGINS]


def test_plugin_is_registered(registered):
    assert plugin.NAME in registered
