"""kolla_inventory: detect upstream ansible inventory groups missing locally.

Compares the group (INI section) names of upstream openstack/kolla-ansible
ansible/inventory/multinode (at the pinned kolla-ansible source ref) against the
union of group names in the OSISM inventory files generics/inventory/50-kolla
and 51-kolla. A group present upstream but absent locally is flagged, and its
upstream members ride in `expected` so a maintainer knows what to add.

The comparison is one-way (upstream -> local). Intentional omissions are
covered by the allowlist (exact base groups plus prefix not-deployed
services).
"""

from osism_drift import inventory_sections, source
from osism_drift.model import DriftEntry

NAME = "kolla_inventory"
DESCRIPTION = (
    "Flag ansible inventory groups in upstream kolla-ansible multinode "
    "that are absent from the OSISM 50/51-kolla inventory."
)
INPUT_FILES = [
    ("kolla_ansible", "ansible/inventory/multinode"),
    ("generics", "inventory/50-kolla"),
    ("generics", "inventory/51-kolla"),
]
SUMMARY = (
    "{n} ansible inventory groups present in upstream kolla-ansible multinode "
    "but missing from the OSISM 50/51-kolla inventory:"
)
REMEDIATION = (
    "add the group and its members to generics/inventory/50-kolla or 51-kolla, "
    "or allowlist it if the service is intentionally not deployed."
)

_MULTINODE = "ansible/inventory/multinode"
_50 = "inventory/50-kolla"
_51 = "inventory/51-kolla"
_FOUND_SRC = "generics/inventory/{50,51}-kolla"


def run(config, allowlist, verbose: bool = False) -> list[DriftEntry]:
    """Return drifts for kolla-ansible inventory groups missing from OSISM."""
    ref = source.current_ref("kolla_ansible", config)
    expected_src = f"openstack/kolla-ansible/ansible/inventory/multinode @ {ref}"
    upstream = inventory_sections.parse_groups(
        source.read("kolla_ansible", _MULTINODE, config)
    )
    local = set(
        inventory_sections.parse_groups(source.read("generics", _50, config))
    ) | set(inventory_sections.parse_groups(source.read("generics", _51, config)))

    drifts = []
    for group in sorted(set(upstream) - local):
        members = upstream[group]
        member_str = ", ".join(members) if members else "(none)"
        d = DriftEntry(
            plugin=NAME,
            image=group,
            alias=group,
            expected=(
                "present in upstream kolla-ansible multinode at "
                f"{ref}; members: {member_str}"
            ),
            found="absent from OSISM inventory (50-kolla/51-kolla)",
            expected_src=expected_src,
            found_src=_FOUND_SRC,
        )
        drifts.append(allowlist.apply(d))
    return drifts
