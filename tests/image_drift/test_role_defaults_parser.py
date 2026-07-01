from osism_drift.role_defaults import parse_role_defaults


def test_concrete_tag_extracted():
    body = b"""\
adminer_tag: '4.7'
adminer_namespace: library
"""
    out = parse_role_defaults(body)
    assert out == {"adminer": "4.7"}


def test_jinja_value_skipped():
    body = b"""\
ara_server_tag: "{{ lookup('vars', 'ara_server_tag', default=Undefined) }}"
"""
    out = parse_role_defaults(body)
    assert out == {}


def test_multiple_aliases_in_one_role_file():
    body = b"""\
manager_redis_tag: '7.4.6-alpine'
ara_server_mariadb_tag: '11.8.3'
some_other_value: foo
"""
    out = parse_role_defaults(body)
    assert out == {"manager_redis": "7.4.6-alpine", "ara_server_mariadb": "11.8.3"}


def test_empty_or_malformed_returns_empty():
    assert parse_role_defaults(b"") == {}
    assert parse_role_defaults(b"---\n") == {}
