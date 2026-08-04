"""kolla_version_gate_orphan: osism/defaults still names a retired release.

Retirement removes a release from the supported range, but osism/defaults keeps
two kinds of reference to it that nothing fails on afterwards:

- a **version gate** -- `openstack_version in ['A', 'B']` -- selecting a value
  for particular releases. Once a listed release is gone, that branch can never
  be taken again.
- a **per-release file** -- `all/010-<release>.yml` -- a backward-compat layer
  extending the 001 mirror for an older supported release. Its own header says
  to delete it when that release leaves the range.

Both are dead rather than broken, so nothing prompts the cleanup: a `victoria`
gate sat in the image catalogue for years unnoticed. This compares every
reference against the supported range (the release/latest/openstack-*.yml file
set) and reports the ones that name a release outside it.

The mirror image of kolla_source_ref_phase: that plugin catches a reference that
should have moved *forward* when a release changed phase, this one a reference
that should have been *dropped* when a release was retired.

Findings are advisory. A gate on a release that can no longer be deployed may
still be load-bearing for an upgrade *from* it, so "outside the supported range"
means "confirm this is still needed", not "this is a defect". Deliberate keeps
belong in the allowlist.

Scope is deliberately osism/defaults only. The same redis/valkey cutover is
gated in shell `case` statements in testbed, metalbox and
container-image-kolla-ansible, but a `case` parser would have to handle `;;&`,
`;&`, nested case, arms sharing a line, quoting and heredocs -- and a subtle
mis-parse there yields a silent false negative, the failure this check exists to
prevent. Those copies are covered by the retirement checklist in the guide
instead, which names each file. Same call as kolla_source_ref_phase made for the
hardcoded requirements ref in container-images-kolla scripts/002-generate.sh.
"""

from osism_drift import enablement, source
from osism_drift.model import DriftEntry

NAME = "kolla_version_gate_orphan"
DESCRIPTION = (
    "Flag osism/defaults version gates and per-release compat files that name an "
    "OpenStack release outside the supported range, so their content is dead."
)
INPUT_FILES = [
    ("defaults", "all/*.yml"),
    ("release", "latest/openstack-*.yml (the supported release range)"),
]
SUMMARY = (
    "{n} version gates naming a release outside the supported range, so the "
    "branch they select can no longer be taken:"
)
REMEDIATION = (
    "drop the retired release from the gate; if that leaves the condition "
    "matching nothing, remove the conditional and keep the value that remains. "
    "Allowlist a gate deliberately kept for upgrades from that release."
)
# Per-entry overrides for the file findings, so they render as their own block:
# deleting a whole file is a different action from editing an expression.
FILE_SUMMARY = (
    "{n} per-release compat files for a release outside the supported range, "
    "which their own header says to delete at this point:"
)
FILE_REMEDIATION = (
    "delete the file. It exists only to extend the 001 mirror for an older "
    "release while that release is supported; once it leaves the range every key "
    "in the file is dead. Allowlist it if it is deliberately kept."
)

EXPECTED_SRC = "osism/release latest/openstack-*.yml (supported releases)"
_DEFAULTS_DIR = "all"


def stale_releases(named, supported) -> list:
    """The `named` releases that are not in `supported`, order preserved."""
    return [r for r in named if r not in supported]


def _entry(image, found, found_src, supported, summary, remediation):
    """One advisory finding: `image` still names `found`, which is unsupported."""
    return DriftEntry(
        plugin=NAME,
        image=image,
        alias=image,
        expected=", ".join(sorted(supported)),
        found=found,
        expected_src=EXPECTED_SRC,
        found_src=found_src,
        summary=summary,
        remediation=remediation,
        severity="advisory",
    )


def run(config, allowlist, verbose: bool = False) -> list[DriftEntry]:
    """Return advisory drifts for defaults references to unsupported releases."""
    supported = set(enablement.release_range(config))
    drifts = []
    for filename in sorted(source.list_dir("defaults", _DEFAULTS_DIR, config)):
        if not filename.endswith(".yml"):
            continue

        target = enablement.per_release_file_target(filename)
        if target is not None and target not in supported:
            drifts.append(
                allowlist.apply(
                    _entry(
                        image=filename,
                        found=target,
                        found_src=f"osism/defaults {_DEFAULTS_DIR}/",
                        supported=supported,
                        summary=FILE_SUMMARY,
                        remediation=FILE_REMEDIATION,
                    )
                )
            )

        body = source.read("defaults", f"{_DEFAULTS_DIR}/{filename}", config)
        gates = enablement.parse_version_gates(body)
        for var, named in sorted(gates.items()):
            dead = stale_releases(named, supported)
            if not dead:
                continue
            drifts.append(
                allowlist.apply(
                    _entry(
                        image=var,
                        found=", ".join(dead),
                        found_src=f"osism/defaults {_DEFAULTS_DIR}/{filename}",
                        supported=supported,
                        summary=SUMMARY,
                        remediation=REMEDIATION,
                    )
                )
            )
    return drifts
