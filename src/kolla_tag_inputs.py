# The inputs of the tag resolution of check-kolla-images.py: what the
# kolla-ansible containers of a release are deployed from.
#
# Predicted mode (the kolla-ansible image does not exist yet, step 1 of the
# release) reconstructs what step 2 will build: upstream kolla-ansible at
# the series' ref with OSISM's patches applied, versions.yml rendered from
# container-image-kolla-ansible's template and the SBOM, the series'
# overlay, and osism/defaults at the defaults_version the inventory-
# reconciler build takes. Recorded mode (the image exists) reads what was
# built: the commits the image labels record, or, where a label is missing
# or unusable, the files of the image itself.
#
# Repos are read through osism_drift.source at commits, remotely (one
# GitHub tarball per repo and commit) or from local clones (--base-dir).
# The release tags are read from this checkout of osism/release
# (src/release_tags.py).

import atexit
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import jinja2
import yaml

import release_tags
from ansible_templar import SUPPORTED_ANSIBLE_CORE
from kolla_registry import SBOM_REPOSITORY, RegistryError, image_manifests
from osism_drift import source
from osism_drift.config import Config, Remote, SourceCfg
from osism_drift.http import SourceError, _get

KOLLA_ANSIBLE = "kolla_ansible"
TEMPLATES = "container_image_kolla_ansible"
DEFAULTS = "defaults"
KOLLA_ANSIBLE_IMAGE = "kolla-ansible"
INVENTORY_RECONCILER_IMAGE = "inventory-reconciler"
ARCHITECTURE = "amd64"

VERSIONS_TEMPLATE = "files/src/templates/versions.yml.j2"
VERSIONS_CONTEXT_KEYS = (
    "openstack_version",
    "openstack_previous_version",
    "openstackclient_version",
    "versions",
)
NEXT_URL = (
    "https://raw.githubusercontent.com/osism/release/main/next/"
    "kolla-ansible-v{version}.yml"
)
LABEL_SERIES = "de.osism.release.openstack"
LABEL_KOLLA_ANSIBLE = "de.osism.commit.kolla_ansible"
LABEL_RELEASE = "de.osism.commit.release"
LABEL_REVISION = "org.opencontainers.image.revision"
LABEL_SBOM = "de.osism.sbom"
LABEL_SBOM_DIGEST = "de.osism.sbom.digest"
LABEL_DEFAULTS = "de.osism.commit.defaults"
# The labels of the kolla-ansible image recorded mode reads (the label
# baseline, osism/issues#1463)
KOLLA_ANSIBLE_LABELS = (
    LABEL_SERIES,
    LABEL_KOLLA_ANSIBLE,
    LABEL_RELEASE,
    LABEL_REVISION,
    LABEL_SBOM,
    LABEL_SBOM_DIGEST,
)
BEFORE_BASELINE = (
    "it was built before the label baseline (osism/issues#1463); "
    "check it with --predicted"
)


class InputError(Exception):
    pass


@dataclass(frozen=True)
class Options:
    version: str  # the kolla-ansible image version, e.g. 0.20261008.0
    inventory_reconciler_version: str
    build: str  # the kolla build the script checks
    series: str  # the OpenStack version of the release tag kolla-v<build>
    kolla_ansible_ref: str | None = None
    defaults_ref: str | None = None
    predicted: bool = False
    base_dirs: tuple = ()
    remote_fallback: bool = False


@dataclass
class Inputs:
    mode: str
    series: str
    sbom: dict
    roles: dict  # role -> role defaults
    overlay: dict
    versions: dict
    defaults: list  # [(file name, values)] of osism/defaults all/
    sources: list = field(default_factory=list)  # [(input, description)]
    notes: list = field(default_factory=list)


class Registry:
    """Harbor access of the tag check: kolla images and SBOMs in the project
    kolla, the kolla-ansible and inventory-reconciler images in osism."""

    def __init__(self, kolla, osism):
        self.kolla = kolla
        self.osism = osism

    def sbom(self, series, reference):
        return self.kolla.read_sbom(series, reference)

    def sbom_exists(self, series, reference):
        repository = SBOM_REPOSITORY.format(openstack_version=series)
        return self.kolla.artifact(repository, reference) is not None

    def image(self, image, version):
        """(digest, labels) of osism/<image>:<version>, None if it does not exist.

        The tag is resolved to its digest once and the labels are read by that
        digest, from the amd64 image of an index, so a tag that moves meanwhile
        cannot change them.
        """
        artifact = self.osism.artifact(image, version)
        if artifact is None:
            return None
        digest = artifact["digest"]
        manifest = self.osism.manifest(image, digest).json()
        if "manifests" in manifest:
            entries = [
                entry
                for entry in image_manifests(manifest)
                if entry.get("platform", {}).get("architecture") == ARCHITECTURE
            ]
            if not entries:
                raise RegistryError(
                    f"osism/{image}:{version} has no {ARCHITECTURE} image"
                )
            manifest = self.osism.manifest(image, entries[0]["digest"]).json()
        config = self.osism.registry_get(
            image, f"blobs/{manifest['config']['digest']}"
        ).json()
        return digest, (config.get("config") or {}).get("Labels") or {}


def source_config(base_dirs=(), remote_fallback=False):
    """osism_drift config with the three source repos pinned: read at commits."""
    return Config(
        remote=Remote(
            "https://raw.githubusercontent.com/",
            "https://api.github.com/repos/",
            "main",
            "osism",
        ),
        release_version="latest",
        plugins={},
        sources={
            KOLLA_ANSIBLE: SourceCfg(owner="openstack"),
            TEMPLATES: SourceCfg(owner="osism"),
            DEFAULTS: SourceCfg(owner="osism"),
        },
        base_dirs=tuple(str(d) for d in base_dirs),
        remote_fallback=remote_fallback,
        archive=True,
    )


def load_yaml(data, what):
    try:
        return yaml.safe_load(data) or {}
    except yaml.YAMLError as e:
        raise InputError(f"{what} is not valid YAML: {e}") from e


def tree_file(root, path, what):
    target = Path(root) / path
    if not target.is_file():
        raise InputError(f"{path} not found in {what}")
    return target.read_text()


def release_file(ref, name, what):
    data = release_tags.read_latest(ref, name)
    if data is None:
        raise InputError(
            f"latest/{name} not found at {what} in osism/release (git fetch --tags)"
        )
    return data


def check_ansible_core(openstack_file, series):
    version = str(openstack_file.get("ansible_core_version") or "")
    if not version.startswith(SUPPORTED_ANSIBLE_CORE):
        raise InputError(
            f"latest/openstack-{series}.yml pins ansible-core {version or '(none)'}; "
            f"the tag check models the templating of ansible-core "
            f"{SUPPORTED_ANSIBLE_CORE}x only"
        )


def read_next(version):
    """next/kolla-ansible-v<version>.yml on main, as the image build reads it."""
    url = NEXT_URL.format(version=version)
    response = _get("fetching", url, ok=(404,))
    if response.status_code == 404:
        return None
    return load_yaml(response.content, url)


def select_sbom(version, read_next):
    """The kolla build whose SBOM the kolla-ansible build of version uses."""
    data = read_next(version)
    if data and data.get("openstack_sbom"):
        return (
            str(data["openstack_sbom"]).strip(),
            f"next/kolla-ansible-v{version}.yml on main",
        )
    return version, "the same version"


def note_other_build(build, options, notes):
    if build and build != options.build:
        notes.append(
            f"osism/kolla-ansible:{options.version} uses the SBOM of the kolla "
            f"build {build}, not {options.build}: compared with that build's "
            f"tags; the registry checks above cover {options.build} only, run "
            f"the script for {build} as well"
        )


def patches(templates_root, series):
    """The patches of the series in the order the image build applies them."""
    directory = Path(templates_root) / "patches" / series
    if not directory.is_dir():
        return []
    return [
        (path.relative_to(directory).as_posix(), path.read_bytes())
        for path in sorted(directory.rglob("*.patch"), key=lambda p: p.as_posix())
    ]


def patched_tree(root, patch_list):
    """A copy of the tree at root with the patches applied, as the build does."""
    if shutil.which("patch") is None:
        raise InputError("the patch program is not installed")
    work = Path(tempfile.mkdtemp(prefix="kolla-tags-"))
    atexit.register(shutil.rmtree, work, ignore_errors=True)
    tree = work / "tree"
    shutil.copytree(root, tree, symlinks=True, ignore=shutil.ignore_patterns(".git"))
    for name, data in patch_list:
        for extra in (["--dry-run"], []):
            result = subprocess.run(
                ["patch", "--forward", "--batch", "-p1", *extra],
                cwd=tree,
                input=data,
                capture_output=True,
            )
            if result.returncode != 0:
                output = result.stdout.decode(errors="replace").strip()
                raise InputError(f"the patch {name} does not apply: {output}")
    return tree


def role_defaults_from_tree(root):
    roles = {}
    for path in sorted((Path(root) / "ansible" / "roles").glob("*/defaults/main.yml")):
        roles[path.parent.parent.name] = load_yaml(path.read_bytes(), str(path))
    if not roles:
        raise InputError(f"no role defaults in {root}")
    return roles


def overlay_from_tree(templates_root, series):
    path = Path(templates_root) / "overlays" / series / "kolla-ansible.yml"
    return load_yaml(path.read_bytes(), str(path)) if path.is_file() else {}


def defaults_files_from_tree(root):
    files = [
        (path.name, load_yaml(path.read_bytes(), str(path)))
        for path in sorted((Path(root) / "all").glob("*.yml"))
    ]
    if not files:
        raise InputError(f"no all/*.yml in {root}")
    return files


def render_versions(template, series, openstack_file, sbom_versions):
    """versions.yml as files/src/render-versions.py renders it."""
    try:
        context = {
            "openstack_version": series,
            "openstack_previous_version": openstack_file["openstack_previous_version"],
            "openstackclient_version": openstack_file["docker_images"][
                "openstackclient"
            ],
            "versions": sbom_versions or {},
        }
    except (KeyError, TypeError) as e:
        raise InputError(f"latest/openstack-{series}.yml lacks {e}") from e
    assert tuple(context) == VERSIONS_CONTEXT_KEYS
    text = jinja2.Environment().from_string(template).render(context)
    return load_yaml(text, "the rendered versions.yml")


def predicted_defaults(options, config):
    """osism/defaults as the inventory-reconciler build of the version takes it."""
    if options.defaults_ref:
        ref, why = options.defaults_ref, "--defaults-ref"
    else:
        tag = f"inventory-reconciler-v{options.inventory_reconciler_version}"
        commit, _ = release_tags.release_commit(tag)
        ref = release_file(commit, "base.yml", tag).get("defaults_version")
        if not ref:
            raise InputError(f"latest/base.yml at {tag} has no defaults_version")
        why = f"defaults_version at {tag}"
    commit = source.resolve_commit(DEFAULTS, str(ref), config)
    files = defaults_files_from_tree(source.tree_dir(DEFAULTS, commit, config))
    return files, f"osism/defaults {ref} @ {commit[:12]} ({why})"


def predicted(options, registry, read_next, config):
    notes = []
    series = options.series
    release_tag = f"kolla-ansible-v{options.version}"
    release, _ = release_tags.release_commit(release_tag)
    openstack_file = release_file(release, f"openstack-{series}.yml", release_tag)
    check_ansible_core(openstack_file, series)
    build, reason = select_sbom(options.version, read_next)
    note_other_build(build, options, notes)
    sbom = registry.sbom(series, build)

    kolla_ansible_ref = source.release_to_ref(KOLLA_ANSIBLE, series, config)
    kolla_ansible = source.resolve_commit(KOLLA_ANSIBLE, kolla_ansible_ref, config)
    templates_ref = options.kolla_ansible_ref or (
        f"v{options.version}"
        if source.ref_exists(TEMPLATES, f"v{options.version}", config)
        else "main"
    )
    templates_commit = source.resolve_commit(TEMPLATES, templates_ref, config)
    templates = source.tree_dir(TEMPLATES, templates_commit, config)
    patch_list = patches(templates, series)
    roles = role_defaults_from_tree(
        patched_tree(source.tree_dir(KOLLA_ANSIBLE, kolla_ansible, config), patch_list)
    )
    versions = render_versions(
        tree_file(
            templates,
            VERSIONS_TEMPLATE,
            f"container-image-kolla-ansible {templates_ref}",
        ),
        series,
        openstack_file,
        sbom.get("versions"),
    )
    defaults, defaults_source = predicted_defaults(options, config)
    templates_source = (
        f"osism/container-image-kolla-ansible {templates_ref} @ {templates_commit[:12]}"
    )
    return Inputs(
        mode="predicted",
        series=series,
        sbom=sbom,
        roles=roles,
        overlay=overlay_from_tree(templates, series),
        versions=versions,
        defaults=defaults,
        sources=[
            ("SBOM", f"kolla/release/{series}/sbom:{build} ({reason})"),
            (
                "role defaults",
                f"openstack/kolla-ansible {kolla_ansible_ref} @ {kolla_ansible[:12]}, "
                f"{len(patch_list)} patches (patches/{series})",
            ),
            (
                "versions.yml",
                f"{templates_source}, osism/release {release_tag} @ {release[:12]}",
            ),
            ("overlay", templates_source),
            ("defaults", defaults_source),
        ],
        notes=notes,
    )


def label_commit(repo, labels, label, image, config):
    """The commit a label names, resolved in repo."""
    value = labels[label]
    try:
        return source.resolve_commit(repo, value, config)
    except SourceError as e:
        raise InputError(
            f"label {label}={value} of {image} names no commit of {repo}: {e}"
        ) from e


def recorded_defaults(options, registry, config, notes):
    """osism/defaults as the inventory-reconciler image of the version ships it."""
    if options.defaults_ref:
        return predicted_defaults(options, config)
    version = options.inventory_reconciler_version
    image = f"osism/{INVENTORY_RECONCILER_IMAGE}:{version}"
    found = registry.image(INVENTORY_RECONCILER_IMAGE, version)
    if found is None:
        notes.append(
            f"{image} does not exist yet; osism/defaults taken as its build will take it"
        )
        return predicted_defaults(options, config)
    digest, labels = found
    if not labels.get(LABEL_DEFAULTS):
        raise InputError(f"{image} has no label {LABEL_DEFAULTS}: {BEFORE_BASELINE}")
    commit = label_commit(DEFAULTS, labels, LABEL_DEFAULTS, image, config)
    files = defaults_files_from_tree(source.tree_dir(DEFAULTS, commit, config))
    return files, f"osism/defaults @ {commit[:12]} (label of {image}@{digest})"


def recorded(options, registry, found, config):
    """The inputs the kolla-ansible image was built from, by its labels."""
    notes = []
    digest, labels = found
    image = f"osism/{KOLLA_ANSIBLE_IMAGE}:{options.version}"
    missing = [label for label in KOLLA_ANSIBLE_LABELS if not labels.get(label)]
    if missing:
        raise InputError(
            f"{image} has no label {', '.join(missing)}: {BEFORE_BASELINE}"
        )
    series = labels[LABEL_SERIES]

    sbom_digest = labels[LABEL_SBOM_DIGEST]
    if not registry.sbom_exists(series, sbom_digest):
        raise InputError(
            f"Harbor no longer serves the SBOM {sbom_digest} that {image} was built with"
        )
    sbom = registry.sbom(series, sbom_digest)
    note_other_build(labels[LABEL_SBOM], options, notes)

    release = labels[LABEL_RELEASE]
    if release_tags.git("cat-file", "-e", f"{release}^{{commit}}") is None:
        raise InputError(
            f"label {LABEL_RELEASE}={release} of {image} is not in this checkout "
            "of osism/release (git fetch)"
        )
    openstack_file = release_file(
        release, f"openstack-{series}.yml", f"label {LABEL_RELEASE}"
    )
    check_ansible_core(openstack_file, series)

    kolla_ansible = label_commit(
        KOLLA_ANSIBLE, labels, LABEL_KOLLA_ANSIBLE, image, config
    )
    revision = label_commit(TEMPLATES, labels, LABEL_REVISION, image, config)
    templates = source.tree_dir(TEMPLATES, revision, config)
    patch_list = patches(templates, series)
    roles = role_defaults_from_tree(
        patched_tree(source.tree_dir(KOLLA_ANSIBLE, kolla_ansible, config), patch_list)
    )
    versions = render_versions(
        tree_file(
            templates,
            VERSIONS_TEMPLATE,
            f"container-image-kolla-ansible {revision[:12]}",
        ),
        series,
        openstack_file,
        sbom.get("versions"),
    )
    defaults, defaults_source = recorded_defaults(options, registry, config, notes)
    templates_source = f"osism/container-image-kolla-ansible @ {revision[:12]}"
    return Inputs(
        mode="recorded",
        series=series,
        sbom=sbom,
        roles=roles,
        overlay=overlay_from_tree(templates, series),
        versions=versions,
        defaults=defaults,
        sources=[
            ("image", f"{image}@{digest}"),
            (
                "SBOM",
                f"kolla/release/{series}/sbom@{sbom_digest} (build {labels[LABEL_SBOM]})",
            ),
            (
                "role defaults",
                f"openstack/kolla-ansible @ {kolla_ansible[:12]}, "
                f"{len(patch_list)} patches (patches/{series})",
            ),
            ("versions.yml", f"{templates_source}, osism/release @ {release[:12]}"),
            ("overlay", templates_source),
            ("defaults", defaults_source),
        ],
        notes=notes,
    )


def gather(options, registry, read_next=read_next):
    """The inputs of the tag resolution for options."""
    config = source_config(options.base_dirs, options.remote_fallback)
    if not options.predicted:
        found = registry.image(KOLLA_ANSIBLE_IMAGE, options.version)
        if found is not None:
            return recorded(options, registry, found, config)
    return predicted(options, registry, read_next, config)
