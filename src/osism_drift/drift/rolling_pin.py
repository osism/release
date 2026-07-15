"""rolling_pin plugin: release base.yml pins whose value is a rolling tag."""

from osism_drift import release, source
from osism_drift.model import DriftEntry

NAME = "rolling_pin"
SHOW_VALUES = True
DESCRIPTION = (
    "Detect release/<version>/base.yml docker_images pins whose value is a "
    "rolling tag (latest/main/...), which deploys a non-reproducible image."
)
INPUT_FILES = [
    ("release", "<release_version>/base.yml"),
]
SUMMARY = "{n} release base.yml pins set to a rolling tag (non-reproducible):"
REMEDIATION = (
    "replace the rolling tag with a concrete, immutable version in release "
    "base.yml (and wire <alias>_tag into the manager render template if the "
    "image deploys via a role default), or allowlist it if the image is "
    "rolling by design (e.g. a kolla-built test image)."
)

# Curated denylist of rolling (mutable) tag values, matched case-insensitively.
# A denylist rather than a "not-semver" heuristic so odd-but-pinned tags like
# `6.1-23.10_beta` are never mistaken for rolling.
ROLLING_TAGS = frozenset(
    {
        "latest",
        "main",
        "master",
        "stable",
        "edge",
        "nightly",
        "rolling",
        "dev",
        "devel",
        "develop",
        "head",
        "current",
    }
)


def run(config, allowlist, verbose: bool = False) -> list:
    release_bytes = source.read("release", f"{config.release_version}/base.yml", config)
    docker_images = release.parse_release(release_bytes)

    base_src = f"release/{config.release_version}/base.yml"
    drifts = []
    for key, value in docker_images.items():
        if value.lower() not in ROLLING_TAGS:
            continue
        d = DriftEntry(
            plugin=NAME,
            image=key,
            alias=key,
            expected="a concrete, immutable tag",
            found=value,
            expected_src=base_src,
            found_src=base_src,
            summary=SUMMARY,
            remediation=REMEDIATION,
        )
        drifts.append(allowlist.apply(d))
    return drifts
