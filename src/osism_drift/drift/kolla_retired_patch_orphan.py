"""kolla_retired_patch_orphan: 099-kolla.yml keys whose only consumer was a
carried OSISM patch retired for the newest supported release.

Three conditions must all hold for a key to be reported:

  1. PATCH-CONSUMED: the key appears (word-boundary match) in at least one
     file under container-image-kolla-ansible patches/<older-release>/.
  2. PATCH-ABSENT: the key appears in NO file under patches/<newest-release>/
     (including .disabled ones — .disabled is bring-up parking, not retirement:
     container-image-kolla-ansible#808 parked five patches at 2025.1 and
     #809-#812 re-enabled four of them in-cycle; the check fires only when a
     file disappears).
  3. UPSTREAM-ABSENT: upstream kolla-ansible does NOT define the key as a
     top-level default at ANY supported release, in either group_vars/all or
     ansible/roles/*/defaults/main.yml (decision 3).

Why the existing service-keyed chain cannot reach this axis:
  kolla_enablement_orphan -> kolla_orphan_config -> kolla_image_orphan ->
  kolla_secrets_orphan anchors every sweep on a service id derived from an
  enable_<id> flag.  A key that belongs to no such service (e.g.
  kolla_disable_python_deprecation_warnings) is invisible to all four.
  No existing plugin reads container-image-kolla-ansible/patches/, where
  the only consumer of such a key lives.

Ref resolution always goes through source.release_to_ref, never through
string-formatting stable/<release>: 2024.1 resolves to unmaintained/2024.1
and 2024.2 to 2024.2-eol, so a stable/-only assumption would silently miss
two releases from the upstream union and produce false positives.
"""

import re

from osism_drift import enablement, source
from osism_drift.drift.kolla_orphan_config import dead_service_set, owning_service
from osism_drift.model import DriftEntry

NAME = "kolla_retired_patch_orphan"
DESCRIPTION = (
    "Flag osism/defaults 099-kolla.yml keys whose only consumer was an OSISM "
    "carried patch retired for the newest release and which upstream kolla-ansible "
    "defines at no supported release."
)
INPUT_FILES = [
    ("defaults", "all/099-kolla.yml"),
    ("container_image_kolla_ansible", "patches/<release>/ (all files)"),
    ("kolla_ansible", "ansible/ (per resolved release ref)"),
]
# Findings split into two blocks, because they call for different actions.
# Whether the retired patch was still ACTIVE at the release it was last carried
# in, or was already parked as .disabled, decides whether anything changed at
# the newest release.  A .disabled patch is not applied, so its keys stopped
# having an effect when it was parked, not when it was deleted -- deleting it
# only made the dead key visible.  Merging both into one list would ask a
# reader to tell "some cruft to delete" from "a behaviour that just stopped"
# by spotting a .disabled suffix in a path.
SUMMARY = (
    "{n} osism/defaults 099-kolla.yml keys whose carried patch was still "
    "applied at the release it was last carried in and is absent at the newest, "
    "so the behaviour it implemented is no longer applied:"
)
REMEDIATION = (
    "the behaviour this key drove is gone at the newest release. Decide "
    "whether it should be restored -- a patch for the new release, or a native "
    "equivalent -- before removing the key from osism/defaults "
    "all/099-kolla.yml. Allowlist it if losing the behaviour is intended."
)

# Per-entry overrides (see DriftEntry.summary/remediation) for keys whose patch
# was already .disabled when it was last carried.
PARKED_SUMMARY = (
    "{n} osism/defaults 099-kolla.yml keys whose only consumer was a carried "
    "patch that was already .disabled when it was last carried and has now been "
    "dropped, so the keys have been inert since it was parked:"
)
PARKED_REMEDIATION = (
    "nothing changed at the newest release -- the key stopped having an effect "
    "when its patch was parked, and dropping the patch only made that visible. "
    "Remove the key from osism/defaults all/099-kolla.yml, or allowlist it if "
    "it is deliberately kept against the patch being revived."
)

_KOLLA_OPINION_FILE = "all/099-kolla.yml"
_CI_REPO = "container_image_kolla_ansible"
_PATCHES_BASE = "patches"
_DISABLED_SUFFIX = ".disabled"

# Per-release state of a key's patch consumers, as computed by _scan_patches.
# "parked" means every consuming file at that release was .disabled, so the key
# was defined but had no effect there; "absent" means no file consumed it.
_ACTIVE = "active"
_PARKED = "parked"
_ABSENT = "absent"


def _scan_patches(config, definitions, releases):
    """Scan patch files for all releases.

    Returns (consumed_older, consumed_newest, last_consumer, state_by_rel) where:
      consumed_older  — keys found in any older-release patch
      consumed_newest — keys found in any newest-release patch
      last_consumer   — {key: (release, path, active)} for the latest older
                        release that consumed each key: the release, a
                        representative consuming file, and whether any file
                        consuming it there was applied rather than .disabled.
                        `active` decides which report block the finding lands
                        in; an active file is preferred as the representative,
                        since that is the one whose removal changed behaviour.
      state_by_rel    — {key: {release: _ACTIVE | _PARKED | _ABSENT}} for every
                        key in `definitions` at every release in `releases`.
                        This is the same applied-vs-.disabled distinction
                        `last_consumer.active` makes, recorded per release
                        instead of only for the last carrying one, so a finding
                        can name where the key is still consumed.

    A missing older patches/<rel>/ directory is treated as "no consumers at
    that release" (missing_ok=True) and does not raise.  A missing or empty
    newest patches/<newest>/ raises SourceError (decision 6): a new release
    directory that has not been created yet is an absence of evidence, not
    evidence of absence.  A patch file that lists but cannot be read raises for
    the same reason.
    """
    patterns = {k: re.compile(r"\b" + re.escape(k) + r"\b") for k in definitions}
    sorted_rels = sorted(releases)
    newest = sorted_rels[-1]

    consumed_older = set()
    consumed_newest = set()
    # {key: release} and {key: [paths]} for the latest older release that
    # consumed the key. Releases are walked in sorted order, so a later release
    # resets the path list rather than appending to the earlier one's.
    last_rel = {}
    paths_at_last_rel = {}
    state_by_rel = {k: {} for k in definitions}

    for rel in sorted_rels:
        is_newest = rel == newest
        paths = source.list_tree(
            _CI_REPO,
            f"{_PATCHES_BASE}/{rel}",
            config,
            missing_ok=not is_newest,
        )
        if is_newest and not paths:
            raise source.SourceError(
                f"patches/{newest}/ is missing or empty in "
                "container-image-kolla-ansible; cannot determine which keys "
                "the newest release still consumes (create the patches "
                "directory before running this check)"
            )
        # Per-release tallies: `seen_here` is any consumer, `active_here` only
        # applied ones. A key in `seen_here` but not `active_here` was parked.
        seen_here = set()
        active_here = set()
        for path in sorted(paths):
            # No try/except: every path here came from the list_tree above at
            # the same ref, so a read that fails is a transport failure, a
            # throttled response or an outage -- never "absent".  Swallowing one
            # would drop a newest-release consumer (a false positive) or an
            # older-release one (a suppressed finding), from a report that still
            # looked complete.  Let it propagate; the driver exits 2.
            body = source.read(_CI_REPO, path, config).decode("utf-8", errors="ignore")
            is_parked = path.endswith(_DISABLED_SUFFIX)
            for k, pat in patterns.items():
                if pat.search(body):
                    seen_here.add(k)
                    if not is_parked:
                        active_here.add(k)
                    if is_newest:
                        consumed_newest.add(k)
                    else:
                        consumed_older.add(k)
                        if last_rel.get(k) != rel:
                            last_rel[k] = rel
                            paths_at_last_rel[k] = []
                        paths_at_last_rel[k].append(path)

        for k in definitions:
            if k in active_here:
                state_by_rel[k][rel] = _ACTIVE
            elif k in seen_here:
                state_by_rel[k][rel] = _PARKED
            else:
                state_by_rel[k][rel] = _ABSENT

    last_consumer = {}
    for k, rel in last_rel.items():
        paths_here = paths_at_last_rel[k]
        applied = [q for q in paths_here if not q.endswith(_DISABLED_SUFFIX)]
        # An active file anywhere at that release makes the key active there,
        # even if a .disabled file also mentions it.
        active = bool(applied)
        last_consumer[k] = (rel, (applied or paths_here)[0], active)

    return consumed_older, consumed_newest, last_consumer, state_by_rel


def _range_sentence(states, releases) -> str:
    """One clause naming where the key is still consumed and where it is dead.

    `states` is one key's {release: state} map from _scan_patches. Reads, for a
    patch applied at the two oldest of five releases and parked at the next
    two:

        still consumed at 2024.1, 2024.2 (patch active); dead at 2025.1,
        2025.2 (patch parked) and 2026.1 (patch absent)

    Parked releases are listed before absent ones. A group with no releases is
    dropped. The absent group is never empty for a key that became a finding:
    condition 2 requires no consumer at the newest release.
    """
    rels = sorted(releases)
    live = [r for r in rels if states.get(r) == _ACTIVE]
    parked = [r for r in rels if states.get(r) == _PARKED]
    absent = [r for r in rels if states.get(r) == _ABSENT]

    dead_parts = []
    if parked:
        dead_parts.append(f"{', '.join(parked)} (patch parked)")
    if absent:
        dead_parts.append(f"{', '.join(absent)} (patch absent)")
    dead = " and ".join(dead_parts)

    if live:
        return f"still consumed at {', '.join(live)} (patch active); dead at {dead}"
    return f"no supported release still applies its patch: dead at {dead}"


def run(config, allowlist, verbose: bool = False) -> list[DriftEntry]:
    """Return retired-patch-orphan drifts for osism/defaults 099-kolla.yml."""
    # Step 1: definitions from the OSISM opinion file.
    body = source.read("defaults", _KOLLA_OPINION_FILE, config)
    definitions = enablement.top_level_keys(body)
    if not definitions:
        raise source.SourceError(
            f"defaults {_KOLLA_OPINION_FILE} has no top-level keys; "
            "the file may have moved or been renamed"
        )

    # Step 2: release range.
    releases = enablement.release_range(config)
    if not releases:
        raise source.SourceError(
            "empty supported release range; cannot compute the patch consumer set"
        )
    newest = sorted(releases)[-1]

    # Step 3+4: scan patches; compute candidates.
    consumed_older, consumed_newest, last_consumer, state_by_rel = _scan_patches(
        config, definitions, releases
    )
    candidates = consumed_older - consumed_newest
    if not candidates:
        return []

    # Step 5: upstream filter — drop any candidate upstream defines at any
    # supported release (group_vars/all OR role defaults, per decision 3).
    up_keys = set()
    for r in releases:
        up_keys |= enablement.upstream_groupvars_keys(r, config)
        up_keys |= enablement.upstream_role_default_keys(r, config)
    candidates -= up_keys
    if not candidates:
        return []

    # Step 6: chain-overlap filter — drop any candidate owned by a dead,
    # non-allowlisted service (kolla_orphan_config reports those already).
    # Use the allowlist-filtered set, not raw orphan_ids(), so allowlisted
    # services like kolla_operations are NOT excluded here (decision 4).
    dead = dead_service_set(config, allowlist)

    # Step 7: build one DriftEntry per survivor.
    drifts = []
    for var in sorted(candidates):
        if dead and owning_service(var, dead) is not None:
            continue
        rel, last_path, active = last_consumer.get(var, (None, None, True))
        rng = _range_sentence(state_by_rel.get(var, {}), releases)
        if rel is not None:
            state = "applied" if active else "already .disabled"
            found_text = (
                f"consumed by {last_path} ({state} at {rel}); {rng}; "
                "upstream defines it at no supported release"
            )
        else:
            # Unreachable by construction -- a candidate came from
            # consumed_older, which is what populates last_consumer -- but kept
            # so a future caller cannot turn it into a KeyError.
            found_text = f"{rng}; upstream defines it at no supported release"
        d = DriftEntry(
            plugin=NAME,
            image=var,
            alias=var,
            expected=(
                "either a patch consumer at the newest release or a definition "
                "in upstream kolla-ansible group_vars/all or role defaults"
            ),
            found=found_text,
            expected_src=(
                f"container-image-kolla-ansible patches/{newest}/ or "
                "openstack/kolla-ansible ansible/ @ supported refs"
            ),
            found_src=f"osism/defaults {_KOLLA_OPINION_FILE}",
            summary=SUMMARY if active else PARKED_SUMMARY,
            remediation=REMEDIATION if active else PARKED_REMEDIATION,
            severity="advisory",
        )
        drifts.append(allowlist.apply(d))
    return drifts
