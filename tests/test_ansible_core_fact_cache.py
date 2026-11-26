"""latest/base.yml must not move osism-ansible to ansible-core 2.19 or later
before the python-osism package and image it pins can read the new fact
cache.

From ansible-core 2.19 on, the redis fact cache stores a host's facts under
ansible_factss1_<host> as {"__payload__": "<JSON>"} instead of plain JSON
under ansible_facts<host>. python-osism reads the cache directly from
redis, and its releases without osism/utils/fact_cache.py read only the old
key: once osism-ansible writes the new one, osism get/dump facts, the facts
API, the RabbitMQ address lookup, osism reset facts --limit and the facts
freshness check of osism apply all see stale facts or none.

The Renovate rule for ansible-core in latest/base.yml blocks the bump, but a
hand edit does not go through Renovate. This test does.
"""

import pathlib

import pytest
import yaml

# First python-osism release (package and image) whose fact cache readers
# handle both key schemas. None while no such release exists: then any
# ansible-core >= 2.19 in latest/base.yml fails.
FACT_CACHE_OSISM = None

NEW_SCHEMA_CORE = (2, 19)

BASE_YML = pathlib.Path(__file__).resolve().parents[1] / "latest" / "base.yml"


def _version(value):
    return tuple(int(part) for part in str(value).split("."))


def check(base, minimum):
    """Return the problems with base (a parsed base.yml); empty if none."""
    core = base["ansible_core_version"]
    if _version(core)[:2] < NEW_SCHEMA_CORE:
        return []
    if minimum is None:
        return [
            f"ansible_core_version is {core}, but no python-osism release "
            "reads the fact cache of ansible-core 2.19+ yet "
            "(FACT_CACHE_OSISM is None)"
        ]
    pins = {
        "osism_projects.osism": base["osism_projects"]["osism"],
        "docker_images.osism": base["docker_images"]["osism"],
    }
    return [
        f"ansible_core_version is {core}, but {name} is {pin}, "
        f"older than {minimum}, the first python-osism release that reads the "
        "fact cache of ansible-core 2.19+"
        for name, pin in pins.items()
        if _version(pin) < _version(minimum)
    ]


def test_latest_base_yml():
    base = yaml.safe_load(BASE_YML.read_text())
    assert check(base, FACT_CACHE_OSISM) == []


def _base(core, package="0.20261111.1", image="0.20261111.1"):
    return {
        "ansible_core_version": core,
        "osism_projects": {"osism": package},
        "docker_images": {"osism": image},
    }


@pytest.mark.parametrize("minimum", [None, "0.20261201.0"])
def test_old_core_passes_with_old_osism(minimum):
    assert check(_base("2.18.18"), minimum) == []


@pytest.mark.parametrize("core", ["2.19.0", "2.20.10", "3.0.0"])
def test_new_core_without_reader_release_fails(core):
    (problem,) = check(_base(core), None)
    assert core in problem
    assert "FACT_CACHE_OSISM is None" in problem


def test_new_core_with_old_package_fails():
    problems = check(
        _base("2.20.10", package="0.20261111.1", image="0.20261201.0"),
        "0.20261201.0",
    )
    assert problems == [
        "ansible_core_version is 2.20.10, but osism_projects.osism is "
        "0.20261111.1, older than 0.20261201.0, the first python-osism release "
        "that reads the fact cache of ansible-core 2.19+"
    ]


def test_new_core_with_old_image_fails():
    (problem,) = check(
        _base("2.20.10", package="0.20261201.0", image="0.20261111.1"),
        "0.20261201.0",
    )
    assert "docker_images.osism is 0.20261111.1" in problem


@pytest.mark.parametrize("pin", ["0.20261201.0", "0.20261201.1", "0.20270101.0"])
def test_new_core_with_reader_release_passes(pin):
    assert check(_base("2.20.10", package=pin, image=pin), "0.20261201.0") == []


def test_versions_compare_numerically():
    # 0.20261201.10 is newer than 0.20261201.9, although it sorts before it
    # as a string.
    assert (
        check(_base("2.20.10", "0.20261201.10", "0.20261201.10"), "0.20261201.9") == []
    )
