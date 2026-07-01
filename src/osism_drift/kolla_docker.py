"""Parser for openstack/kolla's docker/ service directory listing.

The input is the directory listing (already filtered to directories by
source.list_dir(..., dirs_only=True), which drops files such as
docker/macros.j2). The output is the normalised service key space
(hyphens → underscores) for comparison against the template's versions['K']
keys. Naming/variant directories are filtered later by the allowlist.
"""


def parse(names) -> set[str]:
    """Return the set of service names normalised to the underscore key space."""
    return {n.replace("-", "_") for n in names}
