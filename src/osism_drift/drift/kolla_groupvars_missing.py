"""kolla_groupvars_missing: upstream global var OSISM never mirrored.

The add-direction mirror of kolla_enablement_orphan. osism/defaults all/*.yml is
OSISM's hand-maintained copy of upstream kolla-ansible's group_vars/all, and it
*replaces* that layer at deploy time (upstream's own group_vars/all is not loaded
in an OSISM deployment). So an upstream group_vars key that OSISM never mirrored
is undefined in the effective var context: if an upstream role task references it
with no fallback, the play aborts. This is the class behind the 2025.2
keystone_listen_port breakage (added upstream in the uWSGI migration, never
picked up by osism/defaults, aborting the keystone upgrade).

The check diffs key spaces: the union of upstream group_vars/all top-level keys
across the supported release range MINUS everything OSISM supplies to the
container's group_vars/all. OSISM supplies from THREE paths, all counted (via
enablement.osism_groupvars_keys): osism/defaults all/*.yml, the rendered
container-image-kolla-ansible versions.yml (openstack_release, the kolla_*_version
pins, ...), and the per-release container-image-kolla-ansible
overlays/<release>/kolla-ansible.yml the Dockerfile bakes into the image (the
external-Ceph keyrings, the 2024.x swift group_vars, ...) — a var supplied only by
one of these must not false-positive as missing.
Union across releases (not intersection) because OSISM's single defaults set must
satisfy every supported release — a key introduced upstream at only the newest
release is still required there. Top-level group_vars only, never
role-defaults/tasks/tests, matching kolla_enablement_orphan; keys are compared
verbatim (an Ansible var name is an exact identifier).

Not every upstream key is a bug. The most common non-bug is a var for a service
OSISM does not ship/support at all: no environment enables it, so the var is never
evaluated and its absence never bites. (This is distinct from "off in the base
defaults", which is unreliable — a product environment can enable a service the
base leaves off, e.g. metalbox turns on ironic, so enable_ironic_pxe_filter does
bite there.) Other deliberate omissions: a var OSISM supplies another way, or an
upstream typo. Those are carried in the allowlist with a reason; everything for a
service OSISM might ship is a real gap to mirror into osism/defaults.
"""

from osism_drift import enablement, source
from osism_drift.model import DriftEntry

NAME = "kolla_groupvars_missing"
DESCRIPTION = (
    "Flag upstream kolla-ansible group_vars/all keys that osism/defaults never "
    "mirrored, so they are undefined at deploy/upgrade time (add direction)."
)
INPUT_FILES = [
    ("defaults", "all/*.yml"),
    ("container_image_kolla_ansible", "files/src/templates/versions.yml.j2"),
    ("container_image_kolla_ansible", "overlays/*/kolla-ansible.yml"),
    ("kolla_ansible", "ansible/group_vars/all[.yml|/*.yml] (per resolved release ref)"),
]
SUMMARY = (
    "{n} upstream kolla-ansible group_vars OSISM defaults never mirrored; each is "
    "undefined in the deploy var context and aborts any role task that uses it:"
)
REMEDIATION = (
    "mirror the missing var into osism/defaults all/*.yml (copying upstream's "
    "definition — harmless when the service is off, needed when an environment "
    "enables it), or allowlist it with a reason if OSISM deliberately omits it: "
    "the related service is not shipped/supported by OSISM at all (so the var is "
    "never evaluated anywhere), a var OSISM supplies another way, or an upstream "
    "typo."
)


def missing_keys(config) -> set:
    """Upstream group_vars/all keys (union over the supported release range) that
    osism/defaults all/*.yml does not define. The raw drift set before allowlist."""
    releases = enablement.release_range(config)
    if not releases:
        # set().union(*[]) is empty; upstream - {} would then report nothing
        # (mass false negative). Fail loud instead, matching kolla_enablement_orphan.
        raise source.SourceError(
            "empty supported release range; cannot compute the upstream "
            "group_vars union"
        )
    upstream = set().union(
        *(enablement.upstream_groupvars_keys(r, config) for r in releases)
    )
    return upstream - enablement.osism_groupvars_keys(config)


def run(config, allowlist, verbose: bool = False) -> list[DriftEntry]:
    """Return missing-var drifts: upstream defines it, osism/defaults does not."""
    drifts = []
    for key in sorted(missing_keys(config)):
        d = DriftEntry(
            plugin=NAME,
            image=key,
            alias=key,
            expected=(
                "mirrored into osism/defaults all/*.yml (upstream defines it in "
                "group_vars/all at a supported release)"
            ),
            found="absent from osism/defaults all/*.yml (undefined at deploy/upgrade)",
            expected_src="openstack/kolla-ansible group_vars/all @ supported refs",
            found_src="osism/defaults all/*.yml",
        )
        drifts.append(allowlist.apply(d))
    return drifts
