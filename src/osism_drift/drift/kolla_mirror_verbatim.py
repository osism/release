"""kolla_mirror_verbatim: 001-kolla-defaults.yml must mirror upstream-newest.

osism/defaults all/001-kolla-defaults.yml is meant to be a VERBATIM copy of
upstream kolla-ansible group_vars/all at the newest supported release; every
OSISM opinion lives in a 099-* file. Nothing enforced that, so 001 drifts
(it currently carries keys upstream dropped in 2025.2). This is the enforcement
arm of Convention X: it flags every way 001 deviates from upstream-newest and,
for each deviation, prints the exact destination to move the key to. It never
tells anyone to allowlist a group_var (Convention X forbids it).

Companion to kolla_groupvars_missing, which only proves the upstream union is
supplied SOMEWHERE (it cannot see values or which file a key sits in). This
check owns 001 purity specifically.
"""

from osism_drift import enablement, source
from osism_drift.model import DriftEntry

NAME = "kolla_mirror_verbatim"
DESCRIPTION = (
    "Flag any all/001-kolla-defaults.yml key or value that differs from upstream "
    "kolla-ansible group_vars/all at the newest supported release, so 001 stays a "
    "verbatim mirror and OSISM opinions live in 099-*."
)
INPUT_FILES = [
    ("defaults", "all/001-kolla-defaults.yml"),
    ("kolla_ansible", "ansible/group_vars/all[.yml|/*.yml] (newest resolved ref)"),
]
# Module-level fallbacks (the registry test requires SUMMARY to contain "{n}").
# run() sets a per-entry summary/remediation carrying the newest release name and
# the exact destination, so the report groups findings by shape.
SUMMARY = "{n} key(s) in 001 differ from the upstream mirror:"
REMEDIATION = (
    "make 001 a verbatim copy of upstream group_vars/all; move OSISM deltas to 099-*."
)


def _s(v) -> str:
    """Display a parsed YAML value as a short string ('' for None)."""
    return "" if v is None else str(v)


def run(config, allowlist, verbose: bool = False) -> list[DriftEntry]:
    """Return every 001-vs-upstream-newest deviation, each with guided routing."""
    releases = enablement.release_range(config)
    if not releases:
        # Match kolla_groupvars_missing: an empty range would silently report
        # nothing (a mass false negative). Fail loud instead.
        raise source.SourceError(
            "empty supported release range; cannot determine the mirror target"
        )
    # sorted(): an explicit config.releases override is returned in caller order,
    # not sorted, so a bare [-1] could pick the wrong upstream mirror target.
    newest = sorted(releases)[-1]
    up = enablement.upstream_groupvars_values(newest, config)
    local = enablement.osism_mirror_values(config)
    dropped = enablement.dropped_key_release_map(config)
    non001 = enablement.osism_supply_excluding_mirror(config)
    ref = source.release_to_ref("kolla_ansible", newest, config)

    expected_src = f"openstack/kolla-ansible group_vars/all @ {ref}"
    found_src = "osism/defaults all/001-kolla-defaults.yml"

    def _mk(key, *, expected, found, summary, remediation):
        return DriftEntry(
            plugin=NAME,
            image=key,
            alias=key,
            expected=expected,
            found=found,
            expected_src=expected_src,
            found_src=found_src,
            summary=summary,
            remediation=remediation,
            severity="actionable",
        )

    up_keys, local_keys = set(up), set(local)
    drifts = []

    # Shape C — upstream-newest defines it, 001 lacks it.
    for k in sorted(up_keys - local_keys):
        drifts.append(
            _mk(
                k,
                expected=_s(up[k]),
                found="(absent)",
                summary=(
                    f"{{n}} upstream key(s) missing from the 001 mirror "
                    f"(upstream {newest} defines them):"
                ),
                remediation=(
                    f"mirror the upstream {newest} key and value verbatim into "
                    "all/001-kolla-defaults.yml."
                ),
            )
        )

    # Shape A — key in both, value differs.
    for k in sorted(up_keys & local_keys):
        if local[k] != up[k]:
            drifts.append(
                _mk(
                    k,
                    expected=_s(up[k]),
                    found=_s(local[k]),
                    summary=(
                        f"{{n}} key(s) whose 001 value differs from upstream {newest}:"
                    ),
                    remediation=(
                        f"restore the upstream {newest} value in 001; put OSISM's "
                        "value in 099-* (plain, or an openstack_version gate if it "
                        "must vary by release). Never allowlist."
                    ),
                )
            )

    # Shape B — in 001, absent from upstream-newest; sub-classify the home.
    # B-dup is checked before B-dropped: a dup that is also an older-upstream key
    # is still "already supplied elsewhere -> delete".
    for k in sorted(local_keys - up_keys):
        if k in non001:
            summary = "{n} key(s) in 001 that another OSISM layer already supplies:"
            remediation = (
                "delete each from all/001-kolla-defaults.yml — 001 mirrors upstream "
                f"{newest}, which does not define it, and 099-*/overlay/versions "
                "already supplies it."
            )
        elif k in dropped:
            # D8: home = all/010-<L>.yml, L = newest supported release below the
            # newest that still defines the key. Self-retires when L EOLs. Keys with
            # different L carry different summary/remediation, so the report groups
            # them by destination file. NOT 099, NOT the allowlist.
            L = dropped[k]
            path = enablement.groupvars_home(k, newest, up_keys, dropped)[0]
            summary = (
                f"{{n}} key(s) in 001 that upstream dropped by {newest}, "
                f"kept for release {L}:"
            )
            remediation = (
                f"move each to {path} (upstream-vanilla value; "
                f"self-retires when {L} EOLs). NOT 099, NOT the allowlist "
                "(parent spec D8)."
            )
        else:
            summary = "{n} OSISM-invented key(s) sitting in the 001 mirror:"
            remediation = (
                "move each to a 099-* file (Custom features). 001 must contain only "
                "upstream keys."
            )
        drifts.append(
            _mk(
                k,
                expected=f"(absent from upstream {newest})",
                found=_s(local[k]),
                summary=summary,
                remediation=remediation,
            )
        )

    return [allowlist.apply(d) for d in drifts]
