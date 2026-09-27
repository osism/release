"""Tests for role_scan: the role set and the alias -> release-key map."""

from pathlib import Path

import pytest
from osism_drift import role_scan
from osism_drift.config import Config, Remote
from osism_drift.source import SourceError

FIXT = Path(__file__).parent / "fixtures"


@pytest.fixture
def cfg():
    return Config(
        remote=Remote("https://x/", "https://y/", "main", "osism"),
        base_dirs=(str(FIXT),),
        release_version="latest",
        plugins={},
        sources={},
    )


def _pins(cfg):
    return {pin.alias: pin for pin in role_scan.iter_role_pins(cfg)}


def test_validations_roles_are_scanned(cfg):
    pin = _pins(cfg)["tempest_osism"]
    assert pin.role == "tempest"
    assert pin.found_src == (
        "ansible-collection-validations/roles/tempest/defaults/main.yml"
    )
    assert pin.found == "latest"


def test_tempest_osism_tag_resolves_to_tempest(cfg):
    """The manifest key is tempest; only osism-ansible's versions.yml.j2 says so."""
    assert _pins(cfg)["tempest_osism"].release_key == "tempest"


def test_manager_template_aliases_still_resolve(cfg):
    assert _pins(cfg)["widget"].release_key == "gadget"


def test_runner_alias_map_strips_tag_and_version():
    pins = {
        "tempest_osism_tag": "tempest",
        "opentelemetry_collector_version": "opentelemetry_collector",
    }
    assert role_scan.runner_alias_map(pins) == {
        "tempest_osism": "tempest",
        "opentelemetry_collector": "opentelemetry_collector",
    }


def test_conflicting_alias_maps_are_an_error():
    with pytest.raises(SourceError, match="adminer"):
        role_scan.merge_alias_maps({"adminer": "adminer"}, {"adminer": "adminer_ng"})


def test_agreeing_alias_maps_merge():
    assert role_scan.merge_alias_maps(
        {"adminer": "adminer", "manager_redis": "redis"}, {"adminer": "adminer"}
    ) == {"adminer": "adminer", "manager_redis": "redis"}


def test_version_input_resolves_through_runner_template(cfg):
    assert _pins(cfg)["oteldemo"].release_key == "oteldemo"
