"""kolla_inventory: detect upstream ansible inventory groups missing locally.

Compares the group (INI section) names of upstream openstack/kolla-ansible
ansible/inventory/multinode against the union of group names in the OSISM
inventory files generics/inventory/50-kolla and 51-kolla. A group present
upstream but absent locally is flagged, and its upstream members ride in
`expected` so a maintainer knows what to add.

Range-aware: multinode is read at every supported release's resolved ref
(source.release_to_ref), not at the single pinned kolla-ansible ref, because
OSISM ships ONE inventory for every release it builds while upstream renames
groups per release. 2026.1 renamed kolla-toolbox -> kolla_toolbox and
kolla-logs -> kolla_logs; the OSISM inventory keeps the hyphenated spelling, so
those two 2026.1 plays select no hosts and deploy nothing, silently. A
single-ref check pinned to an older release cannot see that at all, which is
why the group is the union over the range: a group missing at ANY supported
release is a finding, and a renamed group needs both spellings in the OSISM
inventory for as long as both releases are supported.

One entry per group rather than per (group, release) — the fix is to add the
group to the OSISM inventory once — with the releases that want it named in
`expected`/`expected_src`. Members come from the last release in the range
order (the newest, for the default sorted range), the spelling a maintainer is
adding for.

The comparison is one-way (upstream -> local). Intentional omissions are
covered by the allowlist (exact base groups plus prefix not-deployed
services).
"""

from osism_drift import enablement, inventory_sections, source
from osism_drift.model import DriftEntry

NAME = "kolla_inventory"
DESCRIPTION = (
    "Flag ansible inventory groups in upstream kolla-ansible multinode "
    "at any supported release that are absent from the OSISM 50/51-kolla "
    "inventory."
)
INPUT_FILES = [
    ("kolla_ansible", "ansible/inventory/multinode (per resolved release ref)"),
    ("generics", "inventory/50-kolla"),
    ("generics", "inventory/51-kolla"),
]
SUMMARY = (
    "{n} ansible inventory groups present in upstream kolla-ansible multinode "
    "at a supported release but missing from the OSISM 50/51-kolla inventory:"
)
REMEDIATION = (
    "add the group and its members to generics/inventory/50-kolla or 51-kolla, "
    "or allowlist it if the service is intentionally not deployed. A group "
    "upstream renamed (kolla-toolbox -> kolla_toolbox in 2026.1) needs BOTH "
    "spellings for as long as both releases are supported."
)

_MULTINODE = "ansible/inventory/multinode"
_50 = "inventory/50-kolla"
_51 = "inventory/51-kolla"
_FOUND_SRC = "generics/inventory/{50,51}-kolla"


def _local_groups(config) -> set:
    """Group names the OSISM inventory defines, across 50-kolla and 51-kolla."""
    return set(
        inventory_sections.parse_groups(source.read("generics", _50, config))
    ) | set(inventory_sections.parse_groups(source.read("generics", _51, config)))


def run(config, allowlist, verbose: bool = False) -> list[DriftEntry]:
    """Return drifts for kolla-ansible inventory groups missing from OSISM."""
    releases = enablement.release_range(config)
    if not releases:
        # No release means no multinode is read at all, so EVERY missing group
        # goes unreported (and every allowlist entry goes stale). Fail loud.
        raise source.SourceError(
            "empty supported release range; cannot compare against upstream "
            "kolla-ansible multinode"
        )

    local = _local_groups(config)
    where = {}  # group -> ["<release> (<ref>)", ...], in range order
    members = {}  # group -> members at the last release that wants it

    for release in releases:
        ref = source.release_to_ref("kolla_ansible", release, config)
        upstream = inventory_sections.parse_groups(
            source.read_at_ref("kolla_ansible", _MULTINODE, ref, config)
        )
        for group in sorted(set(upstream) - local):
            where.setdefault(group, []).append(f"{release} ({ref})")
            members[group] = upstream[group]

    drifts = []
    for group in sorted(where):
        at = ", ".join(where[group])
        member_str = ", ".join(members[group]) if members[group] else "(none)"
        d = DriftEntry(
            plugin=NAME,
            image=group,
            alias=group,
            expected=(
                f"present in upstream kolla-ansible multinode at {at}; "
                f"members: {member_str}"
            ),
            found="absent from OSISM inventory (50-kolla/51-kolla)",
            expected_src=(
                f"openstack/kolla-ansible/ansible/inventory/multinode @ {at}"
            ),
            found_src=_FOUND_SRC,
        )
        drifts.append(allowlist.apply(d))
    return drifts
