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


def test_tag_indirected_through_own_version_resolved():
    body = b"""\
opentelemetry_collector_tag: "{{ opentelemetry_collector_version }}"
opentelemetry_collector_version: '0.136.0'
"""
    out = parse_role_defaults(body)
    assert out == {"opentelemetry_collector": "0.136.0"}


def test_tag_indirected_through_own_version_non_string_pin():
    body = b"""\
substation_tag: "{{ substation_version }}"
substation_version: latest
"""
    out = parse_role_defaults(body)
    assert out == {"substation": "latest"}


def test_tag_indirection_whitespace_insensitive():
    body = b"""\
foo_tag: "{{foo_version}}"
foo_version: '1.2.3'
"""
    out = parse_role_defaults(body)
    assert out == {"foo": "1.2.3"}


def test_tag_indirection_skipped_when_version_non_concrete():
    body = b"""\
foo_tag: "{{ foo_version }}"
foo_version: "{{ lookup('vars', 'foo_version', default=Undefined) }}"
"""
    out = parse_role_defaults(body)
    assert out == {}


def test_tag_indirection_skipped_when_version_missing():
    body = b"""\
foo_tag: "{{ foo_version }}"
"""
    out = parse_role_defaults(body)
    assert out == {}


def test_tag_referencing_other_var_skipped():
    body = b"""\
foo_tag: "{{ bar_version }}"
bar_version: '1.2.3'
"""
    out = parse_role_defaults(body)
    assert out == {}


def test_composed_tag_left_for_later_pass():
    body = b"""\
foo_tag: "{{ foo_version }}-alpine"
foo_version: '16'
"""
    out = parse_role_defaults(body)
    assert out == {}
