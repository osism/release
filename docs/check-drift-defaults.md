# check-drift defaults group

A detector for variables osism/defaults sets that nothing reads. osism/defaults
ships its variables to every OSISM deployment, and nothing notices when one stops
doing anything: its reader is removed or renamed upstream or in an OSISM repo, or
it never matched its reader. The last kind is a silent bug —
`k3s_add_labels_monitoring` misses the `^k3s_add_labels__.+$` pattern
osism-kubernetes merges labels with, so monitoring nodes never got their label.

The framework (config, source resolution, allowlist, output) is shared; see
[check-drift-kolla.md](check-drift-kolla.md) for the full reference.

## Run it locally

    python3 src/check-drift.py --group defaults
    python3 src/check-drift.py --group defaults --base-dir ~/src/osism --base-dir ~/src/openstack --remote-fallback
    python3 src/check-drift.py --group defaults --format json

It reads about 30 whole trees (each once, as a tarball or a local
`git archive`). A remote run extracts about 150 MB into `TMPDIR`, removed
when the run ends; the compressed download is smaller.
Set `GITHUB_TOKEN` (or `GH_TOKEN`) to avoid the anonymous rate limit.

## What is checked

The top-level keys of osism/defaults' `<dir>/<file>.yml` files, except the
kolla-owned layers: `all/001-*` (the upstream mirror), `all/010-*` (per-release
compat) and any file whose name contains `kolla`. Those belong to the `kolla_*`
plugins.

## Where it looks for readers

- osism/defaults itself — a default that reads another, in its value.
- Every `osism.<x>` collection release `latest/base.yml` lists, as
  `ansible-collection-<x>`, and the core repos in `defaults_corpus.CORE_REPOS`,
  at `main`.
- The same collections plus ansible-playbooks, ansible-playbooks-manager and
  generics again at the tag `latest` pins: a reader removed on `main` still
  counts until `latest` stops shipping it.
- Upstream kolla-ansible at every supported OpenStack release, and ceph-ansible
  at every ceph flavour's `ceph_ansible_version` (quincy still pins
  `stable-7.0`, which keeps some old ceph-ansible variables alive).
- The galaxy roles `base.yml` pins under `ansible_roles`, mapped to their GitHub
  `owner/repo` through release `etc/roles.yml` (the file the ansible image's
  `requirements.yml` is rendered from), read at the pin as shipped. They are
  always read from GitHub, so a local-only run of this group (`--base-dir`
  without `--remote-fallback`) is refused up front: pass `--remote-fallback`.

A name counts as read when it appears anywhere in a file, except in YAML as the
key of a mapping line or on a comment line. Prose (`*.md`, `*.rst`, top-level
`doc/`, `docs/`, `releasenotes/`), upstream `tests/` and the upstream top-level
`tox*.ini` files that drive it are skipped. Names read
by pattern through `community.general.merge_variables` are recognised from the
literal patterns in the corpus.

**Not searched:** third-party collections (debops, community.*, …). Reading
them would mean two dozen more downloads per run and, worse, a token corpus
large enough to hide real findings behind accidental matches.

## Findings

- **Unread** — nothing in the corpus reads it. Remove it, or allowlist it if a
  third-party collection reads it.
- **Near miss** — unread, and one underscore away from a `^<prefix>__.+$`
  merge pattern. Rename it; the finding names the pattern and its file.
- **Unresolved merge pattern** (fails the run) — a `merge_variables` call
  whose pattern is not a string literal. While one exists every unread finding
  is advisory, because that call might read any of them, so the call itself is
  reported as actionable: otherwise the check would go quiet unnoticed.

## Allowlisting a third-party reader

    - {plugin: defaults_orphan, image: apt__enabled, reason: "read by debops.debops 3.3.2, ansible/roles/apt/tasks/main.yml (when: apt__enabled | bool)"}

The `reason` must name the file that *reads* the variable (a task, handler or
template), not the role default that declares it. The stale-entry check
catches an entry whose variable left osism/defaults, but not one whose
*reader* left the third-party collection — this check never sees that reader.
When that collection's pin moves, re-check its entries by hand.

## Fallback: not reading the galaxy roles

If the role reads have to go (rate limits, an upstream repo gone), revert the
commit "drift: search the pinned galaxy roles" and add these entries to
`src/drift-allowlist.yml`, re-checking each reading file at the then-current pin
first:

```yaml
- {plugin: defaults_orphan, image: stig_version, reason: "read by openstack/ansible-hardening (hardening) tasks/main.yml (import_tasks {{ stig_version }}stig/main.yml)"}
- {plugin: defaults_orphan, image: security_rhel7_enable_chrony, reason: "read by openstack/ansible-hardening (hardening) tasks/rhel7stig/misc.yml"}
- {plugin: defaults_orphan, image: security_rhel7_enable_linux_security_module, reason: "read by openstack/ansible-hardening (hardening) tasks/rhel7stig/lsm.yml"}
- {plugin: defaults_orphan, image: security_rhel7_remove_shosts_files, reason: "read by openstack/ansible-hardening (hardening) tasks/rhel7stig/auth.yml"}
- {plugin: defaults_orphan, image: security_package_clean_on_remove, reason: "read by openstack/ansible-hardening (hardening) tasks/rhel7stig/apt.yml"}
- {plugin: defaults_orphan, image: security_rhel7_session_timeout, reason: "read by openstack/ansible-hardening (hardening) tasks/rhel7stig/misc.yml"}
- {plugin: defaults_orphan, image: security_sshd_allowed_macs, reason: "read by openstack/ansible-hardening (hardening) vars/main.yml"}
- {plugin: defaults_orphan, image: dotfiles_repo, reason: "read by geerlingguy/ansible-role-dotfiles tasks/main.yml"}
- {plugin: defaults_orphan, image: dotfiles_repo_version, reason: "read by geerlingguy/ansible-role-dotfiles tasks/main.yml"}
- {plugin: defaults_orphan, image: dotfiles_repo_local_destination, reason: "read by geerlingguy/ansible-role-dotfiles tasks/main.yml"}
- {plugin: defaults_orphan, image: dotfiles_files, reason: "read by geerlingguy/ansible-role-dotfiles tasks/main.yml"}
```

## When a new OSISM repo starts reading defaults

It shows up as findings for the variables only it reads. Add the repo to
`CORE_REPOS` in `src/osism_drift/defaults_corpus.py` (an `osism.*` collection
listed in `base.yml` is picked up automatically).
