from kolla_tags import check, osism_layers, split_reference

BUILD = "osism.harbor.regio.digital/kolla/release/2026.1"

COMMON = {  # osism/defaults all/001-common.yml, abridged
    "docker_registry": "registry.osism.tech",
    "docker_namespace": "kolla/release",
    "docker_image_name_prefix": "",
    "docker_image_url": "{{ docker_registry ~ '/' if docker_registry else '' }}"
    "{{ docker_namespace }}/{{ docker_image_name_prefix }}",
}
IMAGES = {  # osism/defaults all/002-images-kolla.yml, abridged
    "prometheus_tag": "{{ kolla_prometheus_version|default(kolla_image_version) }}",
    "hacluster_tag": "{{ kolla_hacluster_version|default(kolla_image_version) }}",
    "ironic_tag": "{{ kolla_ironic_version|default(kolla_image_version) }}",
    "keystone_tag": "{{ kolla_keystone_version|default(kolla_image_version) }}",
    "keystone_service_tag": "{{ keystone_tag }}",
    "letsencrypt_tag": "{{ kolla_letsencrypt_version|default(kolla_image_version) }}",
    "letsencrypt_webserver_image": "{{ docker_image_url }}"
    "{{ 'letsencrypt-webserver' if openstack_version in ['2025.2'] else 'httpd' }}",
}
VERSIONS = {  # the rendered versions.yml, abridged
    "openstack_version": "2026.1",
    "kolla_image_version": "{{ openstack_version }}",
    "kolla_prometheus_version": "3.5.5.20261008",
    "kolla_ironic_version": "35.1.1.20261008",
    "kolla_keystone_version": "29.0.3.20261008",
}


def container(prefix, image, tag):
    """The role defaults of one container, as kolla-ansible declares them."""
    return {
        f"{prefix}_image": "{{ docker_image_url }}" + image,
        f"{prefix}_tag": tag,
        f"{prefix}_image_full": "{{ " + prefix + "_image }}:{{ " + prefix + "_tag }}",
    }


PROMETHEUS = {
    "prometheus_tag": "{{ openstack_tag }}",
    **container("prometheus_server", "prometheus-server", "{{ prometheus_tag }}"),
    **container(
        "prometheus_valkey_exporter",
        "prometheus-valkey-exporter",
        "{{ prometheus_tag }}",
    ),
}
HACLUSTER = {
    "hacluster_tag": "{{ openstack_tag }}",
    **container("hacluster_corosync", "hacluster-corosync", "{{ hacluster_tag }}"),
}
IRONIC = {
    "ironic_tag": "{{ openstack_tag }}",
    **container("ironic_http", "httpd", "{{ ironic_tag }}"),
}
KEYSTONE = {
    "keystone_tag": "{{ openstack_tag }}",
    "keystone_service_tag": "{{ keystone_tag }}",
    "keystone_image": "{{ docker_image_url }}keystone",
    "keystone_image_full": "{{ keystone_image }}:{{ keystone_service_tag }}",
    **container("keystone_httpd", "httpd", "{{ keystone_tag }}"),
}
LETSENCRYPT = {
    "letsencrypt_tag": "{{ openstack_tag }}",
    **container(
        "letsencrypt_webserver", "letsencrypt-webserver", "{{ letsencrypt_tag }}"
    ),
}


def osism(images=IMAGES, versions=VERSIONS, more_defaults=()):
    return osism_layers(
        {},
        versions,
        [("001-common.yml", COMMON), ("002-images-kolla.yml", images), *more_defaults],
    )


def pushed(*images):
    return [f"{BUILD}/{image}" for image in images]


def found(report):
    return sorted(
        (m.image, m.role, m.var, m.resolved, m.pushed) for m in report.mismatches
    )


def test_parent_tag_inheritance_is_a_mismatch():
    report = check(
        {"prometheus": PROMETHEUS},
        osism(),
        pushed(
            "prometheus-server:3.5.5.20261008",
            "prometheus-valkey-exporter:1.82.0.20261008",
        ),
    )
    assert found(report) == [
        (
            "prometheus-valkey-exporter",
            "prometheus",
            "prometheus_valkey_exporter_image_full",
            "3.5.5.20261008",
            "1.82.0.20261008",
        )
    ]
    assert report.failed_images == ["prometheus-valkey-exporter"]
    assert report.ok_images == 1
    assert report.references == 2
    assert report.unresolvable == []


def test_series_fallback_is_a_mismatch():
    report = check(
        {"hacluster": HACLUSTER}, osism(), pushed("hacluster-corosync:3.1.7.20261008")
    )
    assert found(report) == [
        (
            "hacluster-corosync",
            "hacluster",
            "hacluster_corosync_image_full",
            "2026.1",
            "3.1.7.20261008",
        )
    ]


def test_one_image_with_several_consumers():
    report = check(
        {"ironic": IRONIC, "keystone": KEYSTONE, "letsencrypt": LETSENCRYPT},
        osism(),
        pushed("httpd:2.4.58.20261008", "keystone:29.0.3.20261008"),
    )
    assert found(report) == [
        (
            "httpd",
            "ironic",
            "ironic_http_image_full",
            "35.1.1.20261008",
            "2.4.58.20261008",
        ),
        (
            "httpd",
            "keystone",
            "keystone_httpd_image_full",
            "29.0.3.20261008",
            "2.4.58.20261008",
        ),
        (
            "httpd",
            "letsencrypt",
            "letsencrypt_webserver_image_full",
            "2026.1",
            "2.4.58.20261008",
        ),
    ]
    assert report.failed_images == ["httpd"]
    assert report.ok_images == 1
    assert report.references == 4


def test_image_full_with_a_tag_var_of_another_name():
    images = {**IMAGES, "keystone_service_tag": "0.0.1"}
    report = check(
        {"keystone": KEYSTONE}, osism(images=images), pushed("keystone:29.0.3.20261008")
    )
    assert found(report) == [
        ("keystone", "keystone", "keystone_image_full", "0.0.1", "29.0.3.20261008")
    ]


def test_role_local_duplicates_are_valid():
    nova_cell = {
        "project_name": "nova",
        **container("nova_libvirt", "{{ project_name }}-libvirt", "1.0"),
    }
    exporters = {
        "project_name": "prometheus",
        **container(
            "prometheus_node_exporter", "{{ project_name }}-node-exporter", "2.0"
        ),
    }
    report = check(
        {"nova-cell": nova_cell, "prometheus-node-exporters": exporters},
        osism(),
        pushed("nova-libvirt:1.0", "prometheus-node-exporter:2.0"),
    )
    assert report.mismatches == []
    assert report.unresolvable == []
    assert report.references == 2


def test_unconsumed_and_unbuilt_images():
    roles = {
        "prometheus": PROMETHEUS,
        "zun": container("zun_api", "zun-api", "1.0"),
    }
    report = check(
        roles, osism(), pushed("prometheus-server:3.5.5.20261008", "aodh-expirer:1.0")
    )
    assert report.unconsumed == ["aodh-expirer"]
    assert report.mismatches == []
    assert report.unresolvable == []


def test_reference_whose_image_is_unknown_is_unresolvable():
    report = check(
        {"x": container("x", "{{ nowhere }}x", "1.0")}, osism(), pushed("x:1.0")
    )
    assert [(u.role, u.var, u.image) for u in report.unresolvable] == [
        ("x", "x_image_full", None)
    ]
    assert "nowhere" in report.unresolvable[0].cause
    assert len(report.unplaced) == 1


def test_pushed_image_with_a_broken_tag_fails_that_image():
    report = check(
        {"x": container("x", "x", "{{ nowhere }}")}, osism(), pushed("x:1.0")
    )
    assert [(u.var, u.image) for u in report.unresolvable] == [("x_image_full", "x")]
    assert report.failed_images == ["x"]
    assert report.unconsumed == []
    assert report.references == 1
    assert report.failed_references == 1
    assert report.ok_images == 0


def test_unbuilt_image_with_a_broken_tag_is_not_a_finding():
    roles = {
        "prometheus": PROMETHEUS,
        "zun": container("zun_api", "zun-api", "{{ nowhere }}"),
    }
    report = check(roles, osism(), pushed("prometheus-server:3.5.5.20261008"))
    assert report.unresolvable == []
    assert report.failed_images == []


def test_reference_without_a_tag_is_unresolvable():
    role = {"x_image_full": "registry.example.com/kolla/x"}
    report = check({"x": role}, osism(), pushed("x:1.0"))
    assert [(u.var, u.cause, u.image) for u in report.unresolvable] == [
        ("x_image_full", "'registry.example.com/kolla/x' names no tag", "x")
    ]


def test_hops_name_their_layers():
    report = check(
        {"prometheus": PROMETHEUS},
        osism(),
        pushed("prometheus-valkey-exporter:1.82.0.20261008"),
    )
    hops = [(h.name, h.layer) for h in report.mismatches[0].hops]
    assert ("prometheus_valkey_exporter_tag", "role defaults: prometheus") in hops
    assert ("prometheus_tag", "defaults all/002-images-kolla.yml") in hops
    assert ("kolla_prometheus_version", "versions.yml") in hops


def test_defaults_files_override_in_name_order():
    role = container("a", "img", "{{ a_version }}")
    report = check(
        {"a": role},
        osism(
            more_defaults=[
                ("010-x.yml", {"a_version": "1"}),
                ("099-kolla.yml", {"a_version": "2"}),
            ]
        ),
        pushed("img:2"),
    )
    assert report.mismatches == []
    assert report.unresolvable == []


def test_empty_role_defaults():
    report = check({"empty": None, "prometheus": PROMETHEUS}, osism(), pushed())
    assert report.mismatches == []
    assert report.unresolvable == []


def test_split_reference_with_a_registry_port():
    assert split_reference("host:5000/kolla/x:1.0") == ("x", "1.0")
    assert split_reference("host:5000/kolla/x") is None
    assert split_reference("x:") is None
    assert split_reference(
        "registry.osism.tech/kolla/release/aodh-api:22.0.1.20261008"
    ) == (
        "aodh-api",
        "22.0.1.20261008",
    )
