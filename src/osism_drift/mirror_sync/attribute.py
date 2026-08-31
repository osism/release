"""Upstream commit attribution for a changed key.

The report's most valuable column. On the 2026.1 re-sync, five of the fourteen
keys flagged "genuinely semantic" traced to one
`ansible-lint: fix yaml[line-length]` commit, and reading that subject line is
what made them cheap to dismiss. The classifier cannot decide those keys -- see
classify.py -- so putting the commit in front of the reviewer is sync-mirror's actual
contribution, not the classification.
"""

import re
import subprocess

from osism_drift import source

_UPSTREAM = "kolla_ansible"
_GV = "ansible/group_vars/all"


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, check=False
    )


def commits_for(config, key, homes, lo, hi, cap=5):
    """((\"<abbrev> <subject>\", ...), note) for commits touching `key`.

    One `git log` over every home the key has had, passed as multiple pathspecs.
    Keys move between upstream files, so searching only the post-sync home would
    miss the commit that made the change -- but running one log per home and
    concatenating gets the cap wrong: the results are ordered per home, so with
    two homes and cap=2 the two newest commits of the *first* home win over a
    newer commit in the second, while the note claims to show the newest two. A
    single invocation gives git one globally ordered history and deduplicates a
    commit touching both files for free. A home that never existed contributes
    nothing and does not affect the exit status.

    The key is regex-escaped: `git log -G` takes a pattern, so a key carrying
    regex metacharacters would match unrelated diffs.

    The range is bounded. Unbounded history reports commits from before the branch
    point that never affected this transition -- including the commit that first
    introduced the key, which is never the answer to "what changed it".

    `note` is non-empty for every outcome that is not a plain successful list, so
    the report can never render an empty result that reads like "nothing changed
    this" when the truth is "this could not be computed".
    """
    d = source.local_checkout(_UPSTREAM, config)
    if d is None:
        return (), "attribution needs a local checkout of kolla-ansible; skipped"
    if not lo or not hi:
        return (), "attribution range could not be established; skipped"
    paths = [f"{_GV}/{h}" for h in homes if h]
    if not paths:
        return (), "no upstream path known for this key; attribution skipped"

    r = _git(
        d,
        "log",
        "--no-merges",
        "--format=%h %s",
        "-G",
        re.escape(key),
        f"{lo}..{hi}",
        "--",
        *paths,
    )
    if r.returncode != 0:
        detail = r.stderr.decode().strip().splitlines()
        return (), "attribution failed: " + (
            detail[0] if detail else f"git log exited {r.returncode}"
        )

    out = [line for line in r.stdout.decode().splitlines() if line]
    if len(out) > cap:
        return tuple(out[:cap]), f"truncated to the newest {cap} commits"
    return tuple(out), ""
