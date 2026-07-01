from osism_drift.secrets_map import parse_secret_keys


def test_extracts_top_level_keys():
    body = b"---\n# comment\nkeystone_password:\nrabbitmq_password: secret\n"
    assert parse_secret_keys(body) == {"keystone_password", "rabbitmq_password"}


def test_ignores_comments_indented_and_markers():
    body = (
        b"---\n"
        b"# heading\n"
        b"outer_password:\n"
        b"  nested_key: x\n"  # indented -> not a top-level var
        b"\n"
        b"other_password: '{{ lookup() }}'\n"  # jinja value, key still parsed
    )
    assert parse_secret_keys(body) == {"outer_password", "other_password"}


def test_empty_input():
    assert parse_secret_keys(b"") == set()
