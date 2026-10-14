# The tag resolution against the published OSISM 11.0.0 inputs: the SBOM,
# role defaults, versions.yml and overlay of osism/kolla-ansible:0.20261008.0
# and osism/defaults from osism/inventory-reconciler:0.20261008.0. 11.0.0
# shipped ten containers that request tags its build never pushed
# (osism/issues#1462); the check must report exactly those. The fixture was
# read from the images once, outside this repository: they predate the label
# baseline (osism/issues#1463) that the check reads.

import gzip
import json
from pathlib import Path

from kolla_tags import check, osism_layers

FIXTURE = Path(__file__).parent / "fixtures" / "kolla-tags-11.0.0.json.gz"

EXPECTED = [
    ("hacluster-corosync", "hacluster_corosync_image_full"),
    ("hacluster-pacemaker", "hacluster_pacemaker_image_full"),
    ("hacluster-pacemaker-remote", "hacluster_pacemaker_remote_image_full"),
    ("httpd", "ironic_http_image_full"),
    ("httpd", "keystone_httpd_image_full"),
    ("httpd", "letsencrypt_webserver_image_full"),
    ("ovsdpdk-db", "ovsdpdk_db_image_full"),
    ("ovsdpdk-vswitchd", "ovsdpdk_vswitchd_image_full"),
    (
        "prometheus-openstack-network-exporter",
        "prometheus_openstack_network_exporter_image_full",
    ),
    ("prometheus-valkey-exporter", "prometheus_valkey_exporter_image_full"),
]


def report():
    with gzip.open(FIXTURE, "rt") as fp:
        data = json.load(fp)
    return check(
        data["roles"],
        osism_layers(data["overlay"], data["versions"], data["defaults"]),
        data["sbom_images"],
    )


def test_exactly_the_ten_containers_of_osism_issues_1462():
    result = report()
    assert sorted((m.image, m.var) for m in result.mismatches) == EXPECTED
    assert result.failed_images == sorted({image for image, _ in EXPECTED})
    assert result.unresolvable == []


def test_every_reference_and_unconsumed_image():
    # The check counts image references (*_image_full vars naming a pushed
    # image), not containers: nova_api_image_full and
    # masakari_monitors_image_full each serve two containers.
    result = report()
    assert result.references == 134
    assert result.ok_images == 120
    assert result.unconsumed == [
        "aodh-expirer",
        "hacluster-pcs",
        "ovsdpdk",
        "prometheus-mtail",
    ]
