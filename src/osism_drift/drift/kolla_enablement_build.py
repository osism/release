"""kolla_enablement_build: enabled service not built for a release.

For each supported release R, an OSISM-enabled service is a build gap when it is
buildable upstream at R yet missing from OSISM's build set for R:

  - enabled    -- enable_X is truthy in defaults all/099-kolla.yml
  - buildable  -- a docker/X dir exists in openstack/kolla at R's resolved ref
  - built      -- X appears in release latest/openstack-R.yml under
                  infrastructure_projects or openstack_projects

The upstream docker/ universe is the scope filter, so feature flags (which have
no image) are out of scope and no alias table is needed.

Range-aware: the upstream ref per release comes from source.release_to_ref. The
remote is consulted only for the docker/ listing and the ref probe; defaults and
release are read at their pins.
"""

from osism_drift import enablement, source
from osism_drift.model import DriftEntry

NAME = "kolla_enablement_build"
DESCRIPTION = (
    "Flag OSISM-enabled kolla services that are buildable upstream at a "
    "supported release but absent from that release's OSISM build set."
)
INPUT_FILES = [
    ("defaults", "all/099-kolla.yml"),
    ("release", "latest/openstack-<release>.yml"),
    ("kolla", "docker/ (per resolved release ref)"),
]
SUMMARY = (
    "{n} services enabled in OSISM and buildable upstream at this release, but "
    "absent from the release's OSISM build set, so no image is built for them:"
)
REMEDIATION = (
    "add the service to infrastructure_projects or openstack_projects in the "
    "release file, or allowlist it if it is intentionally not built."
)

_ENABLE = "all/099-kolla.yml"


def run(config, allowlist, verbose: bool = False) -> list[DriftEntry]:
    """Return build-gap drifts: enabled and upstream-buildable but not built."""
    enabled = enablement.truthy_enables(
        enablement.parse_enable_flags(source.read("defaults", _ENABLE, config))
    )
    drifts = []
    for release in enablement.release_range(config):
        ref = source.release_to_ref("kolla", release, config)
        buildable = {
            enablement.canon(d)
            for d in source.list_dir_at_ref(
                "kolla", "docker", ref, config, dirs_only=True
            )
        }
        built = enablement.parse_build_set(
            source.read("release", f"latest/openstack-{release}.yml", config)
        )
        for svc in sorted((enabled & buildable) - built):
            d = DriftEntry(
                plugin=NAME,
                image=svc,
                alias=svc,
                expected=(
                    f"buildable upstream at {ref} (docker/{svc}) and enabled in OSISM; "
                    f"expected in release/latest/openstack-{release}.yml build set"
                ),
                found=(
                    "absent from infrastructure_projects/openstack_projects "
                    f"@ openstack-{release}.yml"
                ),
                expected_src=f"openstack/kolla docker/ @ {ref}",
                found_src=f"osism/release latest/openstack-{release}.yml",
            )
            drifts.append(allowlist.apply(d))
    return drifts
