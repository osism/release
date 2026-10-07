# OSISM release repository

Release notes published at https://osism.tech/docs/release-notes/

## Overview

This repository is the central place for managing OSISM releases. It pins all component
versions (container images, Ansible collections, Python packages, GitHub repositories) for
each release and provides the tooling to create new releases, tag components, and generate
changelogs.

## Repository structure

```
.
├── latest/              # Current development versions (continuously updated)
│   ├── base.yml         # Core component versions
│   ├── ceph-*.yml       # Ceph-specific versions (quincy, reef, squid)
│   ├── ceph.yml         # Symlink → default Ceph version (currently ceph-reef.yml)
│   ├── openstack-*.yml  # OpenStack-specific versions (2024.1, 2024.2, 2025.1, 2025.2)
│   └── openstack.yml    # Symlink → default OpenStack version (currently openstack-2025.1.yml)
├── <VERSION>/           # Pinned release versions (e.g. 10.0.0/, 9.5.0/)
│   └── base.yml         # Frozen component versions for this release
├── next/                # SBOMs and metadata for upcoming builds
├── archive/             # Historical versions
├── etc/                 # Reference metadata
│   ├── images.yml       # Docker image name → registry path mapping
│   ├── collections.yml  # Ansible Galaxy collections
│   ├── roles.yml        # Ansible roles
│   └── changelog-repositories.yml  # Component name → GitHub source repository
├── scripts/             # Release automation scripts
│   ├── create-tags.sh
│   ├── generate-changelog-input.sh
│   └── generate-release-changelog.sh
└── src/                 # Python utilities
    ├── create-version.py
    ├── git-diff-log.py
    ├── release-notes.py
    └── remove-images-from-quay.py
```

## Version files

### `latest/base.yml`

The central configuration file. It tracks versions for all components of the OSISM stack:

| Section               | Content                                                        |
|-----------------------|----------------------------------------------------------------|
| `manager_version`     | OSISM manager version identifier                               |
| `ansible_version`     | Ansible and ansible-core versions                              |
| `*_version`           | GitHub tag references for defaults, generics, playbooks, etc.  |
| `osism_projects`      | Python packages (osism, ara, docker, k3s)                      |
| `docker_images`       | 50+ container image versions                                   |
| `ansible_roles`       | External Ansible roles with commit hashes or tags              |
| `ansible_collections` | Ansible Galaxy collections with semantic versions              |

Each version entry has a Renovate annotation comment above it (e.g.
`# renovate: datasource=docker depName=registry.osism.tech/osism/osism-ansible`)
that enables automated dependency updates.

### `latest/ceph-*.yml` and `latest/openstack-*.yml`

Because OSISM supports multiple Ceph and OpenStack versions simultaneously, their
versions are tracked in separate files — one per supported release stream.

**Ceph files** (`ceph-quincy.yml`, `ceph-reef.yml`, `ceph-squid.yml`):

Each file pins the versions specific to one Ceph release stream:
- `ceph_version` — the Ceph release name (e.g. `reef`)
- `ceph_ansible_version` — the ceph-ansible branch (e.g. `stable-8.0`)
- `ansible_version` / `ansible_core_version` — the Ansible versions required by that
  ceph-ansible branch (these can differ from the versions in `base.yml`)
- `defaults_version`, `generics_version`, `playbooks_version` — pinned component versions
- `docker_images` — Ceph container image versions (`ceph`, `cephclient`)

**OpenStack files** (`openstack-2024.1.yml`, `openstack-2024.2.yml`, `openstack-2025.1.yml`,
`openstack-2025.2.yml`):

Each file pins the versions specific to one OpenStack release:
- `openstack_version` / `openstack_previous_version` — the release identifier and its
  predecessor (used for upgrades)
- `ansible_version` / `ansible_core_version` — the Ansible versions required for this
  OpenStack release
- `defaults_version`, `generics_version`, `playbooks_version` — pinned component versions
- `docker_images` — the `openstackclient` image version; from 2025.1 on pinned
  to a concrete release (the same in every file) and kept up to date by Renovate
- `infrastructure_projects` — list of Kolla infrastructure projects (shared across all
  OpenStack versions)
- `openstack_projects` — all OpenStack service projects with their stable branch references
  (e.g. `stable-2025.1`); some projects like `gnocchi` use independent versioning
  (e.g. `stable/4.7`)

**Symlinks** — default versions:

- `ceph.yml` → `ceph-reef.yml` — points to the current default Ceph version
- `openstack.yml` → `openstack-2025.1.yml` — points to the current default OpenStack version

These symlinks are used by consumers that do not specify a particular version and
want to use the recommended default. When the default changes (e.g. after a new
OpenStack release is promoted), the symlink target is updated.

**Release versions** (`<VERSION>/`) only contain `base.yml`. The OpenStack and Ceph
files are not copied into release directories because the supported OpenStack and
Ceph versions remain the same across patch releases — they are always read from
`latest/`.

### `<VERSION>/base.yml`

A frozen snapshot of `latest/base.yml` at the time of a release. Created by
`src/create-version.py`. Does not contain Renovate annotations.

## Version numbering

| Component        | Format                  | Examples                        |
|------------------|-------------------------|---------------------------------|
| OSISM releases   | Semantic versioning     | `9.5.0`, `10.0.0`, `10.0.0-rc.1` |
| Component builds | Date-based              | `v0.20260322.0`, `0.20260320.0` |
| External deps    | Upstream versioning     | `18.2.7` (Ceph), `2025.1` (OpenStack) |

## Release process

### 1. Continuous dependency updates

[Renovate](https://docs.renovatebot.com/) monitors all upstream projects and automatically
creates PRs to update versions in the `latest/` directory. It supports multiple datasources:

- **Docker images** (registry.osism.tech, Docker Hub)
- **PyPI packages** (ansible, osism)
- **GitHub tags/releases** (defaults, generics, playbooks, docker, k3s)
- **Galaxy collections** (osism.commons, community.docker, etc.)

Related updates are grouped (e.g. ansible + ansible-core, postgres + pgautoupgrade)
to keep PRs manageable.

### 2. Tag creation

Before creating tags, trigger a Renovate run on this repository once (e.g. via the
Renovate dashboard issue or the Mend app) and make sure that all required PRs have
been merged beforehand. This applies above all to the `osism` Python package, but
also to the Ansible collections (`osism.commons`, `osism.services`,
`osism.validations`, ...) and the Ansible playbooks (`osism.playbooks`,
`manager-playbooks`). Only then does `latest/base.yml` reflect the state that the
images are supposed to be built from.

Then create tags for the core projects:

```bash
./scripts/create-tags.sh v0.20260322.0
```

This creates and pushes tags in the format `<project>-<version>` for the six core
container image projects:

- `kolla`
- `osism-ansible`
- `osism-kubernetes`
- `kolla-ansible`
- `ceph-ansible`
- `inventory-reconciler`

The tags reference the current HEAD of this repository and serve as version anchors
for the container image build pipelines. The order in which the tags are created
here does not matter.

The script checks for every project whether the tag already exists (locally or on
the remote) and where it points to. A tag that already points to the current HEAD
is left as it is. Otherwise the script asks whether to **move** the tag to the
current HEAD (the existing tag is deleted locally and on the remote and created
again) or to **ignore** it (the existing tag is left untouched). This allows
re-running the script with the same version after tags have been created on the
wrong commit: fix the checkout, run the script again and answer "move" for every
tag that has to be corrected.

The tags in this repository alone do not trigger any builds. After they have been
pushed, three further steps are required, in this order:

1. Create and push the tag `v0.20260322.0` (the plain version, without project
   prefix) in [osism/container-images-kolla](https://github.com/osism/container-images-kolla)
   and wait until the build has finished. The kolla service images have to exist
   before all other images, as the other builds depend on them.
2. Only then create and push the same tag `v0.20260322.0` in all other container
   image repositories:
   - [osism/container-image-osism-ansible](https://github.com/osism/container-image-osism-ansible)
   - [osism/osism-kubernetes](https://github.com/osism/osism-kubernetes)
   - [osism/container-image-kolla-ansible](https://github.com/osism/container-image-kolla-ansible)
   - [osism/container-image-ceph-ansible](https://github.com/osism/container-image-ceph-ansible)
   - [osism/container-image-inventory-reconciler](https://github.com/osism/container-image-inventory-reconciler)
3. Once the images have been built and pushed, Renovate opens one PR per core
   image in this repository that bumps the image version in `latest/base.yml`
   to the new tag (e.g. [#2789](https://github.com/osism/release/pull/2789)
   for `osism-kubernetes`). Merge all of these PRs before continuing: only
   then does `latest/base.yml` reference the images that were just built,
   and a release version created in the next step is based on it. If a PR is
   missing, trigger a Renovate run on this repository once more.

### 3. Create a release version

To freeze the current `latest/` state into a named release:

```bash
./src/create-version.py 10.0.0
```

This:
- Creates a new directory `10.0.0/`
- Copies `latest/base.yml` (without Renovate comments)
- Queries git tags to resolve the latest versions of core container images
  (osism-ansible, osism-kubernetes, inventory-reconciler, kolla-ansible, ceph-ansible, kolla)
- Adds the `openstackclient` image version from `latest/openstack.yml`
- Sets `manager_version` to the release name

### 4. Changelog generation (per-component)

Every component that changed between the previous release and the new one
needs a `CHANGELOG.md` section for its new version. The script generates
these sections from git commits using Claude. It is run **inside the
component repositories**, not in this repository; the repositories in
question are the ones listed under `components` in
`etc/changelog-repositories.yml` (python-osism, the Ansible collections and
playbooks, defaults, generics and the container image repositories). The
recommended invocation for the release process, with the current working
directory being the component repository and `<release>` the path of a
checkout of this repository:

```bash
# In every changed component repository
<release>/scripts/generate-changelog-input.sh --auto --pr --tags-only
```

`--auto` picks up all tags that are not yet documented in `CHANGELOG.md`,
`--tags-only` makes sure that only existing tags are documented (commits
after the last tag are not part of any release yet), and `--pr` commits the
result on a branch and opens a pull request in the component repository.
**Merge these changelog PRs in all component repositories before
continuing with step 5**: the release notes are generated from the
`CHANGELOG.md` files on the default branches of the component repositories,
so a changelog PR that is still open leaves its component out of the
release notes.

Further variants:

```bash
# Auto-detect: process all tags not yet in CHANGELOG.md
./scripts/generate-changelog-input.sh --auto

# Specific tag
./scripts/generate-changelog-input.sh v0.20260322.0

# All tags from a given point onwards
./scripts/generate-changelog-input.sh --from v0.20260301.0

# Only generate input files, do not run Claude
./scripts/generate-changelog-input.sh --auto -n
```

The script:
1. Collects commits and diffs between consecutive tags; changelog
   housekeeping commits (touching only `CHANGELOG.md`, e.g. the
   release-notes PRs created by this script) are excluded and never
   appear in later changelogs
2. Batches them by diff size (default: 2000 lines per batch) to stay within prompt limits
3. Sends each batch to Claude for structured changelog generation
4. Merges batch results into a single entry following
   [Keep a Changelog](https://keepachangelog.com/) format
5. Auto-inserts the entry into `CHANGELOG.md`
6. Deletes the generated `changelog-input-*.md` working files again
   (`--keep-input` keeps them; with `-n` they are always kept)

A tag without any change of its own (its range contains nothing but the
release-notes commit of the previous tag, or it points to the same commit)
is not skipped: the release process sometimes requires tagging a component
again so that its container image is rebuilt with the component versions
currently pinned in `latest/` (above all the `osism` package) and all
images of a release carry the same version tag. Such a tag gets a short
deterministic "rebuild without changes" entry, written without Claude, so
that the release notes generation (step 5) finds a `CHANGELOG.md` section
for every released version.

### 5. Release notes generation (per release)

Generate the release notes section for a follow-up release as published at
https://osism.tech/docs/release-notes/ :

```bash
# Generate the section only (written to release-notes-10.1.0.md)
./scripts/generate-release-changelog.sh 10.1.0

# Generate only the input file, review or edit it, then reuse it
# (skips the changelog fetches and the upstream analysis)
./scripts/generate-release-changelog.sh -n 10.1.0
./scripts/generate-release-changelog.sh -i release-notes-input-10.1.0.md 10.1.0

# Insert it into an existing checkout of osism.github.io
./scripts/generate-release-changelog.sh --site-dir ../osism.github.io 10.1.0

# Clone osism.github.io, insert, commit and open a pull request
./scripts/generate-release-changelog.sh --pr 10.1.0
```

Requires [uv](https://docs.astral.sh/uv/) (fallback: a `python3` with
`requests` and `PyYAML` installed), the `claude` CLI, and an authenticated
GitHub CLI (`gh`); with `-n` (input file only) neither `claude` nor `gh`
is needed.

The script:
1. Diffs `<version>/base.yml` against the previous release
2. Fetches the CHANGELOG.md sections of all changed OSISM components for the
   version range (mapping: `etc/changelog-repositories.yml`); this includes
   derived components whose version is pinned in a requirements file of
   another component (netbox-manager, openstack-image-manager and
   openstack-flavor-manager in python-osism). For components installed from
   a branch when another image is built (openstack-project-manager, cloned
   from `main` by the osism image build), the commits of that branch between
   the build times of the two source versions are included instead
3. When `docker_images.kolla_ansible` changed, collects the upstream
   [openstack/kolla-ansible](https://github.com/openstack/kolla-ansible)
   changes pulled in by the image rebuild: the image is built from the
   branch of the OpenStack version in use (resolved via the
   `kolla-ansible-v<version>` tag of this repository and its
   `latest/openstack.yml` symlink), so the commit subjects and reno release
   notes between the build times of the two image versions are included in
   the input for a dedicated "OpenStack services" subsection. The added and
   removed downstream patches (`patches/<openstack_version>`) of the
   kolla-ansible image and, when `docker_images.kolla` changed, of the
   kolla service images are included as well: a removed patch whose change
   landed upstream is neither reported as a removal nor presented again as
   a new feature. The effective OSISM kolla defaults
   ([osism/defaults](https://github.com/osism/defaults), pinned as
   `defaults_version`) are included as a reference: configuration advice is
   checked against what OSISM actually sets, changes only affecting
   distributions other than Ubuntu are ignored, and kolla-ansible commands
   are written as their `osism apply` equivalents (there is no
   `kolla-ansible` command in OSISM)
4. When `docker_images.kolla` changed, determines the OpenStack Security
   Advisories whose fixes the kolla images contain in addition to the
   previous release: the OSSA pages of
   [osism.github.io](https://github.com/osism/osism.github.io)
   (`docs/appendix/security/ossa-*.md`, read from `--site-dir` or GitHub)
   link the container-images-kolla commits and pull requests that ship a
   fix as downstream patches. An advisory counts if such a commit touches
   the OpenStack version of the release and lies in the image range, or,
   if no referenced commit touches that version (the fix came with the
   upstream sources), if the advisory was published between the two image
   builds. The advisories become a "Security fixes" subsection with a
   fixed wording and links to the advisory pages. The input also names the
   OpenStack version of the release, so that changes only affecting other
   OpenStack versions are left out
5. Lets Claude write an operator-focused release notes body; for entries
   whose changelog line alone does not tell an operator anything, Claude
   looks up the referenced pull requests via read-only `gh pr view`/
   `gh pr diff` calls (chores and Renovate bumps are skipped). Changes
   that only concern the MetalBox (baremetal, SONiC, netbox-manager) are
   grouped in a trailing "MetalBox" subsection. The body is sanitized
   deterministically before use
6. Inserts the section into `docs/release-notes/osism-<major>.md` of
   [osism.github.io](https://github.com/osism/osism.github.io) following the
   `osism-10.md` layout: a plain row in the release table and a
   `## <version>` section (no date suffix), inserted before the first
   existing release section or directly after the release table if the page
   has none yet

The component CHANGELOGs are the content source: the changelog PRs from
step 4 have to be merged in all changed component repositories before this
script is run, so that their `CHANGELOG.md` files cover the new component
versions. A component whose changelog PR is still open is missing from the
generated release notes.

## CI

Gated by [Zuul](https://zuul-ci.org/) with the following checks on every PR
and as periodic daily jobs:

- `flake8`
- `yamllint`
- `python-black`

PRs are merged via squash-merge.

## Tracked projects

The 12 core projects whose versions are managed in this repository:

| Project              | Type                | Repository                                 |
|----------------------|---------------------|--------------------------------------------|
| osism-ansible        | Container image     | osism/container-image-osism-ansible        |
| kolla-ansible        | Container image     | osism/container-image-kolla-ansible        |
| ceph-ansible         | Container image     | osism/container-image-ceph-ansible (?)     |
| osism-kubernetes     | Container image     | osism/osism-kubernetes                     |
| inventory-reconciler | Container image     | osism/container-image-inventory-reconciler |
| osism                | Python package      | osism/python-osism                         |
| osism.services       | Ansible collection  | osism/ansible-collection-services          |
| osism.commons        | Ansible collection  | osism/ansible-collection-commons           |
| osism.playbooks      | Ansible playbooks   | osism/ansible-playbooks                    |
| osism.validations    | Ansible collection  | osism/ansible-collection-validations       |
| manager-playbooks    | Ansible playbooks   | osism/ansible-playbooks-manager            |
| defaults             | Configuration       | osism/defaults                             |
| generics             | Configuration       | osism/generics                             |
