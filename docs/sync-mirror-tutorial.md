# sync-mirror tutorial: a complete re-sync

A walk through one real re-sync of the `osism/defaults` kolla mirror, for
OpenStack **2026.1**: report, decide, apply, commit. Every command and every
block of output below is from an actual run.

For what the tool is and why each rule exists, see
[sync-mirror.md](sync-mirror.md). This page assumes you have not run it before.

**A note on the numbers.** The counts here are from a re-sync measured on
2026-08-27 against `osism/defaults` main. Upstream moves; your run will differ.

## What you need

Local checkouts of `osism/defaults`, `osism/release` (this repo) and
`openstack/kolla-ansible`, arranged so one `--base-dir` per org finds them. The
rest of this page uses two variables for those roots — set them to wherever
yours live:

    export OSISM_BASE=~/src/osism
    export OPENSTACK_BASE=~/src/openstack

    $OSISM_BASE/defaults
    $OSISM_BASE/release
    $OPENSTACK_BASE/kolla-ansible

Fetch kolla-ansible before you start. sync-mirror pins to a commit before
reading anything, so a stale clone gives you a stale — but internally
consistent — result.

## Step 1: work on a branch

sync-mirror computes the re-sync from a ref, never from your working tree.
`--base-ref` names the commit it treats as the "before" state of the mirror, so
anything that ref is missing is reported as part of your re-sync. It defaults to
`origin/main`. Make a branch and point the tool at it:

    cd $OSISM_BASE/defaults
    git switch -c sync-2026.1 origin/main

In this walkthrough nothing is committed to that branch — the re-sync itself is
committed in the worktree at step 6 — so `--base-ref origin/main` would serve
just as well. The branch earns its place when a key needs `--retain`: retention
is a gate you write by hand in `all/099-kolla.yml`, and sync-mirror only sees it
once it is committed to the ref you pass.

If the mirror work you are building on is still on a review branch, base there
instead. Pointing at `origin/main` when the intended base was a branch ahead of
it added 52 spurious "files added" and one spurious semantic key to this
re-sync's report.

## Step 2: create a report

    cd $OSISM_BASE/release
    tox -e sync-mirror -- --target 2026.1 --base-ref sync-2026.1 \
        --defaults-dir $OSISM_BASE/defaults \
        --base-dir $OSISM_BASE --base-dir $OPENSTACK_BASE

That builds a throwaway git worktree of your branch, computes the plan against
it, writes `sync-mirror-2026.1.txt` in the current directory with every changed
value in full, prints a summary, and removes the worktree. Nothing in `defaults`
changes.

The summary opens with what it compared and what it found:

    target        2026.1 @ 6e12d225082ab0ff2273d61febbdd8fb2bfa6e22
    defaults base sync-2026.1 @ faa403c18333650eb6d3b25cb89aa1752ae4fae5

    131 mirror values changed. 15 need a decision from you; the rest are carried through.

      semantic         15  meaning changed and no rule explains it; blocks --apply
      notation          2  identical Jinja once whitespace beside {{ }} / {% %} is collapsed
      representation  101  bool spelling changed, truth value did not ('no' -> False)
      overridden       13  an unconditional OSISM layer already supplies the key

Only `semantic` needs a decision; the other 116 values are reported for
traceability.

It ends with the part you act on — the semantic keys, by name:

    needs a disposition (--accept-upstream / --retain / --retain-unverified):
      cloudkitty_storage_backend
      computes_need_external_bridge
      enable_opensearch
      enable_placement
      haproxy_ssl_settings
      kolla_base_distro_version_default_map
      kolla_same_external_internal_vip
      letsencrypt_managed_certs
      mariadb_default_database_shard_hosts
      om_rabbitmq_qos_prefetch_count
      opensearch_dashboards_port_external
      openstack_auth
      prometheus_alertmanager_public_port
      ssl_intermediate_settings
      ssl_modern_settings

    full values for every key: $OSISM_BASE/release/sync-mirror-2026.1.txt

    exit 2: operator action needed; the plan was not applied

(That path is printed resolved, not as a variable.)

## Step 3: read the semantic changes

That closing list is only the names. Further up, the same report gives each of
those keys its change and the upstream commit behind it — inlining the value
when it is short enough to read and describing its shape when it is not:

    semantic (15) - each needs --accept-upstream / --retain / --retain-unverified
      cloudkitty_storage_backend: 'influxdb' -> 'sqlalchemy'
          upstream: f1e847851 influxdb/telegraf: Drop deployment
      enable_placement: '{{ enable_nova | bool or enable_zun | bool }}' -> '{{ enable_nova | bool }}'
          upstream: 9f1256ba7 Drop zun and kuryr
      om_rabbitmq_qos_prefetch_count: '1' -> '50'
          upstream: 4232989db rabbitmq: Bump default rabbit_qos_prefetch_count from 1 to 50
      letsencrypt_managed_certs: str, 433 chars -> multi-line str, 435 chars (see report)
          upstream: b877cb060 ansible-lint: fix yaml[line-length] in group_vars
      ssl_modern_settings: multi-line str, 394 chars -> multi-line str, 306 chars (see report)
          upstream: b877cb060 ansible-lint: fix yaml[line-length] in group_vars
          upstream: 78f3c2b5d ansible-lint: Remove yaml[truthy] from excludes

The upstream subject is usually enough to decide. `9f1256ba7 Drop zun and kuryr`
tells you `enable_placement` no longer needs to consider zun, because zun is gone.

For the ones described by shape, open the report file named at the end of the
output — `./sync-mirror-2026.1.txt`. It holds every value in full, laid out over
as many lines as the value has:

    -- ssl_modern_settings
       upstream: b877cb060 ansible-lint: fix yaml[line-length] in group_vars
       upstream: 78f3c2b5d ansible-lint: Remove yaml[truthy] from excludes

       mirror (current):
         ssl-default-bind-ciphersuites TLS_AES_128_GCM_SHA256:TLS_AES_256_GCM_SHA384:...
         ssl-default-bind-options prefer-client-ciphers no-sslv3 no-tlsv10 ...

       upstream 2026.1:
         ssl-default-bind-ciphersuites {{ _ssl_modern_ciphersuites | join(':') }}
         ssl-default-bind-options {{ (['prefer-client-ciphers'] + _ssl_modern_options) | join(' ') }}

Despite the `yaml[line-length]` subject, this is **not** a re-wrap: upstream
replaced literal ciphersuite lists with references to new variables. The
upstream subject is not a reliable guide on its own — read the report for
anything you are not sure about.

## Step 4: disposition each key

Three flags, one per key. See [sync-mirror.md](sync-mirror.md#dispositions) for
the full rules.

**Accepting upstream** is the common case — the change is intended and OSISM has
no opinion:

    --accept-upstream enable_placement

**Retaining the OSISM value** requires a `099` release gate, which you write by
hand. `--retain` does not write it; it **verifies** it. Retain something with no
gate and the run refuses:

    $ tox -e sync-mirror -- ... --retain om_rabbitmq_qos_prefetch_count
    om_rabbitmq_qos_prefetch_count: no later layer supplies it, so nothing
    retains the old value for the older releases
    # exit 3

Retention means:

1. add the gate to `all/099-kolla.yml`, listing the older releases with `else`
   the current value. Had OSISM wanted to keep the old prefetch count instead of
   accepting upstream's bump from `'1'` to `'50'`:

       om_rabbitmq_qos_prefetch_count: "{{ '1' if openstack_version in ['2024.1', '2024.2', '2025.1', '2025.2'] else '50' }}"

2. **commit it** on the branch from step 1 — sync-mirror computes the plan
   from a worktree of `--base-ref`, so an uncommitted gate is invisible to it;
3. re-run with `--retain om_rabbitmq_qos_prefetch_count`, and sync-mirror
   confirms the gate is there and binds the right value to the right release:

       dispositioned:
         om_rabbitmq_qos_prefetch_count: verified against the canonical gate in all/099-kolla.yml

Only that one shape can be verified. A compound condition, a `not in`, a nested
ternary or a variable branch is a legitimate gate that the parser cannot read,
and takes `--retain-unverified` instead — see
[Dispositions](sync-mirror.md#dispositions).

A gated key still comes out `semantic` and still needs its flag on every run:
the gate makes the claim checkable, it does not remove the decision.

A `--retain` that merely recorded your intent would let the re-sync report "all
dispositioned" while the old value quietly disappeared.

In this re-sync all fifteen were accepted upstream: the re-wraps and the
ansible-lint truthy sweep are intended, zun and influxdb are gone, and the
rabbitmq prefetch bump is an upstream default OSISM does not override.

## Step 5: apply

Dispositions on their own change nothing: they are validated, and the run still
ends at `exit 1: changes ready to apply; re-run with --apply`. Writing takes
`--apply` as well:

    tox -e sync-mirror -- --target 2026.1 --base-ref sync-2026.1 \
        --defaults-dir $OSISM_BASE/defaults \
        --base-dir $OSISM_BASE --base-dir $OPENSTACK_BASE \
        --accept-upstream cloudkitty_storage_backend \
        --accept-upstream computes_need_external_bridge \
        --accept-upstream enable_opensearch \
        --accept-upstream enable_placement \
        --accept-upstream haproxy_ssl_settings \
        --accept-upstream kolla_base_distro_version_default_map \
        --accept-upstream kolla_same_external_internal_vip \
        --accept-upstream letsencrypt_managed_certs \
        --accept-upstream mariadb_default_database_shard_hosts \
        --accept-upstream om_rabbitmq_qos_prefetch_count \
        --accept-upstream opensearch_dashboards_port_external \
        --accept-upstream openstack_auth \
        --accept-upstream prometheus_alertmanager_public_port \
        --accept-upstream ssl_intermediate_settings \
        --accept-upstream ssl_modern_settings \
        --apply

The report now ends differently:

    dispositioned:
      cloudkitty_storage_backend: upstream value accepted for every supported release
      ...

    exit 0: the plan was applied to the worktree

    applied: 47 written, 4 deleted

    verify with:
    PYTHONPATH=src python3 src/check-drift.py --group all \
        --base-dir $OSISM_BASE/defaults.worktrees/sync-2026.1 \
        --base-dir $OSISM_BASE \
        --base-dir $OPENSTACK_BASE \
        --remote-fallback

(The tool prints those roots fully resolved, not as variables.)

Nothing in your `defaults` checkout changed. `--apply` writes into the throwaway
worktree and, unlike a report run, **keeps** it — that tree is the artifact to
review. Run the printed `check-drift` command to measure it; the paths are
already correct, including the worktree ordering that a hand-written invocation
usually gets wrong.

If a release newer than your target is already declared, that command measures
the *newer* release rather than yours. The report says so and names the config
override when that applies.

## Step 6: commit the re-sync

Add `--commit` to the apply and sync-mirror does this for you: it branches the
detached worktree, stages exactly the paths the plan touched, and commits with
the provenance as the message.

    ... --apply --commit

    committed d6be03956836 on sync-mirror/2026.1 in the worktree
    amend it to say why each semantic value was accepted or retained -- the
    provenance is generated, the reasoning is not

`--commit-branch NAME` picks a different branch. To do it by hand instead — the
worktree is on a **detached HEAD**, so it needs a branch first:

    W=$OSISM_BASE/defaults.worktrees/sync-2026.1/defaults
    git -C $W switch -c sync-2026.1-mirror
    git -C $W add all/
    git -C $W commit

    git -C $W show --stat --format="" HEAD | tail -3
    # all/010-2025.1.yml | 83 +++++++++++++------------
    # all/010-2025.2.yml | 48 ++++++++++++++++++
    # 51 files changed, 459 insertions(+), 303 deletions(-)

sync-mirror never pushes or opens a PR, and commits only when asked. Review the
diff before you go further — that is what the kept worktree is for.

Either way the message carries the provenance: the pin of every supported
release, the `defaults` base it was computed from, and each semantic key under
the disposition you gave it. The pins are also in every generated `010` header,
but nothing in the tree records the dispositions — accepting upstream leaves no
trace, because the value simply ends up matching upstream. What the tool cannot
write is *why*, so amend the commit and say it.

    Generated by sync-mirror for 2026.1.

    Upstream pins (openstack/kolla-ansible ansible/group_vars/all):
      2024.1: 72d6b6ac6ad4dbc2564ccc4472069a65a3e50111
      ...
      2026.1: 6e12d225082ab0ff2273d61febbdd8fb2bfa6e22
    osism/defaults base: 4bdf3bdfa512519fcfa79db33213b2bd173f13f7

    Dispositions:
      accepted upstream (15):
        cloudkitty_storage_backend, computes_need_external_bridge,
        ...

If you need to recompute this re-sync later — to tell a stale branch from a
moved upstream — `--format json` writes the same pins machine-readably and
`--pins-from` feeds them back. It carries the base commit too, so it conflicts
with `--base-ref`; pass one or the other.

When you are done, remove it:

    git -C $OSISM_BASE/defaults worktree remove $W

## Troubleshooting

**`--defaults-dir is required`** (exit 3) — required in both modes; the plan is
computed from a worktree of that repo.

**`<path> already exists; remove it`** (exit 3) — a previous `--apply` left its
worktree behind, by design. Review and remove it, or pass `--worktree-path`.

**`keys [...] given more than one disposition`** (exit 3) — a key takes exactly
one of the three flags.

**`ModuleNotFoundError: No module named 'requests'`** — you ran
`./src/sync-mirror.py` with a `python3` that lacks the dependencies. Use
`tox -e sync-mirror --`, or install them into a virtualenv first:
`uv venv && uv pip install -r requirements.txt`.

**`upstream <target> ships a monolithic group_vars/all.yml; sync-mirror supports
the per-service layout only`** (exit 3) — the target release predates the
per-service split upstream (2025.1 and older).
