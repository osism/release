"""kolla_version_chain_inner: detect inert version pins, split by fix direction.

A versions['K'] key referenced in the kolla-ansible template but absent from
container-images-kolla's SBOM_IMAGE_TO_VERSION map is never emitted, so the
template line silently defaults to openstack_version. Compares key spaces only,
normalising hyphen/underscore; output-variable names and image names are NOT keys.

An inert pin has two opposite fixes, and which one is right depends on whether
OSISM actually deploys a built image for the service:

  - ADD a pin   -- the service is enabled in OSISM (enable_X truthy) AND kolla
                   can build an image for it (a docker/X dir exists upstream).
                   The pin should resolve; wire the missing SBOM key.
  - REMOVE the line -- otherwise (not enabled, or no upstream image). No
                   OSISM-built image backs the pin, so the template line is dead.

Emitting one generic "add or remove" hint conflates these (and points at the
wrong repo for the common remove case), so each finding carries its own
SUMMARY/REMEDIATION and the source pair of the file the operator must edit.
"""

from osism_drift import (
    enablement,
    kolla_docker,
    sbom_map,
    source,
    versions_template,
)
from osism_drift.model import DriftEntry

NAME = "kolla_version_chain_inner"
DESCRIPTION = (
    "Flag versions['K'] template keys absent from the producer's "
    "SBOM_IMAGE_TO_VERSION map (inert pins), split into wire-the-pin vs "
    "remove-the-dead-line by OSISM enablement and upstream buildability."
)
INPUT_FILES = [
    ("container_image_kolla_ansible", "files/src/templates/versions.yml.j2"),
    ("container_images_kolla", "src/tag-images-with-the-version.py"),
    ("defaults", "all/099-kolla.yml"),
    ("kolla", "docker/"),
]
# Module-level fallback for the report; every entry below sets a per-finding
# override, so these render only if an entry ever omits them.
SUMMARY = (
    "{n} image-version keys that the kolla-ansible template reads via "
    "versions['<key>'] but container-images-kolla's SBOM map never sets:"
)
REMEDIATION = (
    "wire the key into SBOM_IMAGE_TO_VERSION if OSISM deploys it, else remove "
    "the dead template line. Allowlist keys that are meant to default."
)

_TEMPLATE = "files/src/templates/versions.yml.j2"
_SBOM = "src/tag-images-with-the-version.py"
_ENABLE = "all/099-kolla.yml"
_DOCKER = "docker"

_ADD_SUMMARY = (
    "{n} image-version keys the kolla-ansible template reads via versions['<key>'] "
    "for services OSISM enables and kolla can build, but container-images-kolla's "
    "SBOM map never sets them, so the pin stays inert:"
)
_ADD_REMEDIATION = (
    "add the key to SBOM_IMAGE_TO_VERSION in tag-images-with-the-version.py so the "
    "pin is produced."
)
_ADD_EXPECTED_SRC = "container-images-kolla/src/tag-images-with-the-version.py"
_ADD_FOUND_SRC = "container-image-kolla-ansible/files/src/templates/versions.yml.j2"

_DEAD_SUMMARY = (
    "{n} versions['<key>'] template lines for services OSISM does not build (not "
    "enabled, or no upstream kolla image), so the pin can never resolve, dead lines:"
)
_DEAD_REMEDIATION = (
    "remove the dead versions['<key>'] line from the kolla-ansible template, or "
    "allowlist it if it is intentionally kept."
)
_DEAD_FOUND_SRC = "container-image-kolla-ansible/files/src/templates/versions.yml.j2"


def run(config, allowlist, verbose: bool = False) -> list[DriftEntry]:
    """Return inert-pin drifts, each routed to its add-vs-remove fix."""
    dead_expected_src = (
        f"openstack/kolla docker/ @ {source.current_ref('kolla', config)} "
        "(no OSISM-built image)"
    )
    template_bytes = source.read("container_image_kolla_ansible", _TEMPLATE, config)
    sbom_bytes = source.read("container_images_kolla", _SBOM, config)
    template_keys = versions_template.parse_versions_keys(template_bytes)
    sbom_keys = {enablement.canon(k) for k in sbom_map.parse_sbom_keys(sbom_bytes)}

    enabled = enablement.truthy_enables(
        enablement.parse_enable_flags(source.read("defaults", _ENABLE, config))
    )
    buildable = kolla_docker.parse(
        source.list_dir("kolla", _DOCKER, config, dirs_only=True)
    )

    drifts = []
    for key in sorted(template_keys):
        nkey = enablement.canon(key)
        if nkey in sbom_keys:
            continue
        if nkey in enabled and nkey in buildable:
            d = DriftEntry(
                plugin=NAME,
                image=key,
                alias=key,
                expected="emitted by SBOM_IMAGE_TO_VERSION",
                found="absent (enabled & buildable, but pin defaults to openstack_version)",
                expected_src=_ADD_EXPECTED_SRC,
                found_src=_ADD_FOUND_SRC,
                summary=_ADD_SUMMARY,
                remediation=_ADD_REMEDIATION,
            )
        else:
            d = DriftEntry(
                plugin=NAME,
                image=key,
                alias=key,
                expected="no OSISM-built image (not enabled and/or not buildable upstream)",
                found="dead versions['...'] line defaulting to openstack_version",
                expected_src=dead_expected_src,
                found_src=_DEAD_FOUND_SRC,
                summary=_DEAD_SUMMARY,
                remediation=_DEAD_REMEDIATION,
            )
        drifts.append(allowlist.apply(d))
    return drifts
