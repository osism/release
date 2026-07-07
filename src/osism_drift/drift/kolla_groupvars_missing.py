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

A missing key is supplied, not judged: OSISM mirrors the full upstream union, so a
missing var is added to osism/defaults — to 001-kolla-defaults.yml if upstream
defines it at the newest supported release, else to a self-retiring
010-<last-release>.yml carrying it for the older releases that still define it
(deleted when that release ages out). OSISM does NOT decide "the service is not
shipped, so omit the var": that judgement is unreliable (a product environment can
enable a service the base leaves off — metalbox turns on ironic, so
enable_ironic_pxe_filter bites there), and a dropped var is cheap to carry as
self-retiring data. The allowlist is used only when OSISM already supplies the var
another way the detector cannot see. (An upstream *typo* is NOT allowlisted — it is
mirrored into 001 verbatim, so it is supplied and never surfaces here.)
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
    "mirror the missing var: into all/001-kolla-defaults.yml if upstream defines it "
    "at the newest supported release, else into a self-retiring "
    "all/010-<last-release>.yml (labelled for the newest supported release that "
    "still defines it, deleted when it ages out). Allowlist only if OSISM already "
    "supplies the var another way the detector cannot see."
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
