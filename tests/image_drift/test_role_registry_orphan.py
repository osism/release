from pathlib import Path

import pytest
from osism_drift.config import Allowlist, AllowEntry, Config, PluginCfg, Remote
from osism_drift.drift import role_registry_orphan

FIXT = Path(__file__).parent / "fixtures"


@pytest.fixture
def cfg():
    return Config(
        remote=Remote("https://x/", "https://y/", "main", "osism"),
        base_dirs=(str(FIXT),),
        release_version="latest",
        plugins={"role_registry_orphan": PluginCfg(enabled=True)},
        sources={},
    )


def _by_image(drifts):
    return {d.image: d for d in drifts}


def test_unreferenced_registry_var_detected(cfg):
    """docker_registry_widget is defined in the widget role and referenced
    nowhere — it must be flagged, pointing at the file that defines it."""
    drifts = _by_image(role_registry_orphan.run(cfg, Allowlist(())))
    assert "docker_registry_widget" in drifts
    d = drifts["docker_registry_widget"]
    assert d.alias == "widget"
    assert d.found_src == "ansible-collection-services/roles/widget/defaults/main.yml"


def test_own_definition_line_is_not_a_consumer(cfg):
    """The widget fixture defines docker_registry_widget *as* a reference to
    docker_registry_ansible. One line must prove both halves: the key being
    defined does not count as consuming itself, while the value's reference
    does count for the var it names."""
    drifts = _by_image(role_registry_orphan.run(cfg, Allowlist(())))
    assert "docker_registry_widget" in drifts
    assert "docker_registry_ansible" not in drifts


def test_reference_in_same_role_not_detected(cfg):
    """docker_registry_adminer is consumed by another key in the same
    defaults file — not an orphan."""
    drifts = _by_image(role_registry_orphan.run(cfg, Allowlist(())))
    assert "docker_registry_adminer" not in drifts


def test_reference_from_another_role_not_detected(cfg):
    """docker_registry_osism_frontend is defined in the manager role and
    consumed in the netbox role — the scan spans roles, not just the
    defining one."""
    drifts = _by_image(role_registry_orphan.run(cfg, Allowlist(())))
    assert "docker_registry_osism_frontend" not in drifts


def test_bare_jinja_statement_reference_counts_as_consumer(cfg):
    """docker_registry_loopy is consumed as a bare loop source
    ({% for m in docker_registry_loopy %}), never as {{ ... }}. A consumer
    pattern that only matched {{ var }} would false-flag it — as it would
    the real docker_registry_mirrors in roles/docker."""
    drifts = _by_image(role_registry_orphan.run(cfg, Allowlist(())))
    assert "docker_registry_loopy" not in drifts


def test_longer_name_does_not_mask_its_prefix(cfg):
    """docker_registry_osism is unreferenced; docker_registry_osism_frontend
    is referenced. The substring must not be read as a reference to the
    shorter var, or the orphan disappears."""
    drifts = _by_image(role_registry_orphan.run(cfg, Allowlist(())))
    assert "docker_registry_osism" in drifts


def test_playbook_reference_not_detected(cfg):
    """docker_registry_pb is consumed only in ansible-playbooks-manager."""
    drifts = _by_image(role_registry_orphan.run(cfg, Allowlist(())))
    assert "docker_registry_pb" not in drifts


def test_manager_template_reference_not_detected(cfg):
    """docker_registry_gen is consumed only by the generics manager render
    template, which renders it into every deployed images.yml."""
    drifts = _by_image(role_registry_orphan.run(cfg, Allowlist(())))
    assert "docker_registry_gen" not in drifts


def test_base_docker_registry_var_is_not_reported(cfg):
    """Bare docker_registry is the base override, not a per-image one; it is
    outside the docker_registry_<alias> family this plugin scans."""
    drifts = _by_image(role_registry_orphan.run(cfg, Allowlist(())))
    assert "docker_registry" not in drifts


def test_consumer_in_an_unfiltered_file_is_found(cfg):
    """docker_registry_shellcons is referenced only in
    roles/shellcons/files/entrypoint.sh — not a .yml/.yaml/.j2 file. The scan
    must read it, or a live registry override is reported for deletion."""
    drifts = _by_image(role_registry_orphan.run(cfg, Allowlist(())))
    assert "docker_registry_shellcons" not in drifts


def test_allowlist_suppresses_orphan(cfg):
    al = Allowlist(
        (
            AllowEntry(
                plugin="role_registry_orphan",
                image="docker_registry_widget",
                alias=None,
                found_src=None,
                reason="intentional",
            ),
        )
    )
    drifts = _by_image(role_registry_orphan.run(cfg, al))
    assert "docker_registry_widget" in drifts
    assert drifts["docker_registry_widget"].allowlisted
