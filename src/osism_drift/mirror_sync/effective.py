"""Effective release range and commit pinning for one sync run.

Two silent-wrongness cases are contained here, both found by review rather than
by a failing check:

- dropped_key_release_map() iterates sorted(release_range(config))[:-1], so it
  derives its own target. Passing the ambient config makes --target decorative
  and emits 010 data for the wrong release. Everything target-sensitive gets the
  derived config instead.
- _local_repo_dir() returns the FIRST --base-dir containing the repo directory,
  so a plain --base-dir pointing at the operator's checkout would shadow the
  worktree. The worktree parent is prepended.
"""

import re
from dataclasses import replace
from pathlib import Path

from osism_drift import enablement, source

_UPSTREAM = "kolla_ansible"
_SHA40 = re.compile(r"^[0-9a-f]{40}$")


def effective_range(config, target) -> list:
    """Supported releases up to and including `target`, ascending."""
    supported = sorted(enablement.release_range(config))
    if target not in supported:
        raise ValueError(
            f"{target!r} is not a supported release; have {', '.join(supported)}"
        )
    return supported[: supported.index(target) + 1]


def resolve_tips(config, rng) -> dict:
    """{release: branch-tip commit} for every release in `rng`.

    Resolved BEFORE any user pin is applied. check_pin() needs the real tip as its
    upper bound; an earlier design validated the pin against a map into which the
    pin had already been written, so the bound was pin <= pin -- and
    `git merge-base --is-ancestor X X` exits 0, making the whole check dead code.
    """
    return {
        rel: source.resolve_commit(
            _UPSTREAM, source.release_to_ref(_UPSTREAM, rel, config), config
        )
        for rel in rng
    }


def requested_shas(opts, rng, tips) -> dict:
    """{release: commit} the run will actually read, pin precedence applied.

    Precedence is an error, never a silent override: --pin and --pins-from may not
    both name the target.
    """
    if opts.pins and opts.pin:
        raise ValueError(
            "--pin and --pins-from both supplied; pass one (--pins-from already "
            "carries the target's commit)"
        )
    shas = dict(tips)
    if opts.pins:
        shas.update(opts.pins)
    elif opts.pin:
        shas[opts.target] = opts.pin
    for rel, sha in shas.items():
        if not _SHA40.match(str(sha)):
            raise ValueError(f"pin for release {rel!r} is not a 40-hex commit: {sha!r}")
    return shas


def validate_pins_payload(payload, target, rng) -> tuple:
    """({release: sha}, defaults_base_sha) from a --pins-from report.

    A replay file decides what the acceptance test compares, so every mismatch is
    an error rather than a best-effort merge.
    """
    if payload.get("schema") != 1:
        raise ValueError(f"--pins-from: unsupported schema {payload.get('schema')!r}")
    if payload.get("target_release") != target:
        raise ValueError(
            f"--pins-from was produced for target {payload.get('target_release')!r}, "
            f"not {target!r}"
        )
    shas = payload.get("release_shas") or {}
    if not isinstance(shas, dict):
        raise ValueError("--pins-from: release_shas is not a mapping")
    want, have = set(rng), set(shas)
    if want != have:
        raise ValueError(
            f"--pins-from release set {sorted(have)} does not match the effective "
            f"range {sorted(want)}"
        )
    for rel in sorted(shas):
        if not _SHA40.match(str(shas[rel])):
            raise ValueError(
                f"--pins-from: release {rel!r} sha {shas[rel]!r} is not a 40-hex "
                "commit"
            )
    base = payload.get("defaults_base_sha")
    if not base or not _SHA40.match(str(base)):
        raise ValueError("--pins-from has no usable defaults_base_sha")
    return shas, base


def derive(config, opts, defaults_tree, shas):
    """Effective Config for this run, given already-validated `shas`.

    Pins every release in the effective range to a commit, so no downstream reader
    can pick up a branch tip, and resolves `defaults` to `defaults_tree`.
    """
    rng = effective_range(config, opts.target)

    refs = {k: dict(v) for k, v in (config.release_refs or {}).items()}
    refs.setdefault(_UPSTREAM, {}).update(shas)

    parent = str(Path(defaults_tree).parent)
    base_dirs = (parent,) + tuple(d for d in config.base_dirs if d != parent)

    return replace(
        config,
        releases=tuple(rng),
        release_refs=refs,
        base_dirs=base_dirs,
        # Fresh per-run state. Sharing these would let a memo taken against an
        # unpinned ref satisfy a pinned read.
        ref_cache={},
        groupvars_cache={},
        snapshot_cache={},
    )


def check_pin(config, target, pin, tip, range_base_sha) -> None:
    """Bound the effective target commit to the target-side history.

    Validated whatever its origin -- --pin, --pins-from, or automatic resolution.
    An earlier design returned early unless --pin was given, so a --pins-from
    replay trusted every supplied commit unchecked.

    `tip` must be the real branch tip, resolved before the pin was applied.
    Ancestor-of-tip alone is also too weak: every commit before the release
    branched is an ancestor, including one from an earlier series with a different
    group_vars layout.
    """
    if pin == tip:
        return  # nothing was overridden
    if not source.is_ancestor(_UPSTREAM, pin, tip, config):
        raise source.SourceError(
            f"pin {pin[:12]} is not reachable from the {target} tip {tip[:12]}"
        )
    if range_base_sha is None:
        # Single-release range: no branch point to bound against. The report says
        # which check ran.
        return
    if not source.is_ancestor(_UPSTREAM, range_base_sha, pin, config):
        raise source.SourceError(
            f"pin {pin[:12]} is before the branch point {range_base_sha[:12]}; "
            f"not a snapshot of {target}"
        )
