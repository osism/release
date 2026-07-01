from osism_drift.versions_template import parse_versions_keys

SAMPLE = b"""\
openstack_version: "2025.2"
kolla_aodh_version: "{{ versions['aodh']|default(openstack_version) }}"
kolla_heat_version: "{{ versions['heat']|default(openstack_version) }}"
kolla_common_version: "{{ versions['kolla_toolbox']|default(openstack_version) }}"
kolla_ironic_version: "{{ versions['ironic']|default(openstack_version) }}"
kolla_ironic_prometheus_exporter_version: "{{ versions['ironic']|default(openstack_version) }}"
"""


def test_extracts_distinct_keys():
    assert parse_versions_keys(SAMPLE) == {"aodh", "heat", "kolla_toolbox", "ironic"}


def test_empty_input():
    assert parse_versions_keys(b"") == set()
