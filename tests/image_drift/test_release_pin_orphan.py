import pytest
from osism_drift.config import Allowlist, AllowEntry, Config, PluginCfg, Remote
from osism_drift.drift import release_pin_orphan

_BASE_YML = """\
---
manager_version: latest
docker_images:
  osism: '0.20261011.0'
  netbox: 'v4.3.5'
  mariadb: '11.8.8'
  adminer: '5.5.1'
  memcached: '1.6.45-alpine'
  nexus: '3.93.1'
"""

# generics: a whole-line pin, an aliased pin (ara_server_mariadb -> mariadb)
# and a key read only inside a condition.
_MANAGER = """\
---
{% if versions['osism'] == 'latest' -%}
osism_tag: "{{ '{{' }} osism_version|default('latest') {{ '}}' }}"
{% endif -%}
netbox_tag: "{{ versions['netbox'] }}"
ara_server_mariadb_tag: "{{ versions["mariadb"] }}"
"""

_VERSIONS = """\
---
{% if 'adminer' in versions -%}
adminer_tag: "{{ versions['adminer'] }}"
{% endif -%}
"""


@pytest.fixture
def cfg(tmp_path):
    files = {
        "release/latest/base.yml": _BASE_YML,
        "generics/environments/manager/images.yml": _MANAGER,
        "container-image-osism-ansible/files/src/templates/versions.yml.j2": _VERSIONS,
    }
    for rel, body in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
    return Config(
        remote=Remote("https://x/", "https://y/", "main", "osism"),
        base_dirs=(str(tmp_path),),
        release_version="latest",
        plugins={"release_pin_orphan": PluginCfg(enabled=True)},
        sources={},
    )


def _by_image(drifts):
    return {d.image: d for d in drifts}


def test_unread_pins_flagged(cfg):
    """Only pins that neither template reads are reported."""
    drifts = _by_image(release_pin_orphan.run(cfg, Allowlist(())))
    assert set(drifts) == {"memcached", "nexus"}


def test_aliased_key_counts_as_read(cfg):
    """A release key read under another alias (ara_server_mariadb) is not an orphan."""
    assert "mariadb" not in _by_image(release_pin_orphan.run(cfg, Allowlist(())))


def test_condition_counts_as_read(cfg):
    """A key read only in a Jinja condition is still read; deleting it would break the render."""
    assert "osism" not in _by_image(release_pin_orphan.run(cfg, Allowlist(())))


def test_osism_ansible_transport_counts(cfg):
    """A key carried only by versions.yml.j2 is not an orphan."""
    assert "adminer" not in _by_image(release_pin_orphan.run(cfg, Allowlist(())))


def test_top_level_key_ignored(cfg):
    """Only docker_images is scanned; the top-level manager_version is out of scope."""
    assert "manager_version" not in _by_image(
        release_pin_orphan.run(cfg, Allowlist(()))
    )


def test_entry_fields(cfg):
    """The finding names the key, shows the pin, and is actionable."""
    d = _by_image(release_pin_orphan.run(cfg, Allowlist(())))["memcached"]
    assert d.alias == "memcached"
    assert "1.6.45-alpine" in d.found
    assert d.found_src == "release/latest/base.yml"
    assert d.severity == "actionable"
    assert not d.allowlisted


def test_allowlist_marks_entry(cfg):
    """A pin read outside the transports is marked allowlisted, not dropped."""
    al = Allowlist(
        (
            AllowEntry(
                plugin="release_pin_orphan",
                image="nexus",
                alias=None,
                found_src=None,
                reason="role-managed",
            ),
        )
    )
    drifts = _by_image(release_pin_orphan.run(cfg, al))
    assert drifts["nexus"].allowlisted
    assert not drifts["memcached"].allowlisted
