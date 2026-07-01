import pytest
from osism_drift.sbom_map import parse_sbom_keys

SAMPLE = b"""\
import logging

SBOM_IMAGE_TO_VERSION = {
    "heat": "heat-api",
    "ironic": "ironic-api",
    "kolla_toolbox": "kolla-toolbox",
    "kolla-toolbox": "kolla-toolbox",
}

OTHER = {"nope": 1}
"""


def test_extracts_keys():
    assert parse_sbom_keys(SAMPLE) == {
        "heat",
        "ironic",
        "kolla_toolbox",
        "kolla-toolbox",
    }


def test_missing_map_raises():
    with pytest.raises(ValueError):
        parse_sbom_keys(b"X = 1\n")
