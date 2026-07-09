"""role_unpinned plugin: role-default <alias>_tag pins absent from release base.yml."""

from osism_drift import release, role_scan, source
from osism_drift.model import DriftEntry

NAME = "role_unpinned"
SHOW_VALUES = True
DESCRIPTION = (
    "Detect role-default <alias>_tag pins whose alias-resolved release key is "
    "absent from release/base.yml — pinned only in the role, no canonical release pin."
)
INPUT_FILES = [
    ("release", "<release_version>/base.yml"),
    ("generics", "environments/manager/images.yml"),
    ("ansible_collection_services", "roles/*/defaults/main.yml"),
    ("container_image_osism_ansible", "files/src/templates/images.yml.j2"),
]
SUMMARY = "{n} <alias>_tag pins in role defaults with no release base.yml pin:"
REMEDIATION = (
    "add a pin to release base.yml (and wire <alias>_tag into the manager render "
    "template) to make it release-managed, or allowlist it if the image is "
    "intentionally role-managed."
)


def run(config, allowlist, verbose: bool = False) -> list:
    release_bytes = source.read("release", f"{config.release_version}/base.yml", config)
    docker_images = release.parse_release(release_bytes)

    expected_src = f"release/{config.release_version}/base.yml"
    drifts = []
    for pin in role_scan.iter_role_pins(config):
        if docker_images.get(pin.release_key) is not None:
            continue
        d = DriftEntry(
            plugin=NAME,
            image=pin.release_key,
            alias=pin.alias,
            expected="",
            found=pin.found,
            expected_src=expected_src,
            found_src=pin.found_src,
            summary=SUMMARY,
            remediation=REMEDIATION,
        )
        drifts.append(allowlist.apply(d))
    return drifts
