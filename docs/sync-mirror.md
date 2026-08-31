# sync-mirror

Regenerates the kolla mirror layer in [osism/defaults](https://github.com/osism/defaults)
for one OpenStack release.

The job is to bring the mirror up to a newly supported OpenStack release and
carry the keys the older supported releases still need. It used to be roughly
fifteen hand-written commits. sync-mirror computes it as one plan, and stops at
every point where the right answer is a judgement call rather than a
derivation.

For a walk through a complete re-sync, see
[sync-mirror-tutorial.md](sync-mirror-tutorial.md). This page is the reference.

## What the mirror layer is

`osism/defaults` carries upstream kolla-ansible's `ansible/group_vars/all` as a
set of layers under `all/`, loaded in lexical order:

| Layer | Content | Owner |
| ------- | --------- | ------- |
| `001-<service>.yml` | byte-for-byte copy of the upstream file | generated |
| `010-<L>.yml` | keys `L` defines that the target dropped | generated |
| `099-kolla.yml` | OSISM's own opinions, overriding the mirror | hand-written |

**The mirror is the `001-*` layer** — that byte-for-byte copy of upstream's
files, and nothing else. `010-*` carries upstream values too, but re-serialized
and OSISM-owned, so it is back-compat rather than mirror; `099-kolla.yml` is
OSISM's own. The distinction is load-bearing: the `kolla_mirror_verbatim`
check-drift plugin enforces byte-equality on `001-*` alone, which is why
sync-mirror never reformats a value there — it has no freedom to choose a
different spelling (see [check-drift-kolla.md](check-drift-kolla.md)) — while it
owns the YAML style of `010-*` completely.

Where this page says "the mirror" without qualification it means the generated
set, `001-*` and `010-*` together: what a re-sync computes and `--apply` writes.

When the supported release range moves — say the newest supported release becomes
2026.1 — three things must happen: the `001` layer must match the new release's
upstream tree, keys the new release dropped must survive for the older releases
that still use them, and any value whose *meaning* changed must be consciously
accepted or deliberately overridden. sync-mirror does the first two and reports
the third.

## Run it locally

    tox -e sync-mirror -- --target 2026.1 \
        --defaults-dir ~/src/osism/defaults \
        --base-dir ~/src/osism --base-dir ~/src/openstack

`tox -e sync-mirror` is the portable form: it creates the environment from
`requirements.txt`. If you would rather run the script directly — it is
executable, with a `/usr/bin/env python3` shebang — install the dependencies
into a virtualenv first:

    uv venv && uv pip install -r requirements.txt
    . .venv/bin/activate
    ./src/sync-mirror.py --target 2026.1 ...

`pip install -r requirements.txt` in a venv works the same way. Only `requests`
and `PyYAML` are actually needed at runtime; a `python3` that already has them
runs the script as-is.

`--defaults-dir` is required in both modes. `--base-dir` is repeatable and is
searched in order for each repo sync-mirror needs — `defaults` and `release` from
the OSISM root, `kolla-ansible` from the OpenStack root.

### Exit codes

| Code | Meaning |
| ------ | --------- |
| 0 | nothing to do, or (with `--apply`) the plan was applied |
| 1 | changes ready to apply; re-run with `--apply` |
| 2 | operator action needed |
| 3 | refused; the plan was not applied |

Exit 2 has two causes: a semantic key with no disposition, or an existing `010`
file the tool does not own. Exit 0 covers two outcomes, so the report's last line
names which one.

### The commit

`--commit` (with `--apply`) branches the detached worktree, stages exactly the
paths the plan touched, and commits. The branch defaults to
`sync-mirror/<target>`; `--commit-branch NAME` overrides it, and an existing
branch is refused rather than moved. The message is the generated subject, the
provenance below, and a `Signed-off-by` taken from your git config — without
that trailer the eventual PR fails Zuul's DCO gate.

It stops there: no push, no PR, and the *reasoning* for each semantic decision
is yours to add by amending. Without `--commit`, the same provenance is printed
as a block to paste, wrapped at 72 columns.

The pins are also in every generated `010` header, so the tree is not missing
them. The dispositions are the part nothing else records — `--accept-upstream`
leaves no trace at all, since the value simply ends up matching upstream — and
`--format json` carries the same block as `commit_summary`.

### The detail report

The terminal report inlines only short single-line values; every changed value in
full goes to a file, written on every run that gets as far as computing a plan —
report runs and refused applies included, since the values behind a refusal are
what you need to resolve it.

It lands in the current directory as `sync-mirror-<target>.txt` (gitignored in
this repo), and the footer prints its absolute path. `--report-file PATH`
overrides the location.

## It never touches your defaults checkout

Every run builds a throwaway worktree of `--defaults-dir` at
`<defaults>.worktrees/sync-<target>/defaults`, from `--base-ref`, and works
there. Report mode removes it on the way out; `--apply` keeps it as the artifact
to review. Nothing is written into `--defaults-dir` itself, in either mode — the
only file the tool writes outside the worktree is the detail report above.

Two consequences worth knowing:

- **Your uncommitted work is never consulted.** The plan is computed from
  `--base-ref`, not from your working tree.
- **`--base-ref` decides what the re-sync even looks like.** It defaults to
  `origin/main`, and whatever it names is treated as the "before" state of the
  mirror — so anything that ref is missing is reported as part of the re-sync.
  Point it at the commit your re-sync actually starts from, which is not
  `origin/main` if the mirror work it builds on is still on a review branch. The
  header echoes what it used:

      target        2026.1 @ 6e12d225082ab0ff2273d61febbdd8fb2bfa6e22
      defaults base sync-2026.1 @ faa403c18333650eb6d3b25cb89aa1752ae4fae5

  In the 2026.1 re-sync, leaving `--base-ref` at `origin/main` when the intended
  base was a branch ahead of it added 52 spurious "files added" and one spurious
  semantic key to the report.

The worktree is created **detached**. After `--apply` you are standing in a
detached tree holding the changes; `git switch -c <branch>` there, then commit —
or pass `--commit` and let sync-mirror do it.

## What it computes

Every supported release is pinned to a **commit** before anything is read, so a
branch advancing mid-run cannot change the result. `--pin` sets the target's
commit; `--pins-from report.json` replays every pin from a previous run, which is
what makes a re-sync reproducible after upstream has moved on.

- **`001` layer** — byte copy of the target's upstream `group_vars/all`, plus
  file additions and deletions as services appear and disappear.
- **`010-<L>.yml` layers** — a key lands here when the target no longer defines
  it **and** no other OSISM layer supplies it. `L` is the newest *currently
  supported* release that still defines the key, because `010-<L>.yml` is retired
  when `L` reaches EOL. It is **not** "the release where the key was last seen"
  historically.
- **Classification** of every changed value, and the upstream commit behind each
  semantic one.

Because the `010` map is the desired end state rather than a delta, a re-sync
usually routes keys that are already exactly where they belong. The report says
so per file, so a large count does not read as a large change:

    keys routed into back-compat layers (75 in the desired tree, not all of them new):
      all/010-2024.1.yml:  6 keys (same keys and values; only the pin header is refreshed)
      all/010-2025.2.yml: 31 keys (new file)

A `010` file is rewritten every re-sync regardless, because its header carries each
release's pinned commit.

### The first regeneration is a one-time normalization

The `010` values are **re-serialized**, unlike the byte-copied `001` layer, so
the first time the generator rewrites a hand-maintained `010` file the diff is
large and entirely cosmetic:

- the header prose becomes the generator's fixed text, and `# Source:` becomes
  the per-release pinned-commit list;
- YAML style normalizes — flow mappings become block mappings, an empty value
  becomes an explicit `null`, and mixed list quoting becomes uniform. String
  values themselves are untouched: the generator emits the same double-quoted
  style a human wrote.

Because the `010` values are re-serialized, the generator owns their YAML style,
and it deliberately matches the `001` layer rather than PyYAML's defaults:
double-quoted string values, bare keys at every depth, sequences indented under
their key, nested mappings in upstream's order. PyYAML on its own would invert
all four.

Two of those are correctness rather than taste. `osism/defaults` runs yamllint
with `extends: default` (line-length disabled, `truthy` enabled), whose
indentation rule makes an unindented block sequence an **error** — a re-sync would
fail CI on a file the generator wrote. And PyYAML's preferred single quotes turn
a Jinja value containing a quote into `== ''influxdb''`, where upstream writes
`== 'influxdb'`.

Verified by running yamllint with that config over a generated tree: zero
errors. The `001` layer is a byte copy and carries whatever upstream wrote,
including the handful of warnings upstream's own files produce.

Not one key or value changes. Once normalized, the generator is idempotent: a
re-run against its own output reports `exit 0: nothing to do`, and a later
re-sync touches an existing `010` file by **exactly one line** — the new
release's pin added to the header.

That difference is worth keeping out of the re-sync diff. Give the transition
its own commit by running the *current* newest release first:

    tox -e sync-mirror -- --target 2025.2 --base-ref <your branch> ... --apply

Because nothing about the mirror content changes, that run needs no
dispositions and touches no `001` file — it reports `no value differences` and
rewrites only the `010` layers. Commit it, then run the real re-sync on top. That
keeps a wholly cosmetic rewrite of the older layers out of the release diff: in
the 2026.1 re-sync, its own effect on those layers was one added line each.

## The four classes

Only `semantic` needs anything from you. The other three are reported so that a
later surprise is traceable, not because there is a decision to make.

### semantic

The value's meaning changed and no rule explains it. Each key blocks `--apply`
until it carries a disposition. The report names the upstream commit responsible:

    enable_placement: '{{ enable_nova | bool or enable_zun | bool }}' -> '{{ enable_nova | bool }}'
        upstream: 9f1256ba7 Drop zun and kuryr

### notation

Identical Jinja once whitespace adjacent to `{{ }}` and `{% %}` is collapsed —
`{% if a %}` versus `{%if a %}`. This is the one class that is **provably**
inert: the values are normalized and compared, so equality is demonstrated
rather than assumed.

### representation

A bool-ish token whose truth value did not change. Two kinds occur:

- a **type** change — `'no'` (string) becomes `False` (bool), because upstream
  rewrote the token and YAML now parses it as a boolean;
- a **spelling** change — a different token from the same truth set, since
  `no`/`false`/`off` and `yes`/`true`/`on` are all accepted.

The 2026.1 re-sync was 88 × `'no' -> False` and 13 × `'yes' -> True`, from
`78f3c2b5d ansible-lint: Remove yaml[truthy] from excludes`.

Unlike `notation`, this is **not proven** inert. A consumer that reads the raw
value rather than passing it through `| bool` sees `"no"` versus `"False"`. It is
very likely harmless, and sync-mirror cannot demonstrate that it is — so it says
so instead of claiming otherwise.

**There is nothing to do about it.** The `001` layer is a byte copy, so the tool
cannot rewrite `False` back to `'no'` even if you wanted it to; upstream's
spelling is carried as-is. The class exists so that if a template breaks later,
you can see which keys changed spelling and in which re-sync.

### overridden

An OSISM layer that applies unconditionally already supplies the key, so the
upstream change cannot reach a deployment. Judged by lexical position after the
mirror prefix, not by a hard-coded name; `010-*` is excluded because it is
release-scoped, and a layer whose value branches on the release does not count as
unconditional.

    overridden (13) - carried through, no decision needed
      supplied unconditionally by all/099-kolla.yml (12): ...
      supplied unconditionally by versions.yml (1): openstack_release

## Dispositions

A semantic key takes exactly one of three flags; giving a key two is an error.

- **`--accept-upstream KEY`** — take the new value for every supported release.
  Nothing to check.
- **`--retain KEY`** — keep the OSISM value. The `099` gate is **verified**: it
  must exist, and carry the right value on the right release.
- **`--retain-unverified KEY`** — keep the OSISM value where the gate exists but
  cannot be parsed. Records your acknowledgement and **verifies nothing**.

`--retain` does not write anything. Retention is expressed as a **gate**: a
value in `099-kolla.yml` that branches on the release, so the older releases keep
the old value while the target gets the new one. `--retain` is checkable for one
shape:

    {{ <old> if openstack_version in [<release>, ...] else <new> }}

The parser binds the `in`-branch to the old value and the `else`-branch to the
new one, so a reversed gate — new value for the older releases — is caught rather
than accepted. Other forms are legitimate but not parseable: compound
conditions, `not in`, nested ternaries, variable branches. Those take
`--retain-unverified`.

A gate is not the same as the `overridden` class above. That class is for a key
an OSISM layer supplies *unconditionally*, which needs no decision because the
upstream change cannot reach a deployment. A gate is conditional by definition,
so its key still comes out `semantic` and still needs a disposition.

So `--retain KEY` means *you add the gate, commit it, then re-run*, and
sync-mirror confirms it is really there. The commit is not optional: the plan is
computed from a worktree of `--base-ref`, so an uncommitted gate is invisible:

    $ tox -e sync-mirror -- ... --retain om_rabbitmq_qos_prefetch_count
    om_rabbitmq_qos_prefetch_count: no later layer supplies it, so nothing
    retains the old value for the older releases
    # exit 3

A `--retain` that only recorded intent would let a re-sync report "all
dispositioned" while the old value silently vanished.

Use `--retain-unverified` only after reading the gate yourself. It is for a gate
whose shape the validator cannot parse — not for a gate you have not written.

## The generator marker

Every `010-*.yml` sync-mirror writes carries `# generated-by: sync-mirror` as
line 2 of a fixed header. Before overwriting or deleting an existing `010` file,
the tool checks the first five lines for that marker and refuses without it:

    --apply is blocked until these are resolved:
      all/010-2024.1.yml lacks the generator marker
      (add the '# generated-by: sync-mirror' header line, or let sync-mirror
       create the file)

This is self-sustaining rather than an ongoing tax: files the tool creates are
marked automatically, and a file that does not exist yet is written freely. Only
a **pre-existing, unmarked** file blocks — a layer written by hand, or restored
from a branch that predates the generator.

## Verifying a synced tree

After `--apply`, the report prints a `check-drift` invocation with the paths
filled in. It prepends the worktree's parent to your `--base-dir` list, because
`check-drift` resolves a repo to the first root containing a directory of that
name — with your own checkout first it would measure the unsynced tree and pass
on nothing.

**One caveat.** `check-drift` derives its release range by globbing
`latest/openstack-*.yml` and anchors on the newest, with no flag to narrow it. So
if a release newer than the one you just synced is already declared, that command
measures *the newer release*, and its count says nothing about your re-sync.
The report prints this warning itself when it applies, and names the
`releases:` config override to measure your target instead.

## Deliberately out of scope

- **`099` edits.** Every semantic decision is named by a human; the tool
  verifies, never authors.
- **`002-images-*`.** A separate concern.
- **Pushing, opening a PR.** `--apply` writes a working tree; `--commit`
  commits it in that worktree on request, and stops there.

## Module map

| File | Responsibility |
| ------ | ---------------- |
| `src/sync-mirror.py` | CLI, disposition validation, emit |
| `mirror_sync/__init__.py` | `build_plan()` — ties the pieces into one `Plan` |
| `mirror_sync/model.py` | `Plan`, `Row`, `SyncOpts`; no I/O |
| `mirror_sync/layers.py` | the `001` byte copy and the `010` key emit |
| `mirror_sync/effective.py` | release range, commit pinning, `--pins-from` |
| `mirror_sync/classify.py` | the four classes |
| `mirror_sync/attribute.py` | upstream commit behind a semantic change |
| `mirror_sync/gate.py` | `099` retention-gate validation |
| `mirror_sync/render.py` | terminal report, exit code, JSON payload |
| `mirror_sync/detail.py` | the full per-key report file |
| `mirror_sync/worktree.py` | throwaway worktree lifecycle |
| `mirror_sync/write.py` | applying a `Plan` |
| `mirror_sync/commit.py` | committing an applied `Plan` (`--commit`) |

Tests live in `tests/mirror_sync/`. Run them with `tox -e drift-test`.
