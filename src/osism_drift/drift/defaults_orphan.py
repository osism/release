"""defaults_orphan: a variable osism/defaults sets that nothing reads.

osism/defaults ships its variables to every deployment, and nothing notices
when one stops doing anything: its reader is removed or renamed, or it never
matched its reader -- k3s_add_labels_monitoring against the merge pattern
^k3s_add_labels__.+$, a silent no-op since 2024-09. This check takes every
top-level key of the non-kolla layers (the kolla-owned ones belong to the
kolla_* checks) and looks for a read of it anywhere in the corpus
defaults_corpus builds: the OSISM repos at main and at the version `latest`
ships, and upstream kolla-ansible and ceph-ansible at every supported
release. A variable is reported only when no source reads it.

Third-party collections and galaxy roles are not in the corpus. A variable
only they read is allowlisted, with a reason that names the reading role.
"""

from osism_drift import defaults_corpus, source, var_refs
from osism_drift.defaults_corpus import CORE_REPOS
from osism_drift.model import DriftEntry

NAME = "defaults_orphan"
DESCRIPTION = (
    "Flag non-kolla variables osism/defaults sets that nothing reads: no OSISM "
    "repo (at main or at the latest pin) and no upstream kolla-ansible or "
    "ceph-ansible release in the supported range."
)
INPUT_FILES = [
    ("defaults", "all/*.yml, <group>/*.yml (non-kolla layers)"),
    ("release", "latest/base.yml (osism collections and their pins)"),
    *((repo, "whole repo, at main and at the latest pin") for repo in CORE_REPOS),
    ("ansible_collection_commons", "whole repo, at main and at the latest pin"),
    ("ansible_collection_services", "whole repo, at main and at the latest pin"),
    ("ansible_collection_validations", "whole repo, at main and at the latest pin"),
    ("kolla_ansible", "whole repo, per supported release"),
    ("ceph_ansible", "whole repo, per ceph-ansible flavour"),
]
SUMMARY = "{n} variables this defaults file sets that nothing reads:"
REMEDIATION = (
    "remove them from osism/defaults. If a third-party collection or galaxy "
    "role reads one (this check does not search those), allowlist it with a "
    "reason naming the role that reads it."
)
_NEAR_MISS_SUMMARY = (
    "{n} variables this defaults file sets that miss a merge_variables "
    "pattern by one underscore, so they never take effect:"
)
_UNCERTAIN_SUMMARY = (
    "{n} variables this defaults file sets look unread, but a "
    "merge_variables pattern could not be resolved:"
)
_UNRESOLVED_SUMMARY = "{n} merge_variables calls with a pattern this check cannot read:"
_UNRESOLVED_REMEDIATION = (
    "make the pattern a string literal, or teach defaults_orphan the form it "
    "uses. Until then this check cannot tell which variables are unread, so "
    "it reports them as advisory and fails on this call instead."
)


def is_checked(path: str) -> bool:
    """Whether a repo-relative osism/defaults file is in the checked set."""
    parts = path.split("/")
    if len(parts) != 2 or parts[0].startswith(".") or not var_refs.is_yaml(parts[1]):
        return False
    if path.startswith(("all/001-", "all/010-")) or "kolla" in parts[1]:
        return False
    return True


def defined_vars(config) -> list:
    """(var, repo-relative path) for every top-level key of a checked file."""
    root = source.tree_dir("defaults", None, config)
    out = []
    for f in sorted(root.glob("*/*")):
        rel = f.relative_to(root).as_posix()
        if not f.is_file() or not is_checked(rel):
            continue
        text = f.read_text(encoding="utf-8", errors="ignore")
        out.extend((var, rel) for var in var_refs.top_level_keys(text))
    return out


def _uncertain_remediation(unresolved) -> str:
    calls = ", ".join(sorted(set(unresolved)))
    return (
        "leave these in place for now: the merge_variables call in "
        f"{calls} has a pattern this check cannot read, so it may read any "
        "of them. Make that pattern a string literal, then run the check again."
    )


def run(config, allowlist, verbose: bool = False) -> list:
    del verbose
    corpus = defaults_corpus.scan(config)
    expected_src = f"{len(corpus.labels)} sources: " + ", ".join(corpus.labels)
    drifts = []
    for where in sorted(set(corpus.unresolved)):
        d = DriftEntry(
            plugin=NAME,
            image=where,
            alias=where,
            expected="a merge_variables call with a literal pattern",
            found="a pattern this check cannot read",
            expected_src=expected_src,
            found_src=where,
            summary=_UNRESOLVED_SUMMARY,
            remediation=_UNRESOLVED_REMEDIATION,
            # Actionable on purpose: the variable findings this call makes
            # uncertain are only advisory, and advisory findings exit 0, so an
            # advisory call would switch the check off without anyone noticing.
            severity="actionable",
        )
        drifts.append(allowlist.apply(d))

    for var, path in defined_vars(config):
        if corpus.reads(var):
            continue
        found_src = f"defaults/{path}"
        common = {
            "plugin": NAME,
            "image": var,
            "alias": var,
            "expected_src": expected_src,
            "found_src": found_src,
        }
        if corpus.unresolved:
            d = DriftEntry(
                expected=f"a read of {var} in the corpus",
                found=f"set in {found_src}; no read found, but the scan is incomplete",
                summary=_UNCERTAIN_SUMMARY,
                remediation=_uncertain_remediation(corpus.unresolved),
                severity="advisory",
                **common,
            )
        else:
            miss = corpus.near_miss(var)
            if miss is not None:
                fixed, pattern, where = miss
                d = DriftEntry(
                    expected=f"{fixed} (matches {pattern} in {where})",
                    found=f"set in {found_src}; matches no merge_variables pattern",
                    summary=_NEAR_MISS_SUMMARY,
                    remediation=(
                        f"rename {var} to {fixed}: {where} collects variables "
                        f"matching {pattern} with community.general.merge_variables, "
                        f"and {var} does not match it."
                    ),
                    **common,
                )
            else:
                d = DriftEntry(
                    expected=f"a read of {var} in the corpus",
                    found=f"set in {found_src}; nothing in the corpus reads it",
                    summary=SUMMARY,
                    remediation=REMEDIATION,
                    **common,
                )
        drifts.append(allowlist.apply(d))
    return drifts
