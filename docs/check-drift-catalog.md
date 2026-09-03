# check-drift catalog group

A detector for drift between python-osism's two static role catalogs
(`MAP_ROLE2ROLE`, `VALIDATE_PLAYBOOKS`) and the playbook interface each OSISM
runtime image actually ships. Both catalogs are release-agnostic Python
literals; the playbook set behind them is built per OpenStack release, from
four separate build pipelines, none of which python-osism consults at catalog-
authoring time. A catalog entry can therefore name a role that resolved when
it was written and silently stopped resolving at some later release, or never
resolved at all — this group exists to catch that *before* an operator hits it
at `osism apply`/`osism validate` time.

Each check is a plugin; the shared `osism_drift` framework handles config,
local/remote source reads, the allowlist, and output. See
[check-drift-kolla.md](check-drift-kolla.md) for the full framework reference
(input resolution, `--base-dir`, stale allowlist semantics, plugin authoring).
This document covers the catalog-group plugin, the reconstruction it relies
on, and how to debug a finding.

## Run it locally

    python3 src/check-drift.py --group catalog                        # the one enabled plugin
    python3 src/check-drift.py --group catalog --base-dir ~/src/osism --base-dir ~/src/openstack --remote-fallback
    python3 src/check-drift.py --group catalog --format json          # JSONL for tooling

Exit codes: **0** = no actionable drift, **1** = drift found (or a stale
allowlist entry), **2** = input/config error. By default every repo is read
from GitHub; see [Input resolution](check-drift-kolla.md#input-resolution-remote-by-default---base-dir-for-local)
in the kolla doc for the full resolution semantics — the rules are identical.
This group reads nine repos across four runtime-image build pipelines, so a
full remote run is noticeably slower than the kolla/image groups; pass
`--base-dir` (repeatable) with local clones of the OSISM org and
`openstack/kolla-ansible`, plus `--remote-fallback` for whatever a base-dir
doesn't resolve (`ceph/ceph-ansible` upstream, typically), to make it fast.

## The two catalogs, and why they need two resolvers

python-osism ships two independent catalogs in `osism/data/enums.py`, and they
are consulted by two different commands in two different ways:

- **`MAP_ROLE2ROLE`** — collection name → nested `Role(...)` trees. Consulted
  by `osism apply <role>`, which looks the role up in
  `MAP_ROLE2ENVIRONMENT` — the **merged** map python-osism builds at runtime
  by unioning every runtime image's own `/interface/playbooks/*.yml`. A role
  resolves if *any* runtime image advertises it.
- **`VALIDATE_PLAYBOOKS`** — validator key → `{runtime, environment,
  playbook}`. Consulted by `osism validate <key>`, which reads the entry's own
  `runtime` field and dispatches straight to that one runtime's playbook
  lookup — it **never consults the merged map**. A validator resolves only if
  its *own declared* runtime ships the named playbook.

Because the two commands resolve differently, the plugin carries two
resolvers that mirror them exactly (`playbooks.resolves` for `MAP_ROLE2ROLE`,
mirroring `apply.py`; `playbooks.validate_resolves` for `VALIDATE_PLAYBOOKS`,
mirroring `validate.py:87-105`) rather than one generic lookup pretending the
commands agree.

## Reconstructing the runtime interface

No repository holds `MAP_ROLE2ENVIRONMENT` — it only exists at runtime, built
from `/interface/playbooks/*.yml` files that each of the four runtime images
(`kolla-ansible`, `osism-ansible`, `ceph-ansible`, `osism-kubernetes`) writes
during its own container build. `src/osism_drift/playbooks.py` rebuilds each
image's playbook set from the same inputs its `Containerfile` consumes, in the
same order, including the build's own quirks and bugs (see "Known mirrored
upstream bugs" below) — a "corrected" reconstruction would disagree with the
image that actually ships, which defeats the point.

### kolla-ansible: four sources, in build order

The kolla-ansible image's `/ansible/kolla-*.yml` set is assembled from four
sources in `Containerfile` order, and two of the four steps a naive read would
credit to "the interface" are actually **post-copy fixups** that add or remove
names from what the first three steps produced:

| # | Source | Containerfile | What it contributes |
|---|--------|----------------|----------------------|
| 1 | `container-image-kolla-ansible` `files/playbooks/*.yml` | :23 | OSISM's own playbooks, shared across every release |
| 2 | `openstack/kolla-ansible` `ansible/site.yml`, split by role | :164 (`split-kolla-ansible-site.py`) | one `kolla-<role>.yml` per surviving `Apply role <X>` play, minus `UNSUPPORTED_ROLES`, with `rabbitmq(outward)` renamed to `rabbitmq-outward` |
| 3 | `openstack/kolla-ansible` `ansible/*.yml` (every top-level file) | :171-172 | one `kolla-<name>.yml` per upstream top-level playbook |
| 4 | `container-image-kolla-ansible` `files/playbooks/<release>/*.yml` | :224 | OSISM's per-release overrides (a release with no override dir is a legitimate absence, not an error) |
| fixup | — | :174-176 | **not a source** — mutates the set steps 1-3 have already produced |

The `:174-176` fixup itself is two independent rules:

- **conditional aliasing** (`:174-175`) — if the build has produced
  `kolla-mariadb_backup.yml` and/or `kolla-mariadb_recovery.yml` (underscore,
  from step 2/3), it adds the hyphenated aliases
  `kolla-mariadb-backup.yml` / `kolla-mariadb-recovery.yml` alongside them.
  Both names co-exist; nothing is removed here.
- **`rm -f` of two names** (`:176`) — `kolla-kolla-host.yml` and
  `kolla-post-deploy.yml` collide with names kolla-ansible's own `kolla-`
  prefix convention already uses, so the build removes those two copies
  outright, regardless of which of steps 1-4 produced them.

### The other three runtimes

- **osism-ansible** — `container-image-osism-ansible` `files/playbooks/*.yml`
  (own top-level playbooks) plus `ansible-playbooks` `playbooks/<env>/*.yml`
  (at the `playbooks_version` pin from `release/latest/base.yml`), symlinked
  as `<env>-<name>.yml` for every `ENVIRONMENTS` entry not in `SKIP`.
- **ceph-ansible** — `container-image-ceph-ansible` `files/playbooks/*.yml`
  (flavour-independent) and `files/playbooks/<flavour>/*.yml` (per flavour),
  plus `ceph/ceph-ansible` `infrastructure-playbooks/*.yml` at each flavour's
  own `ceph_ansible_version` pin (`release/latest/ceph-<flavour>.yml`) —
  unioned across every flavour OSISM builds today (quincy, reef, squid). Two
  build-order fixups apply on top: `ceph-upgrade.yml` is added unconditionally
  (a `ln -s` regardless of source), and every `ceph-purge-*.yml` is wiped and
  replaced by exactly `ceph-purge-storage-node.yml` +
  `ceph-purge-cluster.yml`.
- **osism-kubernetes** — `osism-kubernetes` `playbooks/*.yml`, copied straight
  in with no separate `ansible-playbooks` checkout and no release/flavour ref
  to resolve.

### `osism validate` per-runtime file lookup

`VALIDATE_PLAYBOOKS` entries name one of three runtimes; `validate_resolves`
dispatches on it directly against that runtime's own reconstructed file set
(`playbooks.playbook_files`), never against the merged map above:

| `runtime` | File probed |
|-----------|--------------|
| `kolla-ansible` | `kolla-<playbook>.yml` |
| `ceph-ansible` | `ceph-<playbook>.yml` |
| `osism-ansible` | `<environment>-<playbook>.yml` (a missing `environment` is a malformed catalog entry and raises, rather than silently probing a `None-<playbook>.yml` file) |

`osism-kubernetes` never appears as a `VALIDATE_PLAYBOOKS` runtime today; a
future entry naming it would raise `SourceError` (`validate_resolves` only
recognizes the three above), which is the correct fail-loud behavior for a
runtime the catalog format doesn't currently model.

## Plugin: catalog_role_missing

**Rule:** every role name in `MAP_ROLE2ROLE` must resolve via
`playbooks.resolves` (mirroring `osism apply`) at **every** supported
OpenStack release, and every `VALIDATE_PLAYBOOKS` entry must resolve via
`playbooks.validate_resolves` (mirroring `osism validate`) at every supported
release. An entry that fails at some but not all releases is flagged with both
the failing and the resolving release lists (the collection is not portable
across the supported range); an entry that fails at every release is flagged
as dead outright.

    python3 src/check-drift.py --group catalog --plugin catalog_role_missing

- **Reads:** `python-osism` `osism/data/enums.py` (`MAP_ROLE2ROLE`,
  `VALIDATE_PLAYBOOKS`, extracted by AST — the module is never imported);
  the four-source kolla-ansible reconstruction and the three other runtimes'
  playbook sets described above, at every supported release;
  `release/latest/openstack-*.yml` (the supported-release range) and
  `release/latest/{base,ceph-<flavour>}.yml` (the `*_version` pins the
  reconstruction resolves refs from).
- **What a finding means:** the `image` field is the effective role/playbook
  name that was checked; `alias` is the `MAP_ROLE2ROLE` collection name or the
  `VALIDATE_PLAYBOOKS` key the entry came from. `found` and the report's
  summary line both name the exact releases the role is unresolvable at (and,
  if any, the releases it still resolves at) — that differential is the whole
  point of the check, so read it before the fix, not just the count.
- **Fix:** for a `MAP_ROLE2ROLE` finding — drop the role from the collection,
  replace it with the name upstream now uses, or gate the collection on the
  release. For a `VALIDATE_PLAYBOOKS` finding — correct or drop the entry
  (point `runtime`/`environment`/`playbook` at a file the runtime actually
  ships, or remove it if the validator no longer applies); "drop the role from
  the collection" does not apply here, since `osism validate` never consults
  `MAP_ROLE2ROLE` membership at all.

### How to allowlist a finding

An allowlist entry needs `plugin: catalog_role_missing`, `image` (the role or
playbook name), and a `reason`. Whether you also set `alias` decides the
finding's scope:

    # silences "redis" everywhere it is flagged -- every MAP_ROLE2ROLE
    # collection and every VALIDATE_PLAYBOOKS entry naming it
    - {plugin: catalog_role_missing, image: redis, reason: "..."}

    # silences "redis" only in the "nutshell" collection; it still flags in
    # every other collection/validator that names it
    - {plugin: catalog_role_missing, image: redis, alias: nutshell, reason: "..."}

Since one `image` name can appear in several collections and/or as a
validator key, an `image`-only entry is the broad suppression and `alias`
narrows it to one collection/validator — pick `alias` unless the role is
genuinely dead (or intentionally release-gated) in every catalog entry that
names it.

### Debugging a false positive: which source to check first

If the plugin flags a role you believe *does* resolve, check the sources in
this order — it is the order most false positives have actually come from
during this plugin's development, because a naive reconstruction misses a
source silently (the aggregate count still looks right; only the contents are
wrong):

1. **Is the release range itself right?** `enablement.release_range` derives
   supported releases from `release/latest/openstack-*.yml` — a stale or
   locally-modified `release` checkout changes what "every supported release"
   means.
2. **kolla-ansible's four sources and the `:174-176` fixup** — the most common
   miss historically. Check the role is actually present after *all four*
   steps and *both* fixup rules, not just the one you'd expect it to come
   from — a role landing via step 2 (site.yml split) is easy to overlook if
   you're only checking step 3 (top-level files).
3. **The per-runtime `_pin()` refs** — `playbooks_version` (base.yml) for
   osism-ansible, `ceph_ansible_version` per flavour for ceph-ansible. A role
   resolving upstream at HEAD but not at the pinned ref (or vice versa) means
   the pin, not the role, is the actual mismatch.
4. **`osism_interface`/`ceph_interface`'s HIDE/KEEP_PREFIX transforms** — a
   file can exist in the playbook set (`*_files()`) and still not resolve as a
   *role name*, if the runtime's own `render-playbooks.py` hides it or renames
   it under `KEEP_PREFIX`. `resolves()` checks the **interface** (post-
   transform), not the raw file set.
5. **The catalog extraction itself** (`catalog.py`) — least likely, since it
   is a direct AST read of `enums.py`, but a role reachable only through a
   dependency shape `_role_names` doesn't expect would be a bug here.

## Known mirrored upstream bugs

Two real upstream build quirks are deliberately reproduced rather than
corrected, because the reconstruction's job is to match what the image
*actually ships*, bugs included — "fixing" either of these here would make
`playbooks.py` disagree with the real build:

- **`container-image-osism-ansible/files/src/generate-playbook-symlinks.py`**
  carries a `not name.startswith('_')` guard that tests the *env-prefixed*
  name (e.g. `generic-_gather-facts-limit.yml`), which can never start with
  `_` — the guard is dead code and filters nothing. `playbooks.osism_files`
  does not apply it either, so an underscore-prefixed source file is
  symlinked and shows up in the interface like any other file, exactly as it
  does in the real image.
- **`container-image-ceph-ansible/Containerfile`** copies upstream's
  `site.yml` to `/ansible/ceph-site.ym` — missing the trailing `l` — and
  copies `dashboard.yml` in unprefixed. Neither name ever matches
  `render-playbooks.py`'s `ceph-*.yml` glob, so neither ever reaches the
  interface. `playbooks.ceph_files` reproduces this by construction — it only
  ever derives `ceph-`-prefixed names from `infrastructure-playbooks/*.yml`
  and the OSISM flavour dirs, so `ceph-site.yml` and `dashboard.yml` never
  appear, the same as in the real image.

## Allowlist and release-model background

See [check-drift-kolla.md](check-drift-kolla.md#allowlist) for the general
allowlist mechanics (stale-entry detection, `match: prefix`) and
[Release model and why the range-aware checks use unions](check-drift-kolla.md#release-model-and-why-the-range-aware-checks-use-unions)
for how the supported-release range is derived and why range-aware checks
compare across it — this plugin is range-aware in both catalogs, and its
"resolves at some releases, not others" finding shape is exactly that model
made visible per role.
