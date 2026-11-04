import pytest
from osism_drift import source
from osism_drift.config import Allowlist, AllowEntry
from osism_drift.drift import kolla_build_conf_unknown_section as plugin
from osism_drift.http import SourceError

# The per-project block of container-images-kolla templates/2025.1, trimmed to
# what decides section names.
TEMPLATE = """\
[DEFAULT]
base = {{ base }}
openstack_release = {{ openstack_release }}

{%- for project in projects %}
{% if project.name not in ['horizon', 'neutron-vpnaas-agent', 'neutron-dynamic-routing'] %}
[{{ project.name }}-base]
{% elif project.name == 'neutron-dynamic-routing' %}
[neutron-bgp-dragent]
{% else %}
[{{ project.name }}]
{% endif -%}
type = local
location = tarballs/{{ project.repository }}-{{ project.version }}.tar.gz
{%- endfor %}

[profiles]
default = nova
"""

SOURCES_PY = """\
from kolla.version import version_info as version

SOURCES = {
    'openstack-base': {'type': 'url', 'location': 'x'},
    'nova-base': {'type': 'url', 'location': 'x'},
    'horizon': {'type': 'url', 'location': 'x'},
    'neutron-bgp-dragent': {'type': 'url', 'location': 'x'},
    'skyline-apiserver': {'type': 'url', 'location': 'x'},
}
"""

KEYS = {"openstack-base", "nova-base", "horizon", "neutron-bgp-dragent"}


def _versions(**projects):
    return {"openstack_version": "2025.1", "openstack_projects": projects}


def test_known_sections_are_clean():
    v = _versions(nova="stable-2025.1", horizon="stable-2025.1")
    assert plugin.unknown_sections(TEMPLATE, v, KEYS) == []


def test_flags_section_kolla_has_no_key_for():
    """skyline-apiserver renders as -base, which kolla never reads."""
    v = _versions(nova="stable-2025.1", **{"skyline-apiserver": "stable-2025.1"})
    keys = plugin.kolla_source_keys(SOURCES_PY)
    assert plugin.unknown_sections(TEMPLATE, v, keys) == ["skyline-apiserver-base"]


def test_generator_skips_render_no_section():
    """gnocchi, novajoin, monasca and unversioned projects never reach the
    template, so an unknown-looking name for them is not a finding."""
    v = _versions(gnocchi="stable/4.6", novajoin="x", monasca="x", cinder=None)
    assert plugin.unknown_sections(TEMPLATE, v, KEYS) == []


def test_non_source_sections_are_ignored():
    """[DEFAULT] and [profiles] set no `type`, so they are not source sections."""
    sections = plugin.source_sections(plugin.render(TEMPLATE, _versions(nova="x")))
    assert sections == ["nova-base"]


def test_vpnaas_agent_gets_mapped_repository():
    conf = plugin.render(TEMPLATE, _versions(**{"neutron-vpnaas-agent": "x"}))
    assert "tarballs/neutron-vpnaas-x.tar.gz" in conf


def test_kolla_source_keys_reads_the_dict_literal():
    assert plugin.kolla_source_keys(SOURCES_PY) == KEYS | {"skyline-apiserver"}


def test_kolla_source_keys_without_sources_is_loud():
    with pytest.raises(SourceError):
        plugin.kolla_source_keys("OTHER = {}\n")


@pytest.fixture
def fake_sources(monkeypatch):
    """Two releases; the release file adds skyline-apiserver only at B."""
    files = {
        ("release", "latest/openstack-A.yml"): b"openstack_version: A\n"
        b"openstack_projects:\n  nova: stable-A\n",
        ("release", "latest/openstack-B.yml"): b"openstack_version: B\n"
        b"openstack_projects:\n  nova: stable-B\n  skyline-apiserver: stable-B\n",
        (
            "container_images_kolla",
            "templates/A/kolla-build.conf.j2",
        ): TEMPLATE.encode(),
        (
            "container_images_kolla",
            "templates/B/kolla-build.conf.j2",
        ): TEMPLATE.encode(),
    }
    monkeypatch.setattr(source, "read", lambda repo, path, config: files[(repo, path)])
    monkeypatch.setattr(source, "release_to_ref", lambda repo, r, config: f"stable/{r}")
    monkeypatch.setattr(
        source,
        "read_at_ref",
        lambda repo, path, ref, config, optional=False: SOURCES_PY.encode(),
    )
    return type("Cfg", (), {"releases": ("A", "B"), "release_refs": {}})()


def test_run_reports_per_release(fake_sources):
    drifts = plugin.run(fake_sources, Allowlist(()))
    assert [(d.image, d.found_src, d.expected_src) for d in drifts] == [
        (
            "skyline-apiserver-base",
            "osism/container-images-kolla templates/B/kolla-build.conf.j2",
            "openstack/kolla kolla/common/sources.py@stable/B",
        )
    ]
    assert not drifts[0].allowlisted


def test_run_reads_sources_py_at_the_declared_kolla_ref(fake_sources, monkeypatch):
    """kolla_build.kolla is what the build checks out, so it wins over the probe.

    release_to_ref tries stable/ first, which during an unmaintained transition
    resolves the frozen branch.
    """
    files = {
        (
            "release",
            "latest/openstack-A.yml",
        ): b"openstack_projects:\n  nova: stable-A\n",
        ("release", "latest/openstack-B.yml"): b"kolla_build:\n"
        b"  kolla: unmaintained/B\n  requirements: unmaintained-B\n"
        b"openstack_projects:\n  nova: stable-B\n  skyline-apiserver: stable-B\n",
        (
            "container_images_kolla",
            "templates/A/kolla-build.conf.j2",
        ): TEMPLATE.encode(),
        (
            "container_images_kolla",
            "templates/B/kolla-build.conf.j2",
        ): TEMPLATE.encode(),
    }
    monkeypatch.setattr(source, "read", lambda repo, path, config: files[(repo, path)])
    read_refs = []

    def read_at_ref(repo, path, ref, config, optional=False):
        read_refs.append(ref)
        return SOURCES_PY.encode()

    monkeypatch.setattr(source, "read_at_ref", read_at_ref)
    drifts = plugin.run(fake_sources, Allowlist(()))
    assert read_refs == ["stable/A", "unmaintained/B"]
    assert drifts[0].expected_src.endswith("@unmaintained/B")


def test_run_allowlist_marks_allowlisted(fake_sources):
    al = Allowlist(
        (AllowEntry(plugin=plugin.NAME, image="skyline-apiserver-base", reason="r"),)
    )
    drifts = plugin.run(fake_sources, al)
    assert len(drifts) == 1 and drifts[0].allowlisted


def test_run_template_error_is_a_source_error(fake_sources, monkeypatch):
    real = source.read

    def broken_template(repo, path, config):
        if repo == "container_images_kolla":
            return b"{% if %}"
        return real(repo, path, config)

    monkeypatch.setattr(source, "read", broken_template)
    with pytest.raises(SourceError, match="templates/A/kolla-build.conf.j2"):
        plugin.run(fake_sources, Allowlist(()))
