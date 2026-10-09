"""The corpus defaults_orphan searches for readers, and the scan over it.

Sources, each read as a whole tree via source.tree_dir:

- osism/defaults itself (only values count, by the var_refs rules),
- every osism.<x> collection that release latest/base.yml lists, as
  ansible-collection-<x>, plus CORE_REPOS, at their configured ref,
- the repos `latest` pins (collections, PIN_KEYS) again at the pinned tag,
  so a reader removed on main still counts until `latest` stops shipping it,
- upstream kolla-ansible at every supported OpenStack release and
  ceph-ansible at every flavour's ceph_ansible_version.

- the galaxy roles base.yml `ansible_roles` pins, at their pins, in the GitHub
  repos etc/roles.yml names (always read remotely).

Third-party collections are deliberately absent (see
docs/check-drift-defaults.md). An empty range is an error: without upstream,
every kolla- or ceph-only variable would be reported for deletion.
"""

import os
import re
from dataclasses import dataclass, field

import yaml

from osism_drift import enablement, playbooks, source, var_refs
from osism_drift.http import SourceError

# The only hand-kept repo list: OSISM repos that read defaults and are not
# osism.* collections. A new consumer repo shows up as false findings for the
# variables it reads; add it here.
CORE_REPOS = (
    "ansible_playbooks",
    "ansible_playbooks_manager",
    "osism_kubernetes",
    "generics",
    "python_osism",
    "container_image_osism_ansible",
    "container_image_ceph_ansible",
    "container_image_kolla_ansible",
    "container_image_inventory_reconciler",
)

# latest/base.yml *_version keys -> the repo they pin.
PIN_KEYS = (
    ("ansible_playbooks", "playbooks_version"),
    ("ansible_playbooks_manager", "manager_playbooks_version"),
    ("generics", "generics_version"),
)

# Never searched: VCS metadata and tooling/vendored trees of a local checkout.
_WALK_SKIP_DIRS = frozenset({".git", ".tox", ".venv", "venv", "node_modules"})
_PROSE_DIRS = frozenset({"doc", "docs", "releasenotes"})
_PROSE_SUFFIXES = (".md", ".rst")
_UPSTREAM_SKIP_DIRS = frozenset({"tests"})
# Top-level tox files drive upstream tests/: ceph-ansible's tox-update.ini
# passes `--extra-vars "osd_scenario=lvm"` to a tests/ playbook.
_UPSTREAM_TOX = re.compile(r"tox[^/]*\.ini")


@dataclass(frozen=True)
class Source:
    """One tree to search: a repo at a ref (None = its configured ref)."""

    repo: str
    ref: str | None
    upstream: bool = False
    external: bool = False  # `repo` is a verbatim GitHub "owner/slug"

    def label(self, config) -> str:
        if self.external:
            return f"{self.repo}@{self.ref}"
        ref = (
            self.ref if self.ref is not None else source.current_ref(self.repo, config)
        )
        return f"{self.repo.replace('_', '-')}@{ref}"


@dataclass
class Corpus:
    """Everything the sources read: names, merge patterns, unresolved calls."""

    names: set = field(default_factory=set)
    patterns: list = field(default_factory=list)  # (kind, pattern, where)
    unresolved: list = field(default_factory=list)  # where
    labels: list = field(default_factory=list)

    def reads(self, name: str) -> bool:
        return name in self.names or any(
            var_refs.pattern_matches(kind, pattern, name)
            for kind, pattern, _where in self.patterns
        )

    def near_miss(self, name: str):
        """(corrected name, pattern, where) for the first pattern `name`
        misses by one underscore, or None."""
        for kind, pattern, where in self.patterns:
            if kind != "regex":
                continue
            fixed = var_refs.near_miss(name, pattern)
            if fixed is not None and var_refs.pattern_matches(kind, pattern, fixed):
                return fixed, pattern, where
        return None


def pin_to_ref(pin: str) -> str:
    """The git tag for a base.yml pin: collection pins lack the tag's `v`
    (`0.20261005.0`), *_version pins carry it (`v0.20261005.0`). Anything not
    starting with a digit (`v…`, a branch) is used verbatim."""
    return f"v{pin}" if pin[:1].isdigit() else pin


def sources(config) -> list:
    """Every Source to search, in a stable order, without duplicates."""
    base = yaml.safe_load(source.read("release", "latest/base.yml", config)) or {}
    out = [Source("defaults", None)]
    pinned = []
    for name, pin in sorted((base.get("ansible_collections") or {}).items()):
        if name.startswith("osism."):
            repo = "ansible_collection_" + name[len("osism.") :]
            out.append(Source(repo, None))
            pinned.append((repo, str(pin)))
    if not pinned:
        raise SourceError(
            "no osism.* collection in release latest/base.yml ansible_collections: "
            "the collections would not be read"
        )
    out.extend(Source(repo, None) for repo in CORE_REPOS)
    for repo, key in PIN_KEYS:
        if key not in base:
            raise SourceError(f"{key} missing from release latest/base.yml")
        pinned.append((repo, str(base[key])))
    out.extend(Source(repo, pin_to_ref(pin)) for repo, pin in pinned)

    releases = enablement.release_range(config)
    if not releases:
        raise SourceError(
            "no supported OpenStack release: kolla-ansible would not be read"
        )
    for release in releases:
        ref = source.release_to_ref("kolla_ansible", release, config)
        out.append(Source("kolla_ansible", ref, upstream=True))
    flavours = playbooks.ceph_ansible_flavours(config)
    if not flavours:
        raise SourceError(
            "no ceph flavour with a ceph-ansible branch: ceph-ansible would not be read"
        )
    for flavour in flavours:
        ref = playbooks.pin(f"ceph-{flavour}.yml", "ceph_ansible_version", config)
        out.append(Source("ceph_ansible", ref, upstream=True))
    out.extend(_role_sources(base, config))
    return list(dict.fromkeys(out))


def _role_sources(base, config) -> list:
    """The pinned galaxy roles of base.yml `ansible_roles`, at their pins as
    shipped (verbatim: the image's requirements.yml uses them as git versions),
    in the repos etc/roles.yml names."""
    if "ansible_roles" not in base:
        raise SourceError("ansible_roles missing from release latest/base.yml")
    roles = base["ansible_roles"] or {}
    if not roles:
        return []
    repos = yaml.safe_load(source.read("release", "etc/roles.yml", config)) or {}
    out = []
    for name, pin in sorted(roles.items()):
        if name not in repos:
            raise SourceError(f"galaxy role {name} missing from release etc/roles.yml")
        out.append(Source(str(repos[name]), str(pin), upstream=True, external=True))
    return out


def _skip(src: Source, path: str) -> bool:
    top = path.split("/", 1)[0]
    if top in _PROSE_DIRS or path.endswith(_PROSE_SUFFIXES):
        return True
    return src.upstream and (
        top in _UPSTREAM_SKIP_DIRS or _UPSTREAM_TOX.fullmatch(path) is not None
    )


def iter_files(src: Source, config):
    """(repo-relative path, text) for every searchable file of `src`."""
    if src.external:
        owner, slug = src.repo.split("/", 1)
        root = source.github_tree_dir(owner, slug, src.ref, config)
    else:
        root = source.tree_dir(src.repo, src.ref, config)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in _WALK_SKIP_DIRS)
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            if os.path.islink(full):
                continue
            path = os.path.relpath(full, root).replace(os.sep, "/")
            if _skip(src, path):
                continue
            with open(full, "rb") as fh:
                body = fh.read()
            if b"\0" in body[:4096]:
                continue
            yield path, body.decode("utf-8", errors="ignore")


def scan(config, srcs=None) -> Corpus:
    """Read every source once and collect what it reads."""
    corpus = Corpus()
    for src in sources(config) if srcs is None else srcs:
        label = src.label(config)
        corpus.labels.append(label)
        for path, text in iter_files(src, config):
            corpus.names |= var_refs.reads(path, text)
            for call in var_refs.merge_calls(var_refs.code_text(path, text)):
                where = f"{label}:{path}"
                if not call.resolved:
                    corpus.unresolved.append(where)
                for kind, pattern in call.patterns:
                    corpus.patterns.append((kind, pattern, where))
    return corpus
