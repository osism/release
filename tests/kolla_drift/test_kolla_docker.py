from osism_drift.kolla_docker import parse


def test_normalises_hyphen_to_underscore():
    assert parse(["nova", "mariadb-server", "openstack-base"]) == {
        "nova",
        "mariadb_server",
        "openstack_base",
    }


def test_empty():
    assert parse([]) == set()
