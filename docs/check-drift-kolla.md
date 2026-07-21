# check-drift kolla group

A small detector for accidental drift in the OSISM kolla container-image
toolchain. It compares the OSISM consumer repos (`defaults`, `release`,
`generics`, the container-image repos) against upstream `openstack/kolla` and
`openstack/kolla-ansible`, and flags divergence *when it is introduced* — before
it surfaces at deploy or gate time.

Each check is a plugin; the framework handles config, local/remote source reads,
the allowlist, and output. The checks follow a service's lifecycle —
**enabled → built → version-pinned → deployed** — and the report renders them in
that order.

## Run it locally

    python3 src/check-drift.py --group kolla                        # all enabled plugins
    python3 src/check-drift.py --group kolla --plugin kolla_inventory
    python3 src/check-drift.py --group kolla --base-dir ~/src/osism # read local checkouts
    python3 src/check-drift.py --group kolla --format json          # JSONL for tooling

Exit codes: **0** = no actionable drift, **1** = drift found (or a stale
allowlist entry), **2** = input/config error. By default every repo is read from
GitHub, so the detector runs anywhere including CI; see "Input resolution".

## Input resolution: remote by default, `--base-dir` for local

By default every repo is read from **GitHub** (`remote.branch`, default `main`;
per-repo owner/branch via `sources:`). No local checkout is needed.

To read local checkouts, pass `--base-dir DIR` (repeatable). For each repo a
plugin needs, the dirs are searched **in order** and the first one containing
`<repo-dir>` (the hyphenated repo name, e.g. `container-image-kolla-ansible`)
wins:

    python3 src/check-drift.py --group kolla --base-dir ~/src/osism --base-dir ~/src/openstack

A repo not found under any `--base-dir` is a **hard error** (so a
misconfiguration can't silently compare the wrong thing); all missing repos are
listed at once. Pass `--remote-fallback` to fetch not-found repos from GitHub
instead — this is the CI shape: OSISM repos resolve to the checked-out change
locally, upstream falls back to remote. To fetch everything from GitHub, just
omit `--base-dir` (the default).

**What gets read from where is always logged** (to stderr, unless `--quiet`),
before any comparison:

    Resolving sources (1 base dir(s)):
      defaults                         local  /home/me/src/osism/defaults  [working tree, as-is]
      kolla_ansible                    remote openstack/kolla-ansible @ stable/2025.2 ... [remote]

Local OSISM-consumer repos are read from the **working tree as-is** (the change
under test). Upstream `kolla`/`kolla-ansible` are **pinned** (`sources:`): if a
`--base-dir` holds them as a **git clone**, they are read from git objects at the
pinned/release refs (no checkout, offline, no GitHub rate limit; a missing ref is
a loud error, and a non-`origin` remote such as `gerrit` is searched too). A
pinned repo whose `--base-dir` dir is not a git clone falls back to remote (with
`--remote-fallback`) or is a hard error. With no local clone they are read
remotely at their refs.

### Per-repo source overrides (`sources:`)

By default every repo is read from `default_owner` (osism) at `remote.branch`. A
`sources:` entry overrides the owner and/or ref for one repo:

    sources:
      kolla: {owner: openstack, branch: stable/2025.2}

A repo with a set `branch` is **pinned**: it is always read remotely at that ref
(or from git objects in a local clone), so the result is deterministic regardless
of any local checkout's current branch.

## The checks

A kolla service moves through four **stages** in OSISM, and each check guards a
stage or the transition into it:

1. **enabled** — OSISM turns the service on with an `enable_X` flag in
   `osism/defaults`.
2. **built** — OSISM builds its container image (the service is in the release
   build set).
3. **version-pinned** — the built image's tag is pinned through the kolla-ansible
   versions template and the producer's SBOM map.
4. **deployed** — the service has ansible inventory groups to deploy into.

The plugins run in that order, and the report renders them the same way.
Each opens with the stage it guards. Each is independent and can be run alone
with `--plugin <name>`; every finding can be suppressed with an allowlist entry
(see "Allowlist"). Service names are compared as **key spaces**, normalising
`-`↔`_`; the checks never derive keys from output-variable or image names.

### Plugin: kolla_enablement_orphan

**Enabled — an enable flag must name a service upstream still defines.**
Flags an OSISM enable flag (`osism/defaults` `all/*.yml` `enable_X`) whose service `X` is
**absent from upstream kolla-ansible's enable-defaults at every supported
release** — the service was removed or renamed upstream, so the flag is stale.
Orphan means absent from the **union** across the release range; a service still
defined in any in-range release is not an orphan. Per-release upstream keys come
from the **top-level** enable-defaults only, whose location moves between
releases (the monolithic `ansible/group_vars/all.yml` at 2024.1/2024.2/2025.1, or
the split `ansible/group_vars/all/*.yml` dir at 2025.2+); `roles/*/defaults/`,
tasks, tests, and releasenotes are never read, so a reference-only mention does
not count as a definition.

The plugin ships `SCOPE = "explicit"`: it considers every `enable_X` flag with a
literal value, including dead `enable_X: "no"` cleanup flags for retired services,
which it surfaces so they can be removed. The OSISM-invented `enable_common` and
`enable_kolla_operations` have no upstream counterpart by design and are kept
allowlisted. (Only top-level `enable_<X>` defaults are matched, never
component-prefixed role defaults; if an OSISM flag ever needs the latter the check
fails closed and loud — a false-positive orphan, never a missed one — and the fix
is to extend `upstream_enable_keys` to union role-default keys.)

    python3 src/check-drift.py --group kolla --plugin kolla_enablement_orphan

- **Reads:** `osism/defaults` `all/*.yml`; `openstack/kolla-ansible`
  enable-defaults per resolved release ref.
- **Fix:** remove the stale `enable_<name>` from `osism/defaults`, or migrate it
  to the upstream replacement; if it is an OSISM invention, add an allowlist entry
  with a reason.

### Plugin: kolla_groupvars_missing

**Enabled — a required upstream global var must be mirrored.** The add-direction
mirror of `kolla_enablement_orphan`: flags an upstream kolla-ansible
`group_vars/all` top-level key that OSISM never mirrored, so it is undefined in
the deploy var context and aborts any role task that references it — the class
behind the 2025.2 `keystone_listen_port` failure (added upstream in the uWSGI
migration, never picked up by `osism/defaults`). Compares the **union** of
upstream `group_vars/all` keys across the supported release range against
everything OSISM supplies to the container's `group_vars/all`, from **both**
delivery paths: `osism/defaults` `all/*.yml` (via the generics gilt overlay)
**and** the rendered `container-image-kolla-ansible` `versions.yml`
(`openstack_release`, `openstack_previous_release_name`, the `kolla_*_version`
pins) — a var supplied only by the latter must not false-positive. Union across
releases because one `osism/defaults` snapshot must satisfy every supported
release at once (see "Release model" below); top-level keys only, compared
verbatim (an Ansible var name is an exact identifier).

    python3 src/check-drift.py --group kolla --plugin kolla_groupvars_missing

- **Reads:** `openstack/kolla-ansible` `group_vars/all` per resolved release ref;
  `osism/defaults` `all/*.yml`; `container-image-kolla-ansible`
  `files/src/templates/versions.yml.j2`.
- **Fix:** mirror the missing var into `osism/defaults` `all/*.yml` (copying
  upstream's definition — harmless when the service is off, correct when an
  environment enables it), or allowlist it with a reason if OSISM deliberately
  omits it. The most common allowlist case is a var for a service **OSISM does
  not ship/support at all** (never evaluated anywhere) — note this is a stronger,
  stable claim than "off in the base defaults", which an environment can override
  (metalbox enables ironic, so `enable_ironic_pxe_filter` bites there). Other
  cases: a var OSISM supplies another way, or an upstream typo.

### Plugin: kolla_mirror_verbatim

**Enabled — `001` must stay a verbatim mirror of upstream-newest.** Enforces
Convention X: `osism/defaults` `all/001-kolla-defaults.yml` must equal upstream
kolla-ansible `group_vars/all` at the **newest** supported release
(`release_range[-1]`, `stable/2025.2` today), compared as parsed YAML values
(jinja lives in string values and compares as strings). Every OSISM opinion lives
in a `099-*` file, never in `001`, and the allowlist is never a home for a
group_var. The sibling `kolla_groupvars_missing` only proves the upstream *union*
is *supplied somewhere* — it cannot see values or which file a key sits in, so it
cannot keep `001` pure; this check does.

Each deviation is one of three shapes, and the finding prints the exact
destination to move the key to:

- **absent from `001`** (upstream-newest defines it) → mirror the upstream
  key+value verbatim into `001`.
- **value differs** → restore the upstream value in `001`; put OSISM's value in
  `099-*` (plain, or an `openstack_version` gate if it varies by release).
- **in `001`, not upstream-newest** → remove from `001` and route it: **delete**
  if another OSISM layer (`099-*` / overlay / `versions.yml.j2`) already supplies
  it; **`010-<L>.yml`** (self-retiring, `L` = newest older release still defining
  it) if an older supported release still has it — parent spec D8, not `099`, not
  the allowlist; **`099-*` custom-features** if OSISM-invented.

    python3 src/check-drift.py --group kolla --plugin kolla_mirror_verbatim

- **Reads:** `osism/defaults` `all/001-kolla-defaults.yml`; `openstack/kolla-ansible`
  `group_vars/all` at the newest release's resolved ref (plus each older release's
  keys, to classify a dropped key and pick its `010-<L>` home).
- **Fix:** as printed per shape — mirror into `001`, or move the OSISM delta to
  `099-*` (opinions) / `010-<L>.yml` (dropped upstream keys). Never allowlist a
  group_var (Convention X).

### Plugin: kolla_orphan_config

**Enabled — companion config must not outlive its service.** For each service
`kolla_enablement_orphan` reports as orphaned (minus the allowlisted OSISM
inventions), this flags every dead companion and image-definition var
(`<service>_*`) across `osism/defaults` `all/*.yml`, grouped by file. The
`enable_<name>` flag itself is left to `kolla_enablement_orphan`; this catches the
`<service>_port`, `<service>_tag`, `<service>_*_image` and similar vars that would
otherwise dangle after the flag is removed.

    python3 src/check-drift.py --group kolla --plugin kolla_orphan_config

- **Reads:** `osism/defaults` `all/*.yml` (orphan set derived via
  `kolla_enablement_orphan`).
- **Fix:** remove these vars from the listed `osism/defaults` file, or allowlist
  any intentionally kept (an OSISM invention with no upstream service).

### Plugin: kolla_image_orphan

**Built — an image-catalogue entry must not outlive its upstream role.** The
image-catalogue-driven complement to the enable-flag-driven
`kolla_enablement_orphan` → `kolla_orphan_config` path. That path only flags a
removed service's companion vars while OSISM still defines its `enable_<svc>`
flag; a service upstream removed for which OSISM never had an enable flag leaves
its `<svc>_image` / `<svc>_tag` catalogue entries undetected. This check keys off
the catalogue instead: for each OSISM kolla `*_image` (excluding `*_image_full`)
and `*_tag` var in `osism/defaults` `all/*images-kolla*.yml`, it flags the var
when its **name** is absent from upstream kolla-ansible role defaults across
**every** supported release. It is a pure variable-name set-diff over the union
of supported refs — no image-name resolution or alias chains — and both suffixes
are tested independently. Restricting to the `*images-kolla*` catalogue glob
keeps non-kolla images (ceph, cilium, k3s) out of the comparison. An empty OSISM
catalogue match or an empty upstream union is a hard error, since either would
turn the set-diff into a silent all-clear or a mass false positive.

    python3 src/check-drift.py --group kolla --plugin kolla_image_orphan

- **Reads:** `osism/defaults` `all/*images-kolla*.yml`; `openstack/kolla-ansible`
  `ansible/roles/*/defaults/main.yml` (`*_image` / `*_tag`) per resolved release
  ref, unioned across the supported range.
- **Fix:** remove the orphaned `<svc>_image` / `<svc>_tag` var from the listed
  catalogue file, or allowlist any intentionally kept (an OSISM-built image with
  no upstream kolla-ansible role, e.g. mariabackup, tempest).

### Plugin: kolla_secrets_orphan

**Enabled — a secret must not outlive its service.** Per release, compares the top-level keys of the OSISM
kolla secrets template (`cfg-cookiecutter`
`environments/kolla/secrets.yml.<release>`) against upstream kolla-ansible's
authoritative `etc/kolla/passwords.yml` at that release's resolved ref. A key in
the OSISM template with no upstream counterpart is an orphaned secret (the service
that consumed it was removed upstream). The comparison is per release, not
unioned. Keys are extracted with a line regex rather than a YAML load, because the
template values are jinja/cookiecutter placeholders that do not parse as YAML; a
release with no OSISM template is skipped, not an error.

    python3 src/check-drift.py --group kolla --plugin kolla_secrets_orphan

- **Reads:** `cfg-cookiecutter` `environments/kolla/secrets.yml.<release>`;
  `openstack/kolla-ansible` `etc/kolla/passwords.yml` per resolved release ref.
- **Fix:** remove the orphaned secret from the cfg-cookiecutter template (and
  regenerate environments), or allowlist it if it is an OSISM-invented secret.

### Plugin: kolla_enablement_build

**Enabled → built — an enabled service must be in the build set.** For each supported release R, an OSISM-enabled kolla service
(`osism/defaults` `all/*.yml` `enable_X: "yes"`) that is **buildable
upstream** at R (a `docker/X` dir exists in `openstack/kolla` at R's ref) must
appear in OSISM's **build set** for R — `osism/release`
`latest/openstack-R.yml` under `infrastructure_projects` or `openstack_projects`.
An enabled, buildable service absent from the build set is flagged: nothing builds
its image. The upstream `docker/` universe is the scope filter, so feature flags
with no image (`enable_neutron_qos`, …) are naturally out of scope — no alias
table.

This check is **range-aware**: the supported releases derive from the
`release/latest/openstack-*.yml` file set (override with the `releases:` config
key), and the comparison spans that whole range for the reason set out in
"Release model and why the range-aware checks use unions". Each release resolves
to a real upstream ref via a probe —
`stable/<r>` → `unmaintained/<r>` → `<r>-eol` → `<r>-eom`, first that exists —
because OSISM keeps building releases upstream OpenStack has moved past EOL.
Override a specific ref with `release_refs: {kolla: {"2024.2": "2024.2-eol"}}`. A
release that resolves to no ref is a hard error, not a silent skip.

    python3 src/check-drift.py --group kolla --plugin kolla_enablement_build

- **Reads:** `osism/defaults` `all/*.yml`; `osism/release`
  `latest/openstack-<release>.yml`; `openstack/kolla` `docker/` per resolved ref.
- **Fix:** add the service to `infrastructure_projects` or `openstack_projects`
  in the release file, or allowlist it if it is intentionally not built.

### Plugin: kolla_version_chain_upstream

**Built → version-pinned — a built service must have a version pin.** Lists the top-level service
directories of `openstack/kolla` `docker/` (at the pinned kolla ref, `stable/2025.2`
by default) and flags any whose normalised name has **no** `versions['<key>']`
line in the kolla-ansible
template — an upstream-built service with no version pin wired. The comparison is
one-way (upstream → template) and does not fold in the producer's keys, so a
service present in the producer but missing a template line still flags here.

    python3 src/check-drift.py --group kolla --plugin kolla_version_chain_upstream

- **Reads:** `openstack/kolla` `docker/`;
  `container-image-kolla-ansible` `files/src/templates/versions.yml.j2`.
- **Fix:** add a `versions['<name>']` line to the template to pin the image (and
  wire the producer); if intentionally unpinned, add an allowlist entry with a
  reason. Build-base layers (`base`, `openstack_base`) and naming/variant or
  supporting images with no distinct version key are carried in the shipped
  allowlist.

### Plugin: kolla_version_chain_inner

**Version-pinned — a version pin must resolve to a value.** The producer
(`container-images-kolla`) emits an SBOM mapping each tracked service to a
concrete version via `SBOM_IMAGE_TO_VERSION` in `src/tag-images-with-the-version.py`.
The consumer (`container-image-kolla-ansible`) renders one line per service in
`files/src/templates/versions.yml.j2`:

    kolla_<svc>_version: "{{ versions['<svc>']|default(openstack_version) }}"

A `versions['<key>']` key referenced in the template but **absent** from the SBOM
map is never produced, so the line silently falls back to the coarse
`openstack_version` — an inert pin, with no error. This plugin flags exactly those
keys, and **classifies** each: if OSISM actually deploys the service (enabled in
`all/*.yml` and buildable in `openstack/kolla` `docker/`) the right fix is to
**wire the SBOM key**; otherwise the template line is dead and should be
**removed**. The report renders the two buckets as separate blocks, each pointing
at the repo to edit.

    python3 src/check-drift.py --group kolla --plugin kolla_version_chain_inner

- **Reads:** `container-image-kolla-ansible` `files/src/templates/versions.yml.j2`;
  `container-images-kolla` `src/tag-images-with-the-version.py`;
  `osism/defaults` `all/*.yml`; `openstack/kolla` `docker/`.
- **Fix:** wire the key into `SBOM_IMAGE_TO_VERSION` if OSISM deploys it, else
  remove the dead template line; allowlist keys meant to default.

### Plugin: kolla_inventory

**Deployed — a deployed service must have inventory groups.** Compares the ansible group names (INI section headers) of upstream
`openstack/kolla-ansible` `ansible/inventory/multinode` (at the pinned
kolla-ansible ref, `stable/2025.2` by default)
against the union of group names in the OSISM inventory files
`generics/inventory/50-kolla` and `51-kolla`. A group present upstream but absent
locally is flagged (one-way, upstream → local) — the failure mode behind
generics#599 (neutron 2025.2 groups) and #601 (ironic-dnsmasq). Each flagged
group's upstream **members** (child groups or hosts) ride in the `expected` field,
so a red line tells a maintainer what to add. Subsumes the retired
`check-kolla-inventory.py`.

    python3 src/check-drift.py --group kolla --plugin kolla_inventory

- **Reads:** `openstack/kolla-ansible` `ansible/inventory/multinode`;
  `generics/inventory/50-kolla` and `51-kolla`.
- **Fix:** add the group and its members to `50/51-kolla`; if the service is
  intentionally not deployed, add an allowlist entry with a reason (often
  `match: prefix` — see below). Base-infra groups the OSISM overlay assumes exist
  in the environment inventory are carried in the shipped allowlist.

## Allowlist

`src/drift-allowlist.yml` holds structurally-intentional exceptions — not a
baseline of everything currently drifted. Each entry needs `plugin`, `image`, and
a non-empty `reason`; optional `alias` and `found_src` narrow the match.

By default an entry matches a drift's `image` **exactly**. An entry may set
`match: prefix` to also match sub-groups, using the ansible group-name separators
`-` and `:` as boundaries:

    - {plugin: kolla_inventory, image: cyborg, match: prefix, reason: not deployed}

matches `cyborg`, `cyborg:children`, and `cyborg-agent:children`, but **not**
`cyborgx`. `image` must be non-empty in either mode.

**Stale entries are a hard error.** An allowlist entry (exact or prefix) that
matches no real drift is reported as a **stale allowlist** entry and makes the run
exit non-zero — a dead exception is a bug, not a silent no-op. Detection is scoped
to the plugins that actually ran (a `--plugin` run only judges that plugin's
entries) and is skipped under `--no-allowlist`. Remove an entry once its drift is
fixed.

## Release model and why the range-aware checks use unions

To read the range-aware plugins correctly (or write one), you need the OSISM
defaults release/bake model:

- **`osism/defaults` is a single-tip repo.** It has one line of history (`main`
  plus feature branches) and dated release **tags** like `v0.20260701.0`. There
  are **no** per-OpenStack-release branches of defaults.
- **A defaults tag is baked into each `osism/release` manifest at release-cut
  time.** In `osism/release`, `latest/base.yml` and every
  `latest/openstack-<rel>.yml` carry a renovate-managed
  `defaults_version: '<tag>'`. An already-cut manager version freezes an older
  tag — e.g. `10.1.0/base.yml` pins `v0.20260526.0` while `latest/base.yml` pins
  `v0.20260701.0`.
- **One defaults snapshot serves every supported release.** Within a single
  manager version, one `defaults_version` is shared across **all** supported
  OpenStack releases: in `latest/`, `openstack-2024.1.yml … openstack-2025.2.yml`
  all pin the same `defaults_version`. So that one `osism/defaults` snapshot must
  be simultaneously correct for every supported release (2024.1, 2024.2, 2025.1,
  2025.2).

The range-aware kolla plugins therefore read `osism/defaults` at its single tip
(`remote.branch = main`, unpinned) and compare it against the **union** of the
per-release upstream refs across the supported range (`release_range` /
`release_to_ref`). Both directions need the union, for slightly different
reasons:

- **Removal direction** (e.g. `kolla_enablement_orphan`): a service or flag is
  only an orphan if it is absent from upstream at **every** supported release.
  The one defaults tip must not break **any** supported release, so anything a
  still-in-range release defines is still needed — comparing against the union is
  the conservative choice that avoids false orphans.
- **Add direction** (e.g. `kolla_groupvars_missing`): a var is missing if
  upstream defines it at **any** supported release and OSISM lacks it — because
  that one defaults tip has to satisfy every supported release at once.

## Adding a plugin

A plugin is a module under `src/osism_drift/drift/` exposing:

    NAME: str
    DESCRIPTION: str
    INPUT_FILES: list[tuple[str, str]]    # (repo_key, repo_relative_path) — for --help
    SUMMARY: str                          # "{n} ..." lead line for the report (must contain {n})
    REMEDIATION: str                      # the Fix: line for the report
    def run(config, allowlist, verbose: bool = False) -> list[DriftEntry]

`run()` reads inputs via `source.read(repo_key, rel_path, config)` /
`read_optional` / `list_dir` (GitHub by default, or a local checkout discovered
under a `--base-dir`; the `repo_key` underscores convert to hyphens in the
dir/URL). Build a `DriftEntry` (`osism_drift.model`) per finding and pass each
through the allowlist in one step:

    entry = allowlist.apply(entry)   # returns it unchanged, or as an allowlisted copy

Register the module in `src/osism_drift/drift/__init__.py`
(`KOLLA_PLUGINS = [...]`, in lifecycle order) **and** add a
`plugins.<NAME>: {enabled: true}` stanza to
`src/drift-config.yml` (a plugin absent from config is skipped; a registry
test guards that every registered plugin is enabled). A new repo needs no config —
it is fetched remotely by default, or discovered by name under a `--base-dir`.
Mirror the test pattern in `tests/kolla_drift/`: synthetic fixtures laid out as
`fixtures/<repo-dir>/...` plus a `Config(..., base_dirs=(str(FIXT),))`.
