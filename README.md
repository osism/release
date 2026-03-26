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
│   └── roles.yml        # Ansible roles
├── scripts/             # Release automation scripts
│   ├── create-tags.sh
│   ├── generate-changelog-input.sh
│   └── generate-release-changelog.sh
└── src/                 # Python utilities
    ├── create-version.py
    ├── git-diff-log.py
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
- `docker_images` — the `openstackclient` image version
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

When component images are built and ready, create tags for the core projects:

```bash
./scripts/create-tags.sh v0.20260322.0
```

This creates and pushes tags in the format `<project>-<version>` for the five core
container image projects:

- `osism-ansible`
- `osism-kubernetes`
- `kolla-ansible`
- `ceph-ansible`
- `inventory-reconciler`

The tags reference the current HEAD of this repository and serve as version anchors
for the container image build pipelines. Existing tags can optionally be recreated.

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
- Sets `manager_version` to the release name

### 4. Changelog generation (per-component)

Generate CHANGELOG entries from git commits using Claude:

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
1. Collects commits and diffs between consecutive tags
2. Batches them by diff size (default: 2000 lines per batch) to stay within prompt limits
3. Sends each batch to Claude for structured changelog generation
4. Merges batch results into a single entry following
   [Keep a Changelog](https://keepachangelog.com/) format
5. Auto-inserts the entry into `CHANGELOG.md`

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
