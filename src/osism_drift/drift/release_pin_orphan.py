"""release_pin_orphan plugin: release base.yml pins no release transport reads."""

import re

from osism_drift import release, source
from osism_drift.model import DriftEntry

NAME = "release_pin_orphan"
DESCRIPTION = (
    "Flag release/<version>/base.yml docker_images pins that neither the "
    "generics manager render nor osism-ansible's versions.yml.j2 reads "
    "(pins that reach no deployment)."
)
INPUT_FILES = [
    ("release", "<release_version>/base.yml"),
    ("generics", "environments/manager/images.yml"),
    ("container_image_osism_ansible", "files/src/templates/versions.yml.j2"),
]
SUMMARY = (
    "{n} release base.yml pins that no release transport reads (they reach "
    "no deployment):"
)
REMEDIATION = (
    "find out what the pin was added for. If a role deploys the image, carry "
    "<alias>_tag in versions.yml.j2; if its consumer is gone, remove it from "
    "base.yml together with its etc/images.yml entry. If it awaits a planned "
    "role, or something reads it outside the two transports (a role default "
    "that governs on purpose, a CI job fetching base.yml), allowlist it with "
    "the tracking issue or that reader as the reason."
)

# The two templates that carry docker_images pins into a deployment. Any
# versions['<key>'] reference counts, not only whole-line <alias>_tag pins:
# this plugin's remediation is to delete the pin, so a guard or condition that
# reads the key is enough to keep it.
_TRANSPORTS = [
    ("generics", "environments/manager/images.yml"),
    ("container_image_osism_ansible", "files/src/templates/versions.yml.j2"),
]

_KEY_RE = re.compile(r"""versions\[['"](\w+)['"]\]""")


def run(config, allowlist, verbose: bool = False) -> list:
    release_bytes = source.read("release", f"{config.release_version}/base.yml", config)
    docker_images = release.parse_release(release_bytes)

    read = set()
    for repo, path in _TRANSPORTS:
        body = source.read(repo, path, config).decode("utf-8", errors="ignore")
        read.update(_KEY_RE.findall(body))

    base_src = f"release/{config.release_version}/base.yml"
    drifts = []
    for key in sorted(set(docker_images) - read):
        d = DriftEntry(
            plugin=NAME,
            image=key,
            alias=key,
            expected=(
                f"a versions['{key}'] reader in the generics manager render "
                "or osism-ansible's versions.yml.j2"
            ),
            found=f"pinned as {docker_images[key]!r}; neither template reads it",
            # Constant across entries so the report groups all orphans into one
            # block (the per-key detail lives in `found`/`expected`, JSON only).
            expected_src=(
                "generics/environments/manager/images.yml, "
                "container-image-osism-ansible/files/src/templates/versions.yml.j2"
            ),
            found_src=base_src,
            summary=SUMMARY,
            remediation=REMEDIATION,
        )
        drifts.append(allowlist.apply(d))
    return drifts
