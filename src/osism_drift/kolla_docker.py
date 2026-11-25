"""openstack/kolla's image definition directory: listing and parsing.

kolla keeps one directory per image under docker/ up to 2026.1. From 2026.2
on, the directory is kolla/docker/. list_services() finds whichever the ref
has.

The input is the directory listing (already filtered to directories by
source.list_dir(..., dirs_only=True), which drops files such as
docker/macros.j2). The output is the normalised service key space
(hyphens → underscores) for comparison against the template's versions['K']
keys. Naming/variant directories are filtered later by the allowlist.
"""

from osism_drift import source
from osism_drift.http import SourceError

# Probed in this order. The old path comes first, so a ref that has it costs
# one listing, not two, against the GitHub API budget.
DOCKER_DIRS = ("docker", "kolla/docker")


def list_services(ref: str, config) -> tuple[list[str], str]:
    """The image directory names in openstack/kolla at `ref`, and their path.

    Raises SourceError when neither path holds any image directory, so a
    further upstream move fails the run instead of emptying the comparison.
    """
    for path in DOCKER_DIRS:
        names = source.list_dir_at_ref(
            "kolla", path, ref, config, dirs_only=True, missing_ok=True
        )
        if names:
            return names, path
    raise SourceError(
        f"no image directories under {' or '.join(DOCKER_DIRS)} in kolla@{ref}"
    )


def parse(names) -> set[str]:
    """Return the set of service names normalised to the underscore key space."""
    return {n.replace("-", "_") for n in names}
