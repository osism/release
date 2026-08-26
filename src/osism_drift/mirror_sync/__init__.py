"""Mirror sync: compute a Plan for a per-release defaults re-sync.

Detection lives in osism_drift.drift; this package is the generation side. One
pure Plan is computed and then either rendered (report) or written (--apply), so
the two can never describe different trees.
"""

from osism_drift import enablement, source

from . import layers, attribute, classify, effective, render, worktree  # noqa: F401
from .model import Plan, Row, SyncOpts  # noqa: F401

_UPSTREAM = "kolla_ansible"


def build_plan(config, opts, tree, base_sha) -> Plan:
    """The one Plan both the renderer and the writer consume.

    `tree` is the worktree; `base_sha` the commit it was created from. Pins are
    resolved and validated before any content is read, so nothing downstream can
    pick up a branch tip.
    """
    rng = effective.effective_range(config, opts.target)
    prev = rng[-2] if len(rng) > 1 else None

    tips = effective.resolve_tips(config, rng)
    shas = effective.requested_shas(opts, rng, tips)

    range_base = None
    if prev:
        range_base = source.merge_base(_UPSTREAM, tips[prev], tips[opts.target], config)
        if range_base is None:
            # Two stable branches of one repo always share history. No merge base
            # means something is wrong, and check_pin would silently skip its
            # lower bound -- the same class of dead check as comparing a pin
            # against itself.
            raise source.SourceError(
                f"no merge base between {prev} and {opts.target}; cannot bound a pin"
            )
    effective.check_pin(
        config, opts.target, shas[opts.target], tips[opts.target], range_base
    )

    eff = effective.derive(config, opts, tree, shas)

    pre = enablement.osism_mirror_values(eff)
    m_writes, m_deletes, added_files, deleted_files = layers.mirror_layer(
        eff, opts.target, tree
    )
    post = enablement.groupvars_values(list(m_writes.values()))
    # The ownership guard protects writes, so it must not abort a read-only
    # report: blocking there hides the very plan an operator needs in order to
    # see that a marker commit is required.
    c_writes, c_deletes, dropped, unowned = layers.compat_layer(
        eff, rng, shas, tree, strict=opts.apply
    )

    all_writes = dict(m_writes)
    all_writes.update(c_writes)
    changed, created = layers.changed_and_created(all_writes, tree)

    supply = classify.supply_info(eff, tree)
    # Pre-sync homes come from the mirror ON DISK, not from prev_release. Deriving
    # them from prev is right only on a first transition: re-run after an upstream
    # backport on the same target and the on-disk mirror is an earlier commit of
    # that target. If a key moved files in between, attribution would search the
    # wrong old path.
    homes_pre = layers.mirror_homes(tree)
    homes_post = enablement.upstream_groupvars_homes(opts.target, eff)

    rows = []
    for key in sorted(set(pre) & set(post)):
        if pre[key] == post[key]:
            continue
        cls, note = classify.classify(key, pre[key], post[key], supply)
        commits, anote = (), ""
        if cls == "semantic":
            homes = sorted({h for h in (homes_pre.get(key), homes_post.get(key)) if h})
            commits, anote = attribute.commits_for(
                eff, key, homes, range_base, shas[opts.target]
            )
        rows.append(
            Row(
                key,
                pre[key],
                post[key],
                cls,
                commits,
                "; ".join(x for x in (note, anote) if x),
            )
        )

    return Plan(
        target_release=opts.target,
        release_shas=shas,
        defaults_base_sha=base_sha,
        prev_release=prev,
        range_base_sha=range_base,
        mirror_writes=m_writes,
        mirror_deletes=m_deletes,
        compat_writes=c_writes,
        compat_deletes=c_deletes,
        unowned_compat=unowned,
        changed_writes=changed,
        created_paths=created,
        rows=tuple(rows),
        added_keys=tuple(sorted(set(post) - set(pre))),
        dropped_keys=dropped,
        added_files=added_files,
        deleted_files=deleted_files,
        compat_effects=layers.compat_key_effects(c_writes, tree),
    )
