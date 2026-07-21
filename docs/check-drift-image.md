# check-drift image group

A detector for accidental drift in OSISM container image version pins across
the repos that carry those pins. It compares three locations that should
agree on the same version and flags divergence *when it is introduced* —
before it surfaces at deploy or gate time.

Each check is a plugin; the shared `osism_drift` framework handles config,
local/remote source reads, the allowlist, and output. See
[check-drift-kolla.md](check-drift-kolla.md) for the full framework
reference (input resolution, `--base-dir`, stale allowlist semantics, plugin
authoring). This document covers the image-group plugins and config.

## Run it locally

    python3 src/check-drift.py --group image                        # all enabled plugins
    python3 src/check-drift.py --group image --plugin role_shadows
    python3 src/check-drift.py --group image --base-dir ~/src/osism # read local checkouts
    python3 src/check-drift.py --group image --format json          # JSONL for tooling

Exit codes: **0** = no actionable drift, **1** = drift found (or a stale
allowlist entry), **2** = input/config error. By default every repo is read
from GitHub; see [Input resolution](check-drift-kolla.md#input-resolution-remote-by-default---base-dir-for-local)
in the kolla doc for the full resolution semantics — the rules are identical.

## What it checks

The same image tag should be consistent across these locations:

- `osism/release/<version>/base.yml` — the canonical pinned version under
  `docker_images:`.
- `osism/testbed/environments/manager/images.yml` — the rendered values
  fed to Ansible for the manager environment.
- `osism/ansible-collection-services/roles/<role>/defaults/main.yml` —
  role defaults that occasionally shadow a tag with a hard-coded pin.
- `osism/container-image-osism-ansible/files/src/templates/images.yml.j2`
  — the override template rendered into `group_vars/all/images.yml`, which
  carries higher Ansible precedence than role defaults. An alias emitted here
  is overridden at deploy time by the release pin, making its role default
  dormant (low priority). An alias absent from this template is live — the
  role default is what actually deploys, so the fix is to add the alias here
  (role defaults must never govern the deployed version).

Because these locations evolve at different cadences, they can — and do —
disagree silently. The detector compares them and reports each disagreement
in grouped, narrated blocks with a non-zero exit code.

## Plugins

### `release_vs_manager`

Compares `release/<version>/base.yml` against the rendered
`testbed/environments/manager/images.yml`. Reports any image whose release
tag does not match the deployed value.

Resolution rules for rendered values:

- A plain string (`"5.4.2"`) is compared against the release pin directly.
- A value matching `{{ <name>_version|default('<X>') }}` (the
  rolling-release pattern emitted by the manager render template for
  development-stream images) is treated as the concrete value `<X>`. These
  entries are ordinarily allowlisted; `--no-allowlist` surfaces them.
- Any other Jinja expression indicates an unresolved render. The plugin
  skips it; `--verbose` prints a warning to stderr.

Alias resolution: the plugin reads the generics manager template
(`environments/manager/images.yml`) for lines of the form
`<alias>_tag: "{{ versions['<key>'] }}"` to build the alias-to-release-key
map. An identity entry (`adminer_tag: "{{ versions['adminer'] }}"`) is
handled naturally.

### `role_shadows`

Compares `release/<version>/base.yml` against every
`ansible-collection-services/roles/<role>/defaults/main.yml`. Reports any
`<alias>_tag: <value>` whose value is a concrete string and disagrees with
the corresponding release pin.

Roles whose default file is a Jinja fail-loud expression
(`{{ lookup('vars', '<alias>_tag', default=Undefined) }}`) are not flagged
— those are the deliberate "no in-role default; require an override"
pattern.

**Inputs**: release `base.yml`, generics manager template (alias map),
role `defaults/main.yml` files, and
`container-image-osism-ansible/files/src/templates/images.yml.j2` (the
override template that determines each finding's advice class).

#### Advice classes

Each finding is classified by override precedence:

- **`dormant`** — the alias is emitted by the override template, so the
  release pin wins at deploy via `group_vars/all/images.yml`.
  *Advice: low priority; sync when convenient.*

- **`live`** — the alias is not overridden. The stale role default is what
  actually reaches deployment.
  *Advice: add `<alias>_tag`/`<alias>_image` to the manager render template
  (`images.yml.j2`) so the `latest/base.yml` pin governs the deployed version.
  Role defaults must never govern it, so bumping the role default is the wrong
  fix — it just re-drifts.*

Stream-resolved aliases (see below) are skipped entirely and never reach
this classification.

#### Per-finding output format

Each advice class renders as a separate block. Within a block, entries
appear one per line, sorted by role path then alias:

    role_shadows — 1 LIVE — no images.yml override; the role default is
    what actually deploys:

        dnsmasq_tag (2.90 → 2.91)   roles/dnsmasq/defaults/main.yml

      Fix: add `<alias>_tag`/`<alias>_image` to the manager render template
           (images.yml.j2) so the latest/base.yml pin governs the deployed
           version.
      Refs: release/latest/base.yml

    role_shadows — 3 DORMANT — overridden by the rendered images.yml; the
    release pin wins at deploy:

        adminer_tag (4.7 → 5.4.2)   roles/adminer/defaults/main.yml
        ara_server_mariadb_tag (11.8.3 → 11.8.4)   roles/manager/defaults/main.yml
        manager_redis_tag (7.4.6-alpine → 7.5.0)   roles/manager/defaults/main.yml

      Fix: lower priority; sync when convenient.
      Refs: release/latest/base.yml

### `role_unpinned`

Reports role-default `<alias>_tag` pins whose alias-resolved release key is
**absent from `release/base.yml`** — pinned only in the role, with no
canonical release pin to compare against.

This plugin partitions the concrete role-default `*_tag` pins with
`role_shadows` by the same alias resolution:

- Resolved key **in** `base.yml` → `role_shadows` (drift if values differ).
- Resolved key **not in** `base.yml` → `role_unpinned` (this plugin).

**`image = release_key`, not the alias.** Alias resolution uses the same
alias map as `role_shadows` (from the generics manager template). An alias
like `osism_frontend` that maps to `osism` (which *is* in `base.yml`) is
correctly kept out of this plugin. An alias like `widget` that maps to
`gadget` (absent from `base.yml`) is reported with `image=gadget`.

**Inputs**: release `base.yml` and generics manager template (alias map), plus
role `defaults/main.yml` files. The override template is not needed — the
finding has no comparison value, only a "not present in release" verdict.

**Scope**: `*_tag` pins only. `*_version` pins are deferred.

#### Output format

Entries render one per line with the value-only form (no arrow), sorted by
role path then alias:

    role_unpinned — 2 <alias>_tag pins in role defaults with no release
    base.yml pin:

        ciinternal_tag (1.0, no release pin)   roles/ciinternal/defaults/main.yml
        widget_tag (2.0, no release pin)   roles/widget/defaults/main.yml

      Fix: add a pin to release base.yml (and wire <alias>_tag into the
           manager render template) to make it release-managed, or
           allowlist it if the image is intentionally role-managed.
      Refs: release/latest/base.yml

### `rolling_pin`

Flags `docker_images` pins in `release/<version>/base.yml` whose **value** is a
rolling (mutable) tag — `latest`, `main`, `master`, `stable`, `edge`,
`nightly`, `rolling`, `dev`, `devel`, `develop`, `head`, `current` — matched
case-insensitively. A rolling tag in the release pin deploys a non-reproducible
image: two deploys of the same release can pull different bytes.

The match is a **curated denylist**, not a "not valid semver" heuristic, so an
odd-but-immutable tag like `6.1-23.10_beta` is never mistaken for rolling.

Unlike the other image plugins this reads a single file — the release
`base.yml` — and does not consult the manager template or role defaults.

**Inputs**: release `<release_version>/base.yml` only.

**Fix**: replace the rolling tag with a concrete, immutable version in
`base.yml` (and wire `<alias>_tag` into the manager render template if the image
deploys via a role default), or allowlist the entry if the image is rolling by
design — e.g. a kolla-built test image (`tempest`) or a mirror-only image
(`sonic_vs`). A rolling tag on a *deployed* service is a real finding to fix,
not to allowlist (cf. `substation`, osism/issues#1404).

### `image_orphan`

Reports image aliases **emitted** by the generics manager render template
(`environments/manager/images.yml`, via `<alias>_tag:` lines) that are
**not consumed** by any role or manager playbook — meaning no file under
`ansible-collection-services/roles/` **or** `ansible-playbooks-manager/playbooks/`
contains a `{{ <alias>_image }}` Jinja reference for that alias.

This is the inverse of `role_unpinned`:

- `role_unpinned` — a role pins an image (`<alias>_tag`) with no canonical
  release pin in `base.yml`.
- `image_orphan` — the manager render template defines an image version
  (`<alias>_tag`) that nothing actually deploys (no `{{ <alias>_image }}`
  consumer in any role or manager playbook).

#### Consumer scan

For each emitted alias the plugin checks whether any consumer file contains
`{{ <alias>_image }}`. It scans two sources — the
`ansible-collection-services/roles/` tree (where the container **services**
live) and `ansible-playbooks-manager/playbooks/` (the manager **orchestration**
playbooks, which also reference manager-plane image vars). `ansible-collection-commons`
is host/OS setup with no container images, so it is intentionally not scanned.

Each source is read via
`source.list_tree(repo, root, config)`, which recursively enumerates every
file in a single call — a working-tree walk for a local checkout (these
consumer repos are unpinned), one `git/trees?recursive=1` GitHub API call for
remote. Only `.yml`, `.yaml`, and `.j2` files are then read: a
`{{ <alias>_image }}` reference lives only in ansible YAML or a jinja template,
so every other file is skipped without a read — which in remote mode avoids one
HTTP request each. The files that are read are decoded with `errors="ignore"`
so a non-UTF-8 blob is skipped without error.

**`--base-dir` vs remote**: with a local checkout enumeration and reads are
cheap (a filesystem walk plus local file reads). A remote read fetches the tree
index first and then issues one HTTP request per scanned `.yml`/`.yaml`/`.j2`
file, which is significantly slower for a large collection. Pass `--base-dir`
with the local checkout when iterating on this check.

**A missing consumer root is a hard error**: an absent
`ansible-collection-services/roles/` or `ansible-playbooks-manager/playbooks/`
is treated as an input error (`missing_ok=False`) rather than an empty consumer
set. Silently treating every emitted alias as an orphan when a checkout is
incomplete would be misleading.

No stream-resolved skip is applied: aliases like `osism_netbox` are
themselves stream-resolved yet can still be genuine orphans (nothing
deploys them), so skipping on that criterion would mask the signal.

**Inputs**: generics manager template, and the `.yml`/`.yaml`/`.j2` files under
ansible-collection-services/roles/ and ansible-playbooks-manager/playbooks/.

### Stream-resolved tags

Some `<alias>_tag` lines in the generics manager template resolve at deploy
to a release-stream variable rather than a `docker_images` pin. For
example:

    ceph_ansible_tag: "{{ '{{' }} ceph_version|default(...) {{ '}}' }}"

These aliases (`ceph_ansible`, `kolla_ansible`, and the osism family:
`inventory_reconciler`, `osism`, `osism_ansible`, `osism_frontend`,
`osism_kubernetes`, `osism_netbox`) get their version from a ceph/openstack/osism
release stream, not from `docker_images`. Comparing them against `docker_images`
is meaningless, so **both `role_shadows` and `role_unpinned` skip them**.

The full set is derived automatically by `extract_stream_resolved()` in
`src/osism_drift/manager_template.py`, which matches lines of the form
`<alias>_tag: "{{ '{{' }} <name>_version|default`.

## Input resolution and `--base-dir`

Repos are read from GitHub by default. Pass `--base-dir DIR` (repeatable)
to read from local checkouts — the tool discovers each repo by its
hyphenated directory name (`ansible-collection-services`, `generics`, etc.)
under each base dir.

A repo not found under any `--base-dir` is a **hard error** (all missing
repos are listed at once). Pass `--remote-fallback` to fetch not-found repos
remotely instead. To fetch everything from GitHub, just omit `--base-dir`.

## Output

Text (default) — grouped, narrated blocks, one per (plugin, expected\_src,
found\_src) combination:

```
Checks follow an image's version pin: release base.yml → rendered manager images.yml → role defaults.

release_vs_manager — 3 image tags in the rendered manager images.yml disagree with the release base.yml pins:

    ara_server, manager_redis, netbox_redis

  Fix: re-render environments/manager/images.yml from the current release, or
       allowlist the entry if the divergence is intentional.
  Refs: release/latest/base.yml
        testbed/environments/manager/images.yml

Summary: 3 to act on, 1 allowlisted, 0 stale allowlist entries (4 total)
```

JSONL (`--format json`): one `DriftEntry` per line with keys `plugin`,
`image`, `alias`, `expected`, `found`, `expected_src`, `found_src`,
`allowlisted`, `reason`. Allowlisted entries appear only when `--verbose`
is also set.

Exit codes:

| Code | Meaning |
|------|---------|
| 0    | no actionable drift and no stale allowlist entries |
| 1    | actionable drift or stale allowlist entries found |
| 2    | input error (missing file, unparseable, bad config) |

## Allowlist

`src/drift-allowlist.yml` holds structurally-intentional exceptions.
Each entry needs `plugin`, `image`, and a non-empty `reason`; optional
`alias` and `found_src` narrow the match.

Four `release_vs_manager` rolling-release entries are shipped:
`inventory_reconciler`, `osism`, `osism_ansible`, `osism_kubernetes`. The
manager render template emits these as
`{{ <name>_version|default('latest') }}`, which the detector resolves to the
literal value `latest`. The pin disagreement is intentional — these images
track a rolling release, not the versioned pins.

The osism family no longer needs `role_shadows` allowlist entries: both
`role_shadows` and `role_unpinned` now skip stream-resolved aliases
automatically (see [Stream-resolved tags](#stream-resolved-tags) above).

**Stale entries are a hard error.** An entry that matches no real drift is
reported and makes the run exit non-zero. Remove it once its drift is fixed.

## Configuration

`src/drift-config.yml` — key fields:

- `remote.branch` (default `main`): branch to read from GitHub.
- `release_version` (default `latest`): which release snapshot to compare.
- `plugins.<name>.enabled`: turn plugins on or off.

The shared config includes kolla-only `sources:` pins. Image plugins ignore
those entries; their repos are unpinned consumer reads at `main`, and local
checkouts are discovered by directory name under `--base-dir`.

## Adding a plugin

A plugin is a module under `src/osism_drift/drift/` exposing:

    NAME: str
    DESCRIPTION: str
    INPUT_FILES: list[tuple[str, str]]    # (repo_key, rel_path) — for --help
    SUMMARY: str                          # "{n} ..." lead line (must contain {n})
    REMEDIATION: str                      # the Fix: line
    def run(config, allowlist, verbose: bool = False) -> list[DriftEntry]

See the kolla doc's [Adding a plugin](check-drift-kolla.md#adding-a-plugin)
section for the full recipe. For image-group plugins, register the module in
`IMAGE_PLUGINS = [...]` in `src/osism_drift/drift/__init__.py` **and** add a
`plugins.<NAME>: {enabled: true}` stanza to `src/drift-config.yml`.
Mirror the test pattern in `tests/image_drift/`.
