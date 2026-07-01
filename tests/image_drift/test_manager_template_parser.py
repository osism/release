from osism_drift.manager_template import extract_alias_map, extract_stream_resolved


def test_basic_alias_extraction():
    template = b"""\
adminer_tag: "{{ versions['adminer'] }}"
ara_server_mariadb_tag: "{{ versions['mariadb'] }}"
manager_redis_tag: "{{ versions['redis'] }}"
"""
    m = extract_alias_map(template)
    assert m == {
        "adminer": "adminer",
        "ara_server_mariadb": "mariadb",
        "manager_redis": "redis",
    }


def test_jinja_escaped_lines_ignored():
    # Lines using {{ '{{' }} ... {{ '}}' }} are not alias map entries —
    # they're Jinja2 escape sequences that emit a literal Ansible expression
    # into the rendered file (used for rolling-release images). Skip them;
    # the else-branch line still maps the alias.
    template = b"""\
{% if versions['osism_ansible'] == 'latest' -%}
osism_ansible_tag: "{{ '{{' }} osism_ansible_version|default('latest') {{ '}}' }}"
{% else -%}
osism_ansible_tag: "{{ versions['osism_ansible'] }}"
{% endif -%}
"""
    m = extract_alias_map(template)
    assert m == {"osism_ansible": "osism_ansible"}


def test_double_quoted_versions_key():
    template = b'foo_tag: "{{ versions["foo"] }}"\n'
    m = extract_alias_map(template)
    assert m == {"foo": "foo"}


def test_lines_without_pattern_skipped():
    template = b"""\
not_a_tag_line: "value"
adminer_image: "{{ '{{' }} docker_registry {{ '}}' }}/library/adminer:..."
adminer_tag: "{{ versions['adminer'] }}"
"""
    m = extract_alias_map(template)
    assert m == {"adminer": "adminer"}


def test_extract_stream_resolved_basic():
    template = b"""\
plain_tag: "{{ versions['plain'] }}"
ceph_ansible_tag: "{{ '{{' }} ceph_version|default(manager_version) {{ '}}' }}"
unrelated: "value"
"""
    assert extract_stream_resolved(template) == {"ceph_ansible"}
    assert extract_alias_map(template) == {"plain": "plain"}


def test_extract_stream_resolved_multiple():
    template = b"""\
{% if versions['osism_ansible'] == 'latest' -%}
osism_ansible_tag: "{{ '{{' }} osism_ansible_version|default('latest') {{ '}}' }}"
{% else -%}
osism_ansible_tag: "{{ versions['osism_ansible'] }}"
{% endif -%}
{% if 'ceph_ansible' in versions -%}
ceph_ansible_tag: "{{ versions['ceph_ansible'] }}"
{% else -%}
ceph_ansible_tag: "{{ '{{' }} ceph_version|default(manager_version) {{ '}}' }}"
{% endif -%}
"""
    assert extract_stream_resolved(template) == {"osism_ansible", "ceph_ansible"}
    assert extract_alias_map(template) == {
        "osism_ansible": "osism_ansible",
        "ceph_ansible": "ceph_ansible",
    }
