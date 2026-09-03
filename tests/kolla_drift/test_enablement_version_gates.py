from osism_drift import enablement


def test_parses_a_gate_into_its_listed_releases():
    body = b"""
enable_redis: "{{ 'yes' if openstack_version in ['2024.1', '2024.2'] else 'no' }}"
"""
    assert enablement.parse_version_gates(body) == {
        "enable_redis": ["2024.1", "2024.2"]
    }


def test_parses_the_not_in_form_the_same_way():
    """Whether a named release is still supported does not depend on the sense."""
    body = b"enable_x: \"{{ 'a' if openstack_version not in ['2024.1'] else 'b' }}\"\n"
    assert enablement.parse_version_gates(body) == {"enable_x": ["2024.1"]}


def test_parses_a_named_release_literal():
    body = b"mariadb_image: \"{{ 'mariadb' if openstack_version in ['victoria'] }}\"\n"
    assert enablement.parse_version_gates(body) == {"mariadb_image": ["victoria"]}


def test_ignores_the_rest_of_the_expression():
    """Only the bracket list is read; other conditions and filters are not parsed."""
    body = (
        b"horizon_listen_port: \"{{ '8080' if (enable_haproxy | bool and "
        b"openstack_version not in ['2024.1', '2025.1']) else horizon_port }}\"\n"
    )
    assert enablement.parse_version_gates(body) == {
        "horizon_listen_port": ["2024.1", "2025.1"]
    }


def test_finds_a_gate_nested_inside_a_structured_value():
    """Values are not always scalars; a gate can sit inside a map or a list."""
    body = b"""
dims:
  ulimits:
    nofile: "{{ 1024 if openstack_version in ['2024.2'] else 2048 }}"
opts:
  - "{{ 'x' if openstack_version in ['victoria'] else 'y' }}"
"""
    assert enablement.parse_version_gates(body) == {
        "dims": ["2024.2"],
        "opts": ["victoria"],
    }


def test_var_without_a_gate_is_absent():
    body = b'plain: "{{ docker_image_url }}nova-api"\nother: 5\n'
    assert enablement.parse_version_gates(body) == {}


def test_deduplicates_repeated_literals_preserving_order():
    body = (
        b"v: \"{{ 'a' if openstack_version in ['2025.1', '2024.1'] else "
        b"('b' if openstack_version in ['2024.1'] else 'c') }}\"\n"
    )
    assert enablement.parse_version_gates(body) == {"v": ["2025.1", "2024.1"]}


def test_empty_and_non_mapping_documents_yield_nothing():
    assert enablement.parse_version_gates(b"") == {}
    assert enablement.parse_version_gates(b"- a\n- b\n") == {}


def test_per_release_file_round_trips():
    assert enablement.per_release_file("2024.2") == "all/010-2024.2.yml"
    assert enablement.per_release_file_target("010-2024.2.yml") == "2024.2"


def test_per_release_file_target_rejects_other_files():
    for name in ("001-kolla-defaults.yml", "099-kolla.yml", "010-.yml", "010-2024.2"):
        assert enablement.per_release_file_target(name) is None, name
