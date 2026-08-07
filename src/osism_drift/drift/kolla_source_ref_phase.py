"""kolla_source_ref_phase: an OpenStack source ref left behind by a phase change.

Upstream renames stable/<release> to unmaintained/<release>, then deletes the
branch at EOL. tarballs.opendev.org keeps serving the artifact for a retired
branch name but stops regenerating it, so a build that still asks for
<project>-stable-<release>.tar.gz keeps succeeding while its sources sit frozen
at the moment of the rename. Nothing fails, so nothing prompts a fix.

For each supported release, this compares the ref configured in release
latest/openstack-<R>.yml against the latest lifecycle phase opendev actually
publishes a tarball for. A later phase being available means the configured
artifact is frozen and the build no longer receives upstream fixes.

Artifact-driven rather than branch-driven on purpose. The question is which
tarball the build consumes, and probing the published set answers it directly:
no upstream repository owner is needed for every project, and the check
self-limits at EOL, where no later artifact exists and the frozen tarball is the
only option.

Existence is probed with one HEAD per candidate rather than by reading the
per-project index listing. The listings are large (nova's is ~1.4 MB and 10k
entries) and timed out intermittently in practice, which for a nightly check
means noise; a HEAD is a few hundred bytes and answers exactly the question.

A probe that neither confirms nor denies (an outage, a throttled response) makes
that one ref unverified, not clean: it is reported as such rather than aborting
the run, so one upstream blip cannot discard every other plugin's findings while
still never being read as "no drift". See ProbeInconclusive.
"""

import sys
from dataclasses import dataclass

from osism_drift import enablement, http, source
from osism_drift.http import SourceError
from osism_drift.model import DriftEntry

NAME = "kolla_source_ref_phase"
DESCRIPTION = (
    "Flag OpenStack source refs in the release manifests that a later upstream "
    "lifecycle phase has superseded, so the build reads a frozen tarball."
)
INPUT_FILES = [
    ("release", "latest/openstack-<release>.yml"),
]
# Hosts this plugin reads that no --base-dir can stand in for: the question is
# what upstream publishes right now, which no checkout records. The driver
# refuses a local-only run that selects this plugin rather than reaching the
# network behind the operator's back.
EXTERNAL_HOSTS = ("tarballs.opendev.org",)
SUMMARY = (
    "{n} source refs whose tarball is no longer regenerated upstream because a "
    "later lifecycle phase is published, so these builds use frozen sources:"
)
# Fallback only. Findings carry a per-entry remediation naming the concrete
# target phase and file; see remediation_for().
REMEDIATION = (
    "set the openstack_projects ref to the latest published phase in "
    "release/latest/openstack-<release>.yml, and drop any downstream patch the "
    "newer sources already carry. Upstream renames stable/<release> to "
    "unmaintained/<release> and goes on serving the stable-<release> tarball "
    "without regenerating it, so nothing breaks and the ref has to be moved "
    "deliberately. Allowlist the project if it is deliberately pinned to a "
    "frozen tarball."
)

_URL = "https://tarballs.opendev.org/openstack/{project}/{project}-{ref}.tar.gz"
# Group-level, so all findings render as one block: the per-entry line already
# carries the project and both phases. A per-project value here would split the
# report into one group per finding.
EXPECTED_SRC = "tarballs.opendev.org/openstack/<project>/"
# Lifecycle order, earliest first. Only phases *after* the configured one are
# probed: an earlier phase's tarball keeps being served after the rename, so its
# presence says nothing about freshness.
#
# Only the phases opendev publishes a tarball for belong here. Upstream also
# tags the transitions -- <release>-eom when the stable branch becomes
# unmaintained, <release>-eol when the unmaintained branch is deleted -- but no
# tarball is built for a tag (verified 404 for every project and series
# checked). Listing them would spend two probes per project that can never
# match, and would point the reader at a target they cannot download.
_PHASES = ("stable-{r}", "unmaintained-{r}")

# What an unverified ref reports instead of a target phase. Distinct from a
# clean result on purpose: the check did not establish that the ref is current,
# it failed to establish anything.
UNRESOLVED_EXPECTED = "(unknown: probe failed)"
UNRESOLVED_SUMMARY = (
    "{n} source refs whose upstream lifecycle phase could not be established, "
    "so their freshness is unknown rather than confirmed current:"
)


class ProbeInconclusive(SourceError):
    """A phase probe that neither confirmed nor denied that a tarball exists.

    The distinction this plugin exists to protect is between "no later phase is
    published" and "we could not find out": collapsing the second into the first
    reports no drift and silently reinstates the blind spot. But that guard
    belongs on the finding, not on the run -- aborting would discard every other
    plugin's findings over one upstream hiccup -- so the plugin catches this per
    project and reports the ref as unverified.

    Derives from SourceError so that if one ever escapes uncaught, the run
    degrades to the ordinary source-failure abort instead of a traceback.

    `kind` is the coarse cause ("HTTP 503", "network error"), used for grouping
    so that refs failing the same way share one report block; `detail` is the
    full message, including the URL.
    """

    def __init__(self, kind: str, detail: str):
        super().__init__(detail)
        self.kind = kind
        self.detail = detail


def remediation_for(release: str, latest: str) -> str:
    """Remediation naming the concrete target phase, shared per (release, phase).

    Kept free of the project name so every project moving to the same phase
    groups into one report block rather than one block each.
    """
    return (
        f"set these openstack_projects refs to {latest} in "
        f"release/latest/openstack-{release}.yml, and drop any downstream patch "
        f"the newer sources already carry. Upstream stops regenerating a "
        f"phase's tarball once the release moves on but goes on serving it, so "
        f"nothing breaks and {latest} has to be set deliberately. Allowlist a "
        f"project that is deliberately pinned to the frozen tarball."
    )


def unresolved_remediation(kind: str) -> str:
    """Remediation for refs a failed probe left unverified.

    Keyed on `kind` alone -- no project, no URL -- so that every ref that failed
    the same way groups into one report block. The URLs reach stderr under -v.
    """
    return (
        f"re-run the check: probing {EXTERNAL_HOSTS[0]} for these projects "
        f"failed with {kind}, so whether a later lifecycle phase is published "
        f"is unknown. Treat this as an incomplete result, not a clean one -- "
        f"these refs may be reading frozen sources. Run with -v for the URLs."
    )


def tarball_exists(project: str, ref: str) -> bool:
    """True if opendev publishes <project>-<ref>.tar.gz.

    Only a 404 counts as absent. Any other unsuccessful status raises
    ProbeInconclusive, because reading an outage or a throttled response as "no
    later phase published" would silently report no drift -- the exact blind
    spot this plugin closes.
    """
    url = _URL.format(project=project, ref=ref)
    try:
        r = http.head(f"probing {project} {ref}", url, ok=(404,))
    except SourceError as e:
        kind = f"HTTP {e.status}" if e.status else "network error"
        raise ProbeInconclusive(kind, str(e)) from e
    return r.status_code == 200


def _later_phases(release: str, configured: str) -> list:
    """Lifecycle phases for `release` that come after `configured`."""
    refs = [t.format(r=release) for t in _PHASES]
    if configured not in refs:
        return []
    return refs[refs.index(configured) + 1 :]


@dataclass
class Scan:
    """What one release's phase scan concluded.

    `stale` is [(project, configured, latest)] for refs a later published phase
    supersedes. `unresolved` is [(project, configured, kind, detail)] for refs
    whose phase a failed probe left undecided.
    """

    stale: list
    unresolved: list


def scan(refs: dict, release: str, exists) -> Scan:
    """Classify each openstack_projects ref for `release`.

    `exists(project, ref)` reports whether that tarball is published, and may
    raise ProbeInconclusive. Refs that do not name the release are skipped: a
    project on its own branch scheme (e.g. gnocchi at stable/4.6) does not
    follow the release lifecycle.

    A project whose probe is inconclusive is reported unresolved and never also
    stale: with one candidate's answer unknown, the latest published phase is
    unknown too, so naming a target would be a guess.
    """
    result = Scan([], [])
    for project, configured in sorted(refs.items()):
        if not configured or release not in configured:
            continue
        latest = None
        try:
            for ref in _later_phases(release, configured):
                if exists(project, ref):
                    latest = ref
        except ProbeInconclusive as e:
            result.unresolved.append((project, configured, e.kind, e.detail))
            continue
        if latest is None:
            continue
        result.stale.append((project, configured, latest))
    return result


def run(config, allowlist, verbose: bool = False) -> list[DriftEntry]:
    """Return drifts for source refs superseded by a later published phase, plus
    one entry per ref a failed probe left unverified."""
    drifts = []
    for release in enablement.release_range(config):
        refs = enablement.parse_source_refs(
            source.read("release", f"latest/openstack-{release}.yml", config)
        )
        found_src = f"osism/release latest/openstack-{release}.yml"
        result = scan(refs, release, tarball_exists)
        for project, configured, latest in result.stale:
            d = DriftEntry(
                plugin=NAME,
                image=project,
                alias=project,
                expected=latest,
                found=configured,
                expected_src=EXPECTED_SRC,
                found_src=found_src,
                remediation=remediation_for(release, latest),
            )
            drifts.append(allowlist.apply(d))
        for project, configured, kind, detail in result.unresolved:
            if verbose:
                print(f"warning: {detail}", file=sys.stderr)
            d = DriftEntry(
                plugin=NAME,
                image=project,
                alias=project,
                expected=UNRESOLVED_EXPECTED,
                found=configured,
                expected_src=EXPECTED_SRC,
                found_src=found_src,
                summary=UNRESOLVED_SUMMARY,
                remediation=unresolved_remediation(kind),
            )
            drifts.append(allowlist.apply(d))
    return drifts
