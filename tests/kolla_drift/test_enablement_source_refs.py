from osism_drift import enablement


def test_parse_source_refs_returns_openstack_project_refs():
    body = b"""
infrastructure_projects:
  fluentd:
openstack_projects:
  neutron: stable-2024.1
  nova: unmaintained-2024.1
"""
    assert enablement.parse_source_refs(body) == {
        "neutron": "stable-2024.1",
        "nova": "unmaintained-2024.1",
    }


def test_parse_source_refs_preserves_hyphenated_project_names():
    """Names index tarball URLs, so they must not be canonicalised."""
    body = b"openstack_projects:\n  neutron-dynamic-routing: stable-2024.1\n"
    assert enablement.parse_source_refs(body) == {
        "neutron-dynamic-routing": "stable-2024.1"
    }


def test_parse_source_refs_keeps_valueless_entries():
    """A key with no ref is reported as None so callers can skip it."""
    body = b"openstack_projects:\n  nova:\n"
    assert enablement.parse_source_refs(body) == {"nova": None}


def test_parse_source_refs_empty_when_block_absent():
    assert enablement.parse_source_refs(b"infrastructure_projects:\n  fluentd:\n") == {}
