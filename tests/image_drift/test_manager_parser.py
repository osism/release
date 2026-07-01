from osism_drift.manager import (
    parse_manager,
    ManagerEntry,
    KIND_PLAIN,
    KIND_LATEST_OVERRIDE,
    KIND_UNRESOLVED,
)


def test_plain_value():
    body = b'adminer_tag: "5.4.2"\n'
    out = parse_manager(body)
    assert out["adminer"] == ManagerEntry(value="5.4.2", kind=KIND_PLAIN)


def test_latest_override_pattern():
    body = b"osism_ansible_tag: \"{{ osism_ansible_version|default('latest') }}\"\n"
    out = parse_manager(body)
    assert out["osism_ansible"] == ManagerEntry(
        value="latest", kind=KIND_LATEST_OVERRIDE
    )


def test_unresolved_jinja_marked():
    body = b"mystery_tag: \"{{ versions['something'] }}\"\n"
    out = parse_manager(body)
    assert out["mystery"].kind == KIND_UNRESOLVED


def test_multiple_entries():
    body = b"""\
adminer_tag: "5.4.2"
manager_redis_tag: "7.4.7-alpine"
osism_tag: "{{ osism_version|default('latest') }}"
"""
    out = parse_manager(body)
    assert out["adminer"].kind == KIND_PLAIN
    assert out["manager_redis"].value == "7.4.7-alpine"
    assert out["osism"].kind == KIND_LATEST_OVERRIDE
    assert out["osism"].value == "latest"
