"""Which repos release latest/base.yml pins, and the git ref each is read at.

Shared by the defaults corpus (which reads the pinned repos again at their
pins) and the release gate (which reads every pinned repo at its pinned tag).
"""

import yaml

from osism_drift import source
from osism_drift.http import SourceError

# latest/base.yml *_version keys -> the repo they pin.
PIN_KEYS = (
    ("ansible_playbooks", "playbooks_version"),
    ("ansible_playbooks_manager", "manager_playbooks_version"),
    ("generics", "generics_version"),
)

# What the release gate pins beyond PIN_KEYS.
GATE_PIN_KEYS = PIN_KEYS + (("defaults", "defaults_version"),)

# The keys the ceph image build reads from its ceph file (the first of
# CEPH_BUILD_FILES that exists); everything else in it is ignored.
CEPH_BUILD_KEYS = ("playbooks_version", "generics_version")
CEPH_BUILD_FILES = ("latest/ceph_ansible.yml", "latest/ceph.yml")


def pin_to_ref(pin: str) -> str:
    """The git tag for a base.yml pin: collection pins lack the tag's `v`
    (`0.20261005.0`), *_version pins carry it (`v0.20261005.0`). Anything not
    starting with a digit (`v…`, a branch) is used verbatim."""
    return f"v{pin}" if pin[:1].isdigit() else pin


def collection_pins(base: dict) -> list[tuple[str, str]]:
    """(ansible_collection_<x>, pin) for each osism.<x> collection, sorted."""
    return [
        ("ansible_collection_" + name[len("osism.") :], str(pin))
        for name, pin in sorted((base.get("ansible_collections") or {}).items())
        if name.startswith("osism.")
    ]


def _mapping(raw, path):
    doc = yaml.safe_load(raw)
    if doc is None:
        return {}
    if not isinstance(doc, dict):
        raise SourceError(f"release {path} is not a mapping")
    return doc


def _ceph_build_file(config):
    for path in CEPH_BUILD_FILES:
        raw = source.read_optional("release", path, config)
        if raw is not None:
            return path, _mapping(raw, path)
    raise SourceError(
        "release has neither " + " nor ".join(CEPH_BUILD_FILES) + ": "
        "the ceph image build would have no pins to read"
    )


def _check_ceph_build_file(base, config) -> None:
    path, ceph = _ceph_build_file(config)
    for key in CEPH_BUILD_KEYS:
        if key not in ceph:
            raise SourceError(f"{key} missing from release {path}")
        if str(ceph[key]) != str(base[key]):
            raise SourceError(
                f"release {path} {key} is {ceph[key]}, but latest/base.yml "
                f"has {base[key]}: the ceph image would build from a different "
                "tag than the one checked"
            )


def gate_refs(config) -> dict[str, str]:
    """repo -> refs/tags/<tag> for every repo release latest pins."""
    base = _mapping(
        source.read("release", "latest/base.yml", config), "latest/base.yml"
    )
    pins = []
    for repo, key in GATE_PIN_KEYS:
        if key not in base:
            raise SourceError(f"{key} missing from release latest/base.yml")
        pins.append((repo, str(base[key])))
    pins.extend(collection_pins(base))
    osism = (base.get("osism_projects") or {}).get("osism")
    if osism is None:
        raise SourceError("osism_projects.osism missing from release latest/base.yml")
    pins.append(("python_osism", str(osism)))
    _check_ceph_build_file(base, config)
    return {repo: "refs/tags/" + pin_to_ref(pin) for repo, pin in pins}
