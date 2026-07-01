"""kolla_enablement_orphan: OSISM enables a service upstream no longer defines.

An OSISM enable flag (defaults all/099-kolla.yml enable_X) is an orphan when its
service X is absent from upstream kolla-ansible's enable-defaults at every
supported release: upstream removed or renamed the service, so the OSISM flag is
stale and should be cleaned up or migrated.

A service defined in any in-range release is still needed, so the test is against
the union of upstream top-level enable-defaults across the release range, not any
single release. The per-release upstream keys come from
enablement.upstream_enable_keys (the monolithic group_vars/all.yml, else the split
group_vars/all/*.yml), resolved per release via source.release_to_ref for
kolla-ansible. Only top-level defaults count; reference-only mentions in tasks or
tests do not.

SCOPE selects which OSISM enable flags are eligible, differing only in the OSISM
input set (enablement.osism_enable_ids):

  "truthy"   -- flags set to a truthy value. The only such orphan candidates are
                the OSISM-invented flags common and kolla_operations, both
                allowlisted, so this scope yields a clean, gateable baseline.
  "explicit" -- additionally includes dead enable_X: "no" cleanup flags, which
                are real orphans but not yet removed from osism/defaults.
"""

from osism_drift import enablement, source
from osism_drift.model import DriftEntry

NAME = "kolla_enablement_orphan"
DESCRIPTION = (
    "Flag OSISM enable_* flags whose service is absent from upstream "
    "kolla-ansible enable-defaults across all supported releases (orphan)."
)
INPUT_FILES = [
    ("defaults", "all/099-kolla.yml"),
    ("kolla_ansible", "ansible/group_vars/all[.yml|/*.yml] (per resolved release ref)"),
]
SUMMARY = (
    "{n} OSISM enable flags whose service upstream kolla-ansible no longer "
    "defines at any supported release; the service was removed or renamed "
    "upstream, leaving the flag orphaned:"
)
REMEDIATION = (
    "remove the stale enable_<name> from osism/defaults, or migrate it to the "
    "upstream replacement. Some flags (e.g. common, kolla_operations) are OSISM "
    "inventions with no upstream counterpart — keep those allowlisted rather "
    "than removed."
)

_ENABLE = "all/099-kolla.yml"
SCOPE = "explicit"  # see module docstring; also flags dead enable_X: "no" cruft


def run(config, allowlist, verbose: bool = False) -> list[DriftEntry]:
    """Return orphan-flag drifts: OSISM enables a service upstream dropped."""
    return _run(config, allowlist, SCOPE, verbose)


def orphan_ids(config, scope: str = SCOPE) -> set:
    """Service ids OSISM enables that upstream defines at no supported release.

    The raw orphan set (before the allowlist), shared with kolla_orphan_config
    so the companion/image sweep keys off the same dead-service determination.
    """
    osism = enablement.osism_enable_ids(
        enablement.parse_enable_flags(source.read("defaults", _ENABLE, config)), scope
    )
    releases = enablement.release_range(config)
    if not releases:
        # set().union(*[]) is empty, which would report EVERY selected flag as an
        # orphan (mass false positive). Fail loud instead.
        raise source.SourceError(
            "empty supported release range; cannot compute the upstream "
            "enable-defaults union"
        )
    upstream = set().union(
        *(enablement.upstream_enable_keys(r, config) for r in releases)
    )
    return osism - upstream


def _run(config, allowlist, scope, verbose: bool = False) -> list[DriftEntry]:
    drifts = []
    for sid in sorted(orphan_ids(config, scope)):
        d = DriftEntry(
            plugin=NAME,
            image=sid,
            alias=sid,
            expected=(
                "defined in upstream kolla-ansible enable-defaults at some "
                "supported release"
            ),
            found=(
                "absent from upstream enable-defaults across all supported "
                "releases (orphan)"
            ),
            expected_src="openstack/kolla-ansible group_vars enable-defaults @ supported refs",
            found_src="osism/defaults all/099-kolla.yml",
        )
        drifts.append(allowlist.apply(d))
    return drifts
