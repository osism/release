#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = [
#     "requests",
#     "pyyaml",
# ]
# ///
#
# Helper for scripts/generate-release-changelog.sh
#
# Subcommands:
#
#   input     Build the input document for the release notes generation:
#             the version changes between two release versions plus the
#             CHANGELOG.md excerpts of all changed OSISM components (also
#             those pinned in a requirements file of another component,
#             e.g. netbox-manager in python-osism), the commits of
#             components installed from a branch at image build time
#             (openstack-project-manager in the osism image) together
#             with the CHANGELOG.md sections of the tags created in that
#             range, and,
#             when docker_images.kolla_ansible changed, the upstream
#             openstack/kolla-ansible changes pulled in by the image
#             rebuild (commit subjects and reno release notes) together
#             with the added/removed downstream patches of the
#             kolla-ansible image; when docker_images.kolla changed, the
#             downstream patch changes of the kolla images as well, the
#             OpenStack Security Advisories (OSSA pages of osism.github.io)
#             whose fixes the kolla images contain in addition to the
#             previous release, plus the effective OSISM kolla defaults
#             (osism/defaults) as a reference for checking configuration
#             advice
#   sanitize  Sanitize a model-generated release notes body (stdin to
#             stdout): strip any preamble before the first "### " heading,
#             demote forbidden "# "/"## " headings, move a "### MetalBox"
#             subsection to the end, collapse blank lines.
#             Code fences are honored so that e.g. shell comments in
#             examples are never touched.
#   insert    Insert a generated section into a release notes page of
#             osism.github.io: adds a plain row to the release table
#             (padded to the header column widths, no anchor link) and the
#             section "## <version>" (no date suffix) before the first
#             existing release section, or directly after the release
#             table if the page has no release section yet.

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta

import requests
import yaml

BASE_SECTIONS = [
    "osism_projects",
    "docker_images",
    "ansible_roles",
    "ansible_collections",
]

MONTHS = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]

CHANGELOG_URL = "https://raw.githubusercontent.com/{repo}/{branch}/CHANGELOG.md"

CHANGELOG_HEADING_RE = re.compile(r"^## \[([^]]+)\]")

# Release section headings: "## 10.1.0" (current style) as well as the
# older "## 9.5.0 (20251130)" style and rc suffixes like "## 10.0.0-rc.1"
RELEASE_HEADING_RE = re.compile(
    r"^## (\d+\.\d+\.\d+[0-9A-Za-z.+-]*?)(?: \(\d{8}\))?\s*$"
)

FENCE_RE = re.compile(r"^\s*(```|~~~)")

# The kolla-ansible container image clones this repository at build time
# (branch stable/<openstack_version>); an image rebuild therefore pulls in
# all upstream changes since the previous build
UPSTREAM_KOLLA_ANSIBLE = "https://github.com/openstack/kolla-ansible"

# Repositories carrying downstream patches (backports of upstream changes)
# in patches/<openstack_version>/; a patch is removed again once the
# change landed upstream
KOLLA_ANSIBLE_IMAGE_REPO = "osism/container-image-kolla-ansible"
KOLLA_IMAGES_REPO = "osism/container-images-kolla"

# The effective kolla defaults OSISM ships (osism/defaults, pinned as
# defaults_version in base.yml). They override the upstream kolla-ansible
# defaults and are included in the input as a reference so that
# configuration advice is checked against what OSISM actually sets. The
# files are applied in lexicographic order, later files win.
OSISM_DEFAULTS_REPO = "osism/defaults"
OSISM_KOLLA_DEFAULTS_FILES = [
    "all/099-kolla.yml",
]

# The security advisories published on osism.tech: one page per OpenStack
# Security Advisory (OSSA) with a property table (Date, CVE, Affected
# Project), a Summary section and links to the container-images-kolla
# commits and pull requests that ship the fix as downstream patches
SITE_REPO = "osism/osism.github.io"
SITE_SECURITY_DIR = "docs/appendix/security"
ADVISORY_FILE_RE = re.compile(r"^(ossa-\d{4}-\d{3})\.md$")
ADVISORY_REF_RE = re.compile(
    r"github\.com/osism/container-images-kolla/(commit|pull)/([0-9a-f]{7,40}|\d+)"
)
CVE_RE = re.compile(r"CVE-\d{4}-\d{4,}")
# Link target of an advisory page, relative to docs/release-notes/osism-<major>.md
ADVISORY_LINK_PREFIX = "../appendix/security/"

# The MetalBox-only changes are the last subsection of a release section
METALBOX_HEADING_RE = re.compile(r"^### MetalBox\b", re.IGNORECASE)

# Caps for the upstream kolla-ansible input section; exceeding entries are
# dropped with an explicit note so that nothing is truncated silently
MAX_UPSTREAM_NOTES = 100
MAX_UPSTREAM_COMMITS = 400

# GitHub commit listings are paginated; the maximum page size
COMMITS_PER_PAGE = 100


def warn(message):
    print(f"Warning: {message}", file=sys.stderr)


def info(message):
    print(message, file=sys.stderr)


def load_versions(release):
    """Flatten <release>/base.yml into {(section, name): version}."""
    with open(f"{release}/base.yml") as fp:
        data = yaml.safe_load(fp)

    versions = {}
    for key, value in data.items():
        if key == "manager_version":
            continue
        if key in BASE_SECTIONS and isinstance(value, dict):
            for name, version in value.items():
                versions[(key, str(name))] = str(version)
        elif key.endswith("_version"):
            versions[("", key[: -len("_version")])] = str(value)
        else:
            versions[("", key)] = str(value)
    return versions


def diff_versions(previous, current):
    changed = []
    added = []
    removed = []
    for key in sorted(current):
        if key not in previous:
            added.append((key, current[key]))
        elif previous[key] != current[key]:
            changed.append((key, previous[key], current[key]))
    for key in sorted(previous):
        if key not in current:
            removed.append((key, previous[key]))
    return changed, added, removed


def version_key(version):
    """Sort key for OSISM version strings like v0.20260615.0, or None."""
    version = version.lstrip("v")
    if not re.fullmatch(r"\d+(\.\d+)*", version):
        return None
    return tuple(int(part) for part in version.split("."))


def fetch_changelog(repo):
    for branch in ("main", "master"):
        url = CHANGELOG_URL.format(repo=repo, branch=branch)
        try:
            response = requests.get(url, timeout=30)
        except requests.RequestException as e:
            warn(f"Fetching {url} failed: {e}")
            return None
        if response.status_code == 200:
            return response.text
    return None


def component_version(versions, name):
    """Return the version of a component regardless of its base.yml section."""
    for (_, n), version in versions.items():
        if n == name:
            return version
    return None


def resolve_requirement_pin(repo, ref, path, package):
    """Read the ==-pinned version of a package from a requirements file.

    The file is fetched from the given repository at the given tag/ref;
    a leading v is added to the ref if needed (base.yml pins osism as
    0.20260615.0 while python-osism tags are v0.20260615.0).
    """
    for tag in (f"v{ref.lstrip('v')}", ref):
        url = f"https://raw.githubusercontent.com/{repo}/{tag}/{path}"
        try:
            response = requests.get(url, timeout=30)
        except requests.RequestException as e:
            warn(f"Fetching {url} failed: {e}")
            return None
        if response.status_code == 200:
            for line in response.text.splitlines():
                m = re.match(rf"{re.escape(package)}\s*==\s*(\S+)", line)
                if m:
                    return m.group(1)
            warn(f"No {package}== pin found in {url}")
            return None
    warn(f"Could not fetch {path} of {repo} at {ref}")
    return None


def run_git(args, cwd=None):
    """Run git and return its stdout, or None if the command failed."""
    try:
        result = subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True)
    except OSError as e:
        warn(f"git {' '.join(args)}: {e}")
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def github_headers():
    """Authorization header for the GitHub API, if a token is available.

    Uses GITHUB_TOKEN/GH_TOKEN or the token of an authenticated gh; the
    unauthenticated rate limit is easily exhausted otherwise.
    """
    if not hasattr(github_headers, "cached"):
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        if not token:
            try:
                result = subprocess.run(
                    ["gh", "auth", "token"], capture_output=True, text=True
                )
                if result.returncode == 0:
                    token = result.stdout.strip()
            except OSError:
                pass
        github_headers.cached = {"Authorization": f"Bearer {token}"} if token else {}
    return github_headers.cached


def release_openstack_version(tag_prefix, image_version):
    """Resolve the OpenStack version used by a kolla(-ansible) image version.

    The image builds check out the tag <prefix>-v<image version> of this
    repository; latest/openstack.yml is a symlink to the
    openstack-<version>.yml of the OpenStack version in use. Read from the
    local repository, with the GitHub raw contents as fallback (both
    return the symlink target as content).
    """
    tag = f"{tag_prefix}-v{image_version.lstrip('v')}"
    content = run_git(["show", f"{tag}:latest/openstack.yml"])
    if content is None:
        url = (
            "https://raw.githubusercontent.com/osism/release/"
            f"{tag}/latest/openstack.yml"
        )
        try:
            response = requests.get(url, timeout=30)
        except requests.RequestException as e:
            warn(f"Fetching {url} failed: {e}")
            return None
        if response.status_code != 200:
            warn(f"Could not fetch {url} (HTTP {response.status_code})")
            return None
        content = response.text
    m = re.fullmatch(r"openstack-(\S+)\.yml", content.strip())
    if m:
        return m.group(1)
    try:
        return str(yaml.safe_load(content)["openstack_version"])
    except (yaml.YAMLError, KeyError, TypeError):
        warn(f"No openstack_version found in {tag}:latest/openstack.yml")
        return None


def github_commit_time(repo, ref):
    """Committer date of a commit, tag or branch head via the GitHub API."""
    url = f"https://api.github.com/repos/{repo}/commits/{ref}"
    try:
        response = requests.get(url, headers=github_headers(), timeout=30)
        if response.status_code == 200:
            return response.json()["commit"]["committer"]["date"]
    except (requests.RequestException, KeyError, ValueError) as e:
        warn(f"Fetching {url} failed: {e}")
    return None


def version_date(version):
    """End of the day encoded in an OSISM version like v0.20260615.0."""
    m = re.fullmatch(r"v?\d+\.(\d{4})(\d{2})(\d{2})\.\d+", version)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}T23:59:59+00:00"
    return None


def kolla_ansible_build_time(image_version):
    """Approximate when a kolla-ansible image version was built.

    The image build checks out the tag kolla-ansible-v<image version> of
    this repository, which is created for the build; the committer date of
    the tagged commit is the closest timestamp available. Falls back to
    the GitHub API and finally to the date encoded in the version number.
    """
    tag = f"kolla-ansible-v{image_version.lstrip('v')}"
    out = run_git(["log", "-1", "--format=%cI", tag])
    if out and out.strip():
        return out.strip()

    when = github_commit_time("osism/release", tag)
    if when:
        return when

    when = version_date(image_version)
    if when:
        warn(
            f"Tag {tag} not found, falling back to the date encoded "
            f"in {image_version}"
        )
        return when
    warn(f"Cannot determine a build time for kolla-ansible {image_version}")
    return None


def image_build_date(tag_prefix, version):
    """Build date (YYYY-MM-DD) of a kolla(-ansible) image version.

    The date is encoded in the version number (0.20261001.0 was built on
    2026-10-01); the committer date of the <prefix>-v<version> tag of
    this repository is the fallback (the tag may predate the build by a
    few days), the GitHub API the last resort.
    """
    when = version_date(version)
    if when:
        return when[:10]
    tag = f"{tag_prefix}-v{version.lstrip('v')}"
    out = run_git(["log", "-1", "--format=%cI", tag])
    if out and out.strip():
        return out.strip()[:10]
    when = github_commit_time("osism/release", tag)
    if when:
        return when[:10]
    warn(f"Cannot determine a build date for {tag_prefix} {version}")
    return None


def source_build_time(repo, version):
    """Approximate when the container image of an OSISM component was built.

    The image is built from the tag v<version> of its source repository
    (e.g. python-osism for the osism image); the committer date of the
    tagged commit is the closest timestamp available without the build
    logs. Falls back to the date encoded in the version number.
    """
    tag = f"v{version.lstrip('v')}"
    when = github_commit_time(repo, tag)
    if when:
        return when
    when = version_date(version)
    if when:
        warn(
            f"Tag {tag} of {repo} not found, falling back to the date encoded "
            f"in {version}"
        )
        return when
    warn(f"Cannot determine a build time for {repo} {version}")
    return None


def list_branch_commits(repo, branch, since, until, limit=None):
    """Non-merge commits of a branch between two times, newest first.

    Lists the commits of the branch in (since, until] via the GitHub API
    (either bound may be None); returns [(sha, subject)] or None if the
    listing failed. With a limit, at most that many commits are returned.
    """
    url = f"https://api.github.com/repos/{repo}/commits"
    commits = []
    page = 1
    while True:
        params = {"sha": branch, "per_page": COMMITS_PER_PAGE, "page": page}
        if since:
            params["since"] = since
        if until:
            params["until"] = until
        try:
            response = requests.get(
                url, headers=github_headers(), params=params, timeout=30
            )
        except requests.RequestException as e:
            warn(f"Fetching {url} failed: {e}")
            return None
        if response.status_code != 200:
            warn(
                f"Could not list the commits of {repo} {branch} "
                f"(HTTP {response.status_code})"
            )
            return None
        data = response.json()
        for entry in data:
            if len(entry.get("parents", [])) > 1:
                continue
            message = entry["commit"]["message"].strip()
            subject = message.splitlines()[0].strip() if message else ""
            commits.append((entry["sha"], subject))
            if limit is not None and len(commits) >= limit:
                return commits
        if len(data) < COMMITS_PER_PAGE:
            return commits
        page += 1


def list_tags(repo):
    """The newest tags of a repository as [(name, commit sha)], or None."""
    url = f"https://api.github.com/repos/{repo}/tags"
    try:
        response = requests.get(
            url, headers=github_headers(), params={"per_page": 100}, timeout=30
        )
    except requests.RequestException as e:
        warn(f"Fetching {url} failed: {e}")
        return None
    if response.status_code != 200:
        warn(f"Could not list the tags of {repo} (HTTP {response.status_code})")
        return None
    return [(entry["name"], entry["commit"]["sha"]) for entry in response.json()]


def branch_release_lines(repo, commits):
    """CHANGELOG.md sections of the tags created among the given commits.

    A component installed from a branch may still tag releases and keep
    a CHANGELOG.md; the sections of the tags pointing at one of the
    listed commits describe those commits in more detail. Returns the
    markdown lines or None if no tag lies in the range or the changelog
    has no matching sections.
    """
    tags = list_tags(repo)
    if not tags:
        return None
    shas = {sha for sha, _ in commits}
    released = [(i, name) for i, (name, sha) in enumerate(tags) if sha in shas]
    if not released:
        return None
    newest = released[0][1]
    oldest_index = released[-1][0]
    previous = tags[oldest_index + 1][0] if oldest_index + 1 < len(tags) else "0"
    changelog = fetch_changelog(repo)
    if changelog is None:
        warn(f"No CHANGELOG.md found for {repo}")
        return None
    sections = extract_sections(changelog, previous, newest)
    if sections is None:
        warn(f"No CHANGELOG.md sections found for {repo} in ({previous}, {newest}]")
        return None
    names = ", ".join(name for _, name in reversed(released))
    return [
        f"The commits up to {newest} were released as {names}; these "
        "CHANGELOG.md sections describe them in more detail and take "
        "precedence over the commit subjects. Commits after the last release "
        "are unreleased changes of the branch that are nevertheless part of "
        "the image:",
        "",
        sections,
    ]


def branch_component_change(name, cfg, source_repo, old_source, new_source):
    """Resolve the changes of a component installed from a branch.

    Some components are not pinned anywhere: the container image of the
    source component clones a branch of their repository when it is built
    (e.g. openstack-project-manager in the osism image). The commits of
    that branch between the build times of the two source versions stand
    in for a version range. Returns (table row, input section lines) or
    None if nothing changed or the state cannot be determined.
    """
    repo = cfg["repository"]
    branch = cfg["branch"]
    source = cfg["source"]
    info(
        f"Resolving {name} ({repo} {branch}) from the build times of "
        f"{source_repo} {old_source} and {new_source}..."
    )
    since = source_build_time(source_repo, old_source)
    until = source_build_time(source_repo, new_source)
    if since is None or until is None:
        return None
    commits = list_branch_commits(repo, branch, since, until)
    if commits is None:
        return None
    if not commits:
        info(f"{name}: no commits on {branch} between {since} and {until}")
        return None
    head = commits[0][0]
    base = list_branch_commits(repo, branch, None, since, limit=1)
    base = base[0][0] if base else None
    old = base[:10] if base else "?"
    new = head[:10]

    lines = [
        f"### {repo} ({branch}, {old} -> {new})",
        "",
        f"{name} is not pinned: the {source} container image installs it "
        f"from the {branch} branch of {repo} when the image is built. These "
        f"are the commits of {branch} between the builds of {source} "
        f"{old_source} and {new_source} ({since} -> {until}); the install "
        "background is context only and must not be mentioned in the "
        "release notes:",
        "",
    ]
    for _, subject in commits:
        # "(#123)" of a squash merge -> "(org/repo#123)", the reference
        # style of the component changelogs
        subject = re.sub(r"\(#(\d+)\)\s*$", rf"({repo}#\1)", subject)
        lines.append(f"- {subject}")
    released = branch_release_lines(repo, commits)
    if released:
        lines.append("")
        lines.extend(released)
    row = (f"{name} (via {source}, {branch})", repo, old, new)
    return row, lines


def upstream_kolla_ansible_branch(openstack_version):
    """Return the upstream branch/ref used for an OpenStack version.

    Mirrors the branch selection of the container-image-kolla-ansible
    build: master builds from master, stable releases from stable/<x>;
    branches of unmaintained releases move to unmaintained/<x> or are
    replaced by an <x>-eol tag.
    """
    if openstack_version == "master":
        return "master"
    for ref in (
        f"stable/{openstack_version}",
        f"unmaintained/{openstack_version}",
        f"{openstack_version}-eol",
    ):
        out = run_git(
            [
                "ls-remote",
                UPSTREAM_KOLLA_ANSIBLE,
                f"refs/heads/{ref}",
                f"refs/tags/{ref}",
            ]
        )
        if out and out.strip():
            return ref
    warn(f"No upstream kolla-ansible branch found for {openstack_version}")
    return None


def list_patch_files(repo, image_version, openstack_version):
    """List the applied downstream patches of an image version.

    Returns the .patch files below patches/<openstack_version>/ of the
    repository at the image version tag, relative to that directory
    (disabled patches are not applied and not listed), or None if the
    tree cannot be fetched.
    """
    ref = f"v{image_version.lstrip('v')}"
    url = f"https://api.github.com/repos/{repo}/git/trees/{ref}?recursive=1"
    try:
        response = requests.get(url, headers=github_headers(), timeout=30)
    except requests.RequestException as e:
        warn(f"Fetching {url} failed: {e}")
        return None
    if response.status_code != 200:
        warn(
            f"Could not list the tree of {repo} at {ref} (HTTP {response.status_code})"
        )
        return None
    data = response.json()
    if data.get("truncated"):
        warn(f"Tree listing of {repo} at {ref} is truncated")
    prefix = f"patches/{openstack_version}/"
    return sorted(
        entry["path"][len(prefix) :]
        for entry in data.get("tree", [])
        if entry.get("type") == "blob"
        and entry["path"].startswith(prefix)
        and entry["path"].endswith(".patch")
    )


def list_mirror_layer_files(repo, version):
    """List the all/001-* kolla defaults mirror layer of `repo` at `version`.

    The layer may be split per upstream service, so list it from the tree rather
    than hardcoding names.
    Sorted, because all/ is merged in lexical filename order and later files win.

    Returns None if the listing fails or is truncated, and [] only when the
    listing succeeded and found no 001-* file. The caller must treat None as
    fatal: this section is labelled "the effective kolla defaults" and every
    configuration recommendation is checked against it, so publishing it without
    the mirror layer is worse than not publishing it at all.
    """
    url = f"https://api.github.com/repos/{repo}/git/trees/{version}?recursive=1"
    try:
        response = requests.get(url, headers=github_headers(), timeout=30)
    except requests.RequestException as e:
        warn(f"Fetching {url} failed: {e}")
        return None
    if response.status_code != 200:
        warn(
            f"Could not list the tree of {repo} at {version} "
            f"(HTTP {response.status_code})"
        )
        return None
    data = response.json()
    if data.get("truncated"):
        # Not the partial tree: a short layer would render a plausible but
        # incomplete "effective defaults" section. None, not [], so the caller
        # drops the whole section rather than publishing it without the layer.
        warn(f"Tree listing of {repo} at {version} is truncated")
        return None
    return sorted(
        entry["path"]
        for entry in data.get("tree", [])
        if entry.get("type") == "blob"
        and entry["path"].startswith("all/001-")
        and entry["path"].endswith(".yml")
    )


def downstream_patch_lines(repo, old, old_release, new, new_release):
    """Markdown lines for the downstream patch changes of an image.

    Diffs the applied patches below patches/<openstack_version>/ between
    the two image version tags; case-only renames are ignored. Returns
    None when nothing changed or a tree listing is unavailable.
    """
    info(f"Diffing downstream patches of {repo} ({old} -> {new})...")
    old_patches = list_patch_files(repo, old, old_release)
    new_patches = list_patch_files(repo, new, new_release)
    if old_patches is None or new_patches is None:
        return None
    old_map = {path.lower(): path for path in old_patches}
    new_map = {path.lower(): path for path in new_patches}
    added = [new_map[key] for key in sorted(new_map) if key not in old_map]
    removed = [old_map[key] for key in sorted(old_map) if key not in new_map]
    if not added and not removed:
        info(f"No downstream patch changes in {repo}")
        return None

    lines = [
        f"Applied downstream patches of {repo} "
        f"(patches/{old_release} at v{old.lstrip('v')} compared with "
        f"patches/{new_release} at v{new.lstrip('v')}); these lists are "
        "context for reconciliation only, not content for the release notes:",
        "",
    ]
    for heading, patches in (("Added patches:", added), ("Removed patches:", removed)):
        if patches:
            lines.append(heading)
            lines.append("")
            for patch in patches:
                lines.append(f"- {patch}")
            lines.append("")
    return lines[:-1]


def kolla_ansible_upstream_section(previous, current):
    """Markdown input section for the kolla-ansible changes of a release.

    Returns the upstream commit subjects and reno release notes that the
    kolla-ansible image rebuild pulled in between the build times of the
    two image versions, together with the added/removed downstream
    patches of the image, or None if docker_images.kolla_ansible did not
    change or nothing can be determined.
    """
    key = ("docker_images", "kolla_ansible")
    old = previous.get(key)
    new = current.get(key)
    if not old or not new or old == new:
        info("kolla_ansible image unchanged, skipping the upstream analysis")
        return None

    old_release = release_openstack_version("kolla-ansible", old)
    new_release = release_openstack_version("kolla-ansible", new)
    if old_release is None or new_release is None:
        return None

    parts = [f"## kolla-ansible upstream changes (OpenStack {new_release})", ""]

    if old_release != new_release:
        parts.append(
            f"The kolla-ansible container image ({old} -> {new}) switched "
            f"from OpenStack {old_release} to OpenStack {new_release}. A "
            "commit-level upstream diff is not meaningful across OpenStack "
            "releases; describe the OpenStack upgrade itself instead."
        )
        parts.append("")
    else:
        upstream = upstream_change_lines(old, new, new_release)
        if upstream:
            parts.extend(upstream)
            parts.append("")

    patches = downstream_patch_lines(
        KOLLA_ANSIBLE_IMAGE_REPO, old, old_release, new, new_release
    )
    if patches:
        parts.append("### Downstream patch changes")
        parts.append("")
        parts.extend(patches)
        parts.append("")

    if len(parts) <= 2:
        return None
    return "\n".join(parts).rstrip()


def kolla_images_patch_section(previous, current):
    """Markdown input section for the kolla images downstream patches.

    The kolla service images built by container-images-kolla carry
    downstream patches as well; return their changes between the two
    docker_images.kolla versions, or None if the image did not change or
    the patches did not.
    """
    key = ("docker_images", "kolla")
    old = previous.get(key)
    new = current.get(key)
    if not old or not new or old == new:
        return None

    old_release = release_openstack_version("kolla", old)
    new_release = release_openstack_version("kolla", new)
    if old_release is None or new_release is None:
        return None

    patches = downstream_patch_lines(
        KOLLA_IMAGES_REPO, old, old_release, new, new_release
    )
    if not patches:
        return None
    return "\n".join(
        [
            f"## kolla images downstream patch changes (OpenStack {new_release})",
            "",
        ]
        + patches
    )


def osism_kolla_defaults_section(previous, current):
    """Reference input section with the effective OSISM kolla defaults.

    Configuration advice must be checked against the kolla defaults OSISM
    itself ships: include the osism/defaults kolla files of the current
    release. Only added when the kolla images, kolla-ansible or the
    defaults changed; returns None otherwise or when nothing could be
    fetched.
    """
    relevant = any(
        previous.get(key) != current.get(key)
        for key in (
            ("docker_images", "kolla_ansible"),
            ("docker_images", "kolla"),
            ("", "defaults"),
        )
    )
    if not relevant:
        return None
    version = component_version(current, "defaults")
    if not version:
        warn("No defaults version found in base.yml")
        return None

    out = [
        "## OSISM kolla defaults (reference)",
        "",
        f"The effective kolla defaults of this release ({OSISM_DEFAULTS_REPO} "
        f"{version}). They override the upstream kolla-ansible defaults; "
        "within this list, later files override earlier ones "
        "(all/099-kolla.yml wins over the all/001-*.yml mirror layer). Check "
        "every configuration recommendation against these values.",
        "",
    ]
    layer = list_mirror_layer_files(OSISM_DEFAULTS_REPO, version)
    if layer is None:
        warn(
            "Could not list the all/001-* mirror layer; omitting the OSISM kolla "
            "defaults section rather than publishing it without the layer"
        )
        return None
    if not layer:
        warn(f"No all/001-* mirror layer found in {OSISM_DEFAULTS_REPO} at {version}")
    found = False
    for path in layer + OSISM_KOLLA_DEFAULTS_FILES:
        info(f"Fetching {path} of {OSISM_DEFAULTS_REPO} at {version}...")
        url = (
            f"https://raw.githubusercontent.com/{OSISM_DEFAULTS_REPO}/"
            f"{version}/{path}"
        )
        try:
            response = requests.get(url, timeout=30)
        except requests.RequestException as e:
            # Fatal, not skippable: a section missing one of the files it claims
            # to list is indistinguishable from a complete one to a reader.
            warn(f"Fetching {url} failed: {e}; omitting the section")
            return None
        if response.status_code != 200:
            warn(
                f"Could not fetch {url} (HTTP {response.status_code}); omitting the section"
            )
            return None
        found = True
        out.append(f"### {path}")
        out.append("")
        out.append("```yaml")
        out.append(response.text.rstrip())
        out.append("```")
        out.append("")
    if not found:
        return None
    return "\n".join(out).rstrip()


def list_advisory_pages(site_dir=None):
    """Return [(id, text)] of the OSSA pages of osism.github.io.

    Read from a local checkout if given, from the main branch on GitHub
    otherwise. Returns None if the listing or a page fetch failed; an
    empty list only when there are no advisory pages.
    """
    pages = []
    if site_dir:
        directory = os.path.join(site_dir, SITE_SECURITY_DIR)
        try:
            names = sorted(os.listdir(directory))
        except OSError as e:
            warn(f"Listing {directory} failed: {e}")
            return None
        for name in names:
            m = ADVISORY_FILE_RE.match(name)
            if not m:
                continue
            with open(os.path.join(directory, name)) as fp:
                pages.append((m.group(1), fp.read()))
        return pages

    url = f"https://api.github.com/repos/{SITE_REPO}/contents/{SITE_SECURITY_DIR}"
    try:
        response = requests.get(url, headers=github_headers(), timeout=30)
    except requests.RequestException as e:
        warn(f"Fetching {url} failed: {e}")
        return None
    if response.status_code != 200:
        warn(
            f"Could not list {SITE_SECURITY_DIR} of {SITE_REPO} "
            f"(HTTP {response.status_code})"
        )
        return None
    for entry in sorted(response.json(), key=lambda e: e.get("name", "")):
        m = ADVISORY_FILE_RE.match(entry.get("name", ""))
        if not m or entry.get("type") != "file":
            continue
        raw = (
            f"https://raw.githubusercontent.com/{SITE_REPO}/main/"
            f"{SITE_SECURITY_DIR}/{entry['name']}"
        )
        try:
            page = requests.get(raw, timeout=30)
        except requests.RequestException as e:
            warn(f"Fetching {raw} failed: {e}")
            return None
        if page.status_code != 200:
            warn(f"Could not fetch {raw} (HTTP {page.status_code})")
            return None
        pages.append((m.group(1), page.text))
    return pages


def parse_advisory(advisory_id, text):
    """Extract the facts of an OSSA page needed for the release notes.

    Returns a dict with the advisory id, the link target relative to the
    release notes page, the title, the publication date, the CVE ids, the
    affected project, the text of the Summary section and the referenced
    container-images-kolla commits and pull requests.
    """
    upper = advisory_id.upper()
    advisory = {
        "id": upper,
        "link": f"{ADVISORY_LINK_PREFIX}{advisory_id}.md",
        "title": upper,
        "date": None,
        "cves": [],
        "project": None,
        "summary": "",
        "commits": [],
        "pulls": [],
    }
    summary = []
    section = None
    for line in text.splitlines():
        if line.startswith("# "):
            heading = line[2:].strip()
            if heading.upper().startswith(upper + ":"):
                heading = heading[len(upper) + 1 :].strip()
            advisory["title"] = heading
            continue
        if line.startswith("## "):
            section = line[3:].strip()
            continue
        m = re.match(r"^\|\s*(Date|CVE|Affected Project)\s*\|(.*)\|\s*$", line)
        if m:
            key, value = m.group(1), m.group(2).strip()
            if key == "Date":
                d = re.search(r"\d{4}-\d{2}-\d{2}", value)
                advisory["date"] = d.group(0) if d else None
            elif key == "CVE":
                for cve in CVE_RE.findall(value):
                    if cve not in advisory["cves"]:
                        advisory["cves"].append(cve)
            else:
                advisory["project"] = value
            continue
        if section == "Summary":
            summary.append(line)
    advisory["summary"] = "\n".join(summary).strip()
    for kind, ref in ADVISORY_REF_RE.findall(text):
        target = advisory["commits"] if kind == "commit" else advisory["pulls"]
        if ref not in target:
            target.append(ref)
    return advisory


def list_range_commits(repo, old, new):
    """SHAs of the commits in v<old>..v<new> of a repository, or None.

    Uses the paginated compare API; a failure returns None, never a
    partial list.
    """
    base = f"v{old.lstrip('v')}"
    head = f"v{new.lstrip('v')}"
    url = f"https://api.github.com/repos/{repo}/compare/{base}...{head}"
    shas = []
    page = 1
    while True:
        params = {"per_page": COMMITS_PER_PAGE, "page": page}
        try:
            response = requests.get(
                url, headers=github_headers(), params=params, timeout=30
            )
        except requests.RequestException as e:
            warn(f"Fetching {url} failed: {e}")
            return None
        if response.status_code != 200:
            warn(
                f"Could not compare {base}...{head} of {repo} "
                f"(HTTP {response.status_code})"
            )
            return None
        commits = response.json().get("commits", [])
        shas.extend(entry["sha"] for entry in commits)
        if len(commits) < COMMITS_PER_PAGE:
            return shas
        page += 1


def commit_files(repo, sha):
    """Paths changed by a commit via the GitHub API, or None on failure."""
    url = f"https://api.github.com/repos/{repo}/commits/{sha}"
    try:
        response = requests.get(url, headers=github_headers(), timeout=30)
    except requests.RequestException as e:
        warn(f"Fetching {url} failed: {e}")
        return None
    if response.status_code != 200:
        warn(f"Could not fetch commit {sha} of {repo} (HTTP {response.status_code})")
        return None
    return [entry["filename"] for entry in response.json().get("files", [])]


def pull_merge_commit(repo, number):
    """Merge commit SHA of a pull request, or None if unknown."""
    url = f"https://api.github.com/repos/{repo}/pulls/{number}"
    try:
        response = requests.get(url, headers=github_headers(), timeout=30)
    except requests.RequestException as e:
        warn(f"Fetching {url} failed: {e}")
        return None
    if response.status_code != 200:
        warn(
            f"Could not fetch pull request {number} of {repo} (HTTP {response.status_code})"
        )
        return None
    return response.json().get("merge_commit_sha")


def advisory_fix_in_release(
    advisory, range_shas, openstack_version, old_date, new_date
):
    """How the fix of an advisory reached the kolla images of a release.

    The advisory pages link the container-images-kolla commits and pull
    requests that ship a fix as downstream patches. Returns
    ("patch", sha) if such a commit touches the OpenStack version of the
    release and lies in the image range, None if one touches the version
    but lies outside the range (the fix was shipped by another release),
    and ("upstream", None) if no referenced commit touches the version
    at all (the fix came with the upstream sources) and the advisory
    was published between the two image builds. Returns None as well
    when a lookup failed, so that nothing is claimed on a guess.
    """
    shas = list(advisory["commits"])
    for number in advisory["pulls"]:
        sha = pull_merge_commit(KOLLA_IMAGES_REPO, number)
        if sha and not any(sha.startswith(s) or s.startswith(sha) for s in shas):
            shas.append(sha)
    marker = f"/{openstack_version}/"
    touching = []
    for sha in shas:
        files = commit_files(KOLLA_IMAGES_REPO, sha)
        if files is None:
            warn(f"{advisory['id']}: cannot resolve the files of {sha}, skipping")
            return None
        if any(marker in path for path in files):
            touching.append(sha)
    for sha in touching:
        if any(full.startswith(sha) or sha.startswith(full) for full in range_shas):
            return ("patch", sha)
    if touching:
        return None
    date = advisory["date"]
    if date and old_date and new_date and old_date < date <= new_date:
        return ("upstream", None)
    return None


def long_date(date):
    """YYYYMMDD -> '1 October 2026' (running text, no ordinal dot)."""
    d = datetime.strptime(date, "%Y%m%d")
    return f"{d.day} {MONTHS[d.month - 1]} {d.year}"


def security_advisory_section(previous, current, previous_release, date, site_dir=None):
    """Markdown input section with the advisories newly fixed in the release.

    Compares the kolla images of the two releases against the OSSA pages
    of osism.github.io and lists the advisories whose fixes the new
    images contain in addition to the previous release, together with
    the fixed wording of the "Security fixes" subsection. Returns None
    if the kolla images did not change, nothing is newly fixed or the
    state cannot be determined.
    """
    key = ("docker_images", "kolla")
    old = previous.get(key)
    new = current.get(key)
    if not old or not new or old == new:
        info("kolla images unchanged, skipping the security advisory check")
        return None
    openstack_version = release_openstack_version("kolla", new)
    if openstack_version is None:
        return None

    info(
        f"Checking the security advisories of {SITE_REPO} against "
        f"{KOLLA_IMAGES_REPO} ({old} -> {new})..."
    )
    pages = list_advisory_pages(site_dir)
    if pages is None:
        return None
    range_shas = list_range_commits(KOLLA_IMAGES_REPO, old, new)
    if range_shas is None:
        return None
    old_date = image_build_date("kolla", old)
    new_date = image_build_date("kolla", new)

    fixed = []
    for advisory_id, text in pages:
        advisory = parse_advisory(advisory_id, text)
        how = advisory_fix_in_release(
            advisory, range_shas, openstack_version, old_date, new_date
        )
        if how:
            info(f"{advisory['id']}: fixed in this release ({how[0]})")
            fixed.append((advisory, how))
    if not fixed:
        info("No security advisories newly fixed in the kolla images")
        return None

    out = [
        "## Security advisories fixed in this release",
        "",
        f"The kolla container images ({old} -> {new}, OpenStack "
        f"{openstack_version}) contain the fixes for the following OpenStack "
        f"Security Advisories that were not yet part of OSISM {previous_release}. "
        'Write the "### Security fixes" subsection from this list as described '
        "in the prompt: the opening and closing paragraphs verbatim, one bullet "
        "per advisory starting with its bullet prefix.",
        "",
        "Opening paragraph:",
        "",
        f"This release includes all security fixes that were known and patched "
        f"up to {long_date(date)}. Compared to OSISM {previous_release}, the "
        "container images additionally contain the fixes for the following "
        "advisories:",
        "",
        "Closing paragraph:",
        "",
        f"The fixes for the advisories already covered by OSISM {previous_release} "
        "remain included. If you obtained one of the fixes above ahead of this "
        "release through the rolling tags, remove the corresponding image "
        "overrides from `environments/kolla/images.yml` when upgrading, so that "
        "the images pinned by the release are used again. All advisories are "
        f"listed in the [Security]({ADVISORY_LINK_PREFIX}index.md) section.",
        "",
    ]
    for advisory, (how, sha) in fixed:
        cves = ", ".join(advisory["cves"]) if advisory["cves"] else "CVE pending"
        project = advisory["project"] or "OpenStack"
        if how == "patch":
            shipped = (
                f"downstream patch, {KOLLA_IMAGES_REPO} commit {sha[:10]} "
                "(context only, not for the release notes)"
            )
        else:
            shipped = (
                "upstream fix included by the image rebuild "
                "(context only, not for the release notes)"
            )
        out.append(f"### {advisory['id']}: {advisory['title']}")
        out.append("")
        out.append(
            f"- Bullet prefix: [{advisory['id']}]({advisory['link']}) "
            f"({project}, {cves}):"
        )
        out.append(f"- Published: {advisory['date'] or 'unknown'}")
        out.append(f"- Fix: {shipped}")
        out.append("")
        out.append(advisory["summary"] or "(no summary)")
        out.append("")
    return "\n".join(out).rstrip()


def upstream_change_lines(old, new, release):
    """Markdown lines for the upstream kolla-ansible changes of a rebuild.

    Collects the commit subjects and the reno release notes between the
    build times of the two image versions from the upstream branch of the
    given OpenStack release, or None if the upstream state cannot be
    determined.
    """
    since = kolla_ansible_build_time(old)
    until = kolla_ansible_build_time(new)
    branch = upstream_kolla_ansible_branch(release)
    if since is None or until is None or branch is None:
        return None

    info(f"Analyzing openstack/kolla-ansible {branch} " f"({since} -> {until})...")
    clone_since = (datetime.fromisoformat(since) - timedelta(days=90)).isoformat()
    tmpdir = tempfile.mkdtemp(prefix="kolla-ansible-")
    try:
        out = run_git(
            [
                "clone",
                "--quiet",
                "--single-branch",
                "--branch",
                branch,
                f"--shallow-since={clone_since}",
                UPSTREAM_KOLLA_ANSIBLE,
                tmpdir,
            ]
        )
        if out is None:
            warn("Cloning the upstream kolla-ansible repository failed")
            return None

        base = (
            run_git(["rev-list", "-1", f"--before={since}", "HEAD"], cwd=tmpdir) or ""
        ).strip()
        head = (
            run_git(["rev-list", "-1", f"--before={until}", "HEAD"], cwd=tmpdir) or ""
        ).strip()
        if not base:
            # branch quiet for the whole shallow window: fall back to the
            # oldest commit of the shallow clone
            revs = (run_git(["rev-list", "HEAD"], cwd=tmpdir) or "").splitlines()
            if not revs:
                warn("No commits found in the upstream kolla-ansible clone")
                return None
            base = revs[-1]
            warn(f"No upstream commit before {since}, using {base[:10]} as base")
        if not head:
            head = "HEAD"
        if base == head or run_git(["rev-parse", head], cwd=tmpdir) == run_git(
            ["rev-parse", base], cwd=tmpdir
        ):
            return [
                f"The kolla-ansible container image ({old} -> {new}) was "
                f"rebuilt from the upstream branch {branch}, which did not "
                "change between the two builds."
            ]

        subjects = [
            line
            for line in (
                run_git(
                    ["log", "--no-merges", "--format=%s", f"{base}..{head}"],
                    cwd=tmpdir,
                )
                or ""
            ).splitlines()
            if line.strip()
        ]

        notes = []
        name_status = (
            run_git(
                ["diff", "--name-status", base, head, "--", "releasenotes/notes"],
                cwd=tmpdir,
            )
            or ""
        )
        for line in name_status.splitlines():
            parts = line.split("\t")
            path = parts[-1]
            if parts[0].startswith(("A", "M", "R")) and path.endswith(
                (".yaml", ".yml")
            ):
                content = run_git(["show", f"{head}:{path}"], cwd=tmpdir)
                if content and content.strip():
                    notes.append((path, content.strip()))
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    out = []
    out.append(
        f"The kolla-ansible container image ({old} -> {new}) is built from "
        f"the upstream openstack/kolla-ansible branch {branch}; the rebuild "
        f"pulled in the following upstream changes ({base[:10]}..{head[:10]}). "
        "This build background is context only and must not be mentioned in "
        "the release notes."
    )
    out.append("")
    out.append("### Upstream release notes (reno) in this range")
    out.append("")
    if notes:
        dropped = len(notes) - MAX_UPSTREAM_NOTES
        for path, content in notes[:MAX_UPSTREAM_NOTES]:
            out.append(f"#### {path}")
            out.append("")
            out.append("```yaml")
            out.append(content)
            out.append("```")
            out.append("")
        if dropped > 0:
            warn(f"Dropped {dropped} upstream release note(s) over the cap")
            out.append(f"({dropped} more release notes omitted)")
            out.append("")
    else:
        out.append("(none)")
        out.append("")
    out.append("### Upstream commit subjects")
    out.append("")
    if subjects:
        dropped = len(subjects) - MAX_UPSTREAM_COMMITS
        for subject in subjects[:MAX_UPSTREAM_COMMITS]:
            out.append(f"- {subject}")
        if dropped > 0:
            warn(f"Dropped {dropped} upstream commit subject(s) over the cap")
            out.append(f"- ({dropped} more commits omitted)")
    else:
        out.append("(none)")
    return out


def extract_sections(changelog, old, new):
    """Return the changelog sections with old < version <= new.

    Sections are returned in chronological order (oldest first). Returns
    None if no section heading in the range could be identified.
    """
    old_key = version_key(old)
    new_key = version_key(new)
    if old_key is None or new_key is None:
        return None

    sections = []
    current_version = None
    current_lines = []
    for line in changelog.splitlines():
        m = CHANGELOG_HEADING_RE.match(line)
        if m:
            if current_version is not None:
                sections.append((current_version, current_lines))
            current_version = version_key(m.group(1))
            current_lines = [line]
        elif current_version is not None:
            current_lines.append(line)
    if current_version is not None:
        sections.append((current_version, current_lines))

    selected = [
        "\n".join(lines).strip()
        for version, lines in sections
        if version is not None and old_key < version <= new_key
    ]
    if not selected:
        return None
    return "\n\n".join(reversed(selected))


def format_table(headers, rows):
    lines = ["| " + " | ".join(headers) + " |"]
    lines.append("|" + "|".join("---" for _ in headers) + "|")
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def cmd_input(args):
    with open(args.repositories) as fp:
        mapping = yaml.safe_load(fp)
    repositories = mapping.get("components", {})
    derived = mapping.get("derived", {})

    previous = load_versions(args.previous)
    current = load_versions(args.current)
    changed, added, removed = diff_versions(previous, current)

    osism_rows = []
    third_rows = []
    ranges = {}
    for (section, name), old, new in changed:
        component = name if not section else f"{section}.{name}"
        repo = repositories.get(name)
        if repo:
            osism_rows.append((component, repo, old, new))
            ranges.setdefault((repo, old.lstrip("v"), new.lstrip("v")), (old, new))
        else:
            third_rows.append((component, old, new))

    # Components whose version is pinned in a requirements file of another
    # component (e.g. netbox-manager in python-osism) instead of base.yml,
    # or installed from a branch when the image of another component is
    # built (e.g. openstack-project-manager in the osism image)
    branch_sections = []
    for name, cfg in sorted(derived.items()):
        repo = cfg["repository"]
        source = cfg["source"]
        source_repo = repositories.get(source)
        package = cfg.get("package", name)
        old_source = component_version(previous, source)
        new_source = component_version(current, source)
        if not source_repo or not old_source or not new_source:
            warn(f"Cannot resolve source component '{source}' for {name}")
            continue
        if old_source == new_source:
            info(f"{name}: unchanged ({source} {old_source})")
            continue
        if "branch" in cfg:
            result = branch_component_change(
                name, cfg, source_repo, old_source, new_source
            )
            if result:
                row, lines = result
                osism_rows.append(row)
                branch_sections.append(lines)
            continue
        info(f"Resolving {name} pin from {source_repo} {cfg['file']}...")
        old = resolve_requirement_pin(source_repo, old_source, cfg["file"], package)
        new = resolve_requirement_pin(source_repo, new_source, cfg["file"], package)
        if old is None or new is None:
            continue
        if old == new:
            info(f"{name}: unchanged ({old})")
            continue
        osism_rows.append((f"{name} (via {source})", repo, old, new))
        ranges.setdefault((repo, old.lstrip("v"), new.lstrip("v")), (old, new))

    out = []
    out.append(f"# Input for the OSISM {args.current} release notes")
    out.append("")
    out.append(f"- Previous release: {args.previous}")
    out.append(f"- New release: {args.current}")
    kolla = current.get(("docker_images", "kolla"))
    openstack_version = release_openstack_version("kolla", kolla) if kolla else None
    if openstack_version:
        out.append(f"- OpenStack version: {openstack_version}")
    else:
        warn("Cannot determine the OpenStack version of the release")
    out.append("")
    out.append("## Version changes")
    out.append("")
    out.append("### OSISM components")
    out.append("")
    if osism_rows:
        out.append(
            format_table(
                ["Component", "Repository", args.previous, args.current],
                osism_rows,
            )
        )
    else:
        out.append("(none)")
    out.append("")
    out.append("### Third-party components")
    out.append("")
    if third_rows:
        out.append(format_table(["Component", args.previous, args.current], third_rows))
    else:
        out.append("(none)")
    out.append("")
    for title, entries in (
        ("Added components", added),
        ("Removed components", removed),
    ):
        if entries:
            out.append(f"### {title}")
            out.append("")
            for (section, name), version in entries:
                component = name if not section else f"{section}.{name}"
                out.append(f"- {component}: {version}")
            out.append("")

    out.append("## Component changelogs")
    out.append("")
    for (repo, _, _), (old, new) in sorted(ranges.items()):
        info(f"Fetching CHANGELOG.md of {repo} ({old} -> {new})...")
        out.append(f"### {repo} ({old} -> {new})")
        out.append("")
        changelog = fetch_changelog(repo)
        if changelog is None:
            warn(f"No CHANGELOG.md found for {repo}")
            out.append("(no CHANGELOG.md available, version bump only)")
        else:
            sections = extract_sections(changelog, old, new)
            if sections is None:
                warn(f"No CHANGELOG.md sections found for {repo} in ({old}, {new}]")
                out.append(
                    "(no matching CHANGELOG.md sections found, version bump only)"
                )
            else:
                out.append(sections)
        out.append("")
    for lines in branch_sections:
        out.extend(lines)
        out.append("")

    upstream = kolla_ansible_upstream_section(previous, current)
    if upstream:
        out.append(upstream)
        out.append("")

    kolla_patches = kolla_images_patch_section(previous, current)
    if kolla_patches:
        out.append(kolla_patches)
        out.append("")

    advisories = security_advisory_section(
        previous, current, args.previous, args.date, args.site_dir
    )
    if advisories:
        out.append(advisories)
        out.append("")

    defaults = osism_kolla_defaults_section(previous, current)
    if defaults:
        out.append(defaults)
        out.append("")

    document = "\n".join(out).rstrip() + "\n"
    if args.output:
        with open(args.output, "w") as fp:
            fp.write(document)
        info(f"Input document written to {args.output}")
    else:
        sys.stdout.write(document)


def unwrap_markdown_fence(lines):
    """Remove an outer ```markdown fence wrapping the whole output.

    If the first fence-like line before any "### " heading is a bare or
    markdown-tagged fence, the model wrapped its whole output in a code
    fence: remove the fence pair and drop everything after the closing
    fence (by construction not part of the intended output). Returns
    (lines, dropped_trailer).
    """
    for i, line in enumerate(lines):
        if line.startswith("### "):
            return lines, []
        if FENCE_RE.match(line):
            if re.fullmatch(r"\s*```(markdown|md)?\s*", line):
                closers = [
                    j
                    for j in range(len(lines) - 1, i, -1)
                    if re.fullmatch(r"\s*```\s*", lines[j])
                ]
                if closers:
                    j = closers[0]
                    trailer = [t for t in lines[j + 1 :] if t.strip()]
                    return lines[:i] + lines[i + 1 : j], trailer
            return lines, []
    return lines, []


def move_metalbox_last(lines):
    """Move a "### MetalBox" subsection to the end of the body.

    The MetalBox-only changes are always the last subsection of a release
    section. Splits the body at the "### " headings outside code fences
    and returns (lines, moved).
    """
    sections = []
    current = []
    in_fence = False
    for line in lines:
        if not in_fence and line.startswith("### "):
            sections.append(current)
            current = []
        current.append(line)
        if FENCE_RE.match(line):
            in_fence = not in_fence
    sections.append(current)

    metalbox = [s for s in sections if s and METALBOX_HEADING_RE.match(s[0])]
    if not metalbox or sections[-1] is metalbox[0]:
        return lines, False

    out = []
    for section in [s for s in sections if s is not metalbox[0]] + [metalbox[0]]:
        if out and out[-1].strip() and section and section[0].startswith("### "):
            out.append("")
        out.extend(section)
    return out, True


def cmd_sanitize(args):
    lines = sys.stdin.read().splitlines()

    lines, trailer = unwrap_markdown_fence(lines)
    if trailer:
        warn(
            f"Dropped {len(trailer)} line(s) after the closing fence of "
            f"the wrapped output:"
        )
        for line in trailer:
            print(f"  {line}", file=sys.stderr)

    in_fence = False
    started = False
    dropped = []
    demoted = 0
    body = []
    for line in lines:
        m = None if in_fence else re.match(r"^#{1,2} (.*)", line)
        if m and re.match(r"^\d+\.\d+\.\d+", m.group(1)):
            # A duplicate of the release heading added by the calling
            # script; never part of the body
            dropped.append(line)
            continue
        if m:
            line = "### " + m.group(1).strip()
            demoted += 1
        if not started:
            if not in_fence and line.startswith("### "):
                started = True
            elif line.strip():
                dropped.append(line)
        if started:
            body.append(line)
        if FENCE_RE.match(line):
            in_fence = not in_fence

    if dropped:
        warn(f"Dropped {len(dropped)} line(s) (preamble or release headings):")
        for line in dropped:
            print(f"  {line}", file=sys.stderr)
    if demoted:
        warn(f"Demoted {demoted} '# '/'## ' heading(s) to '### '")
    if in_fence:
        warn("Unbalanced code fence in the generated body")

    if not started:
        print(
            "Error: generated body contains no '### ' heading, refusing to use it",
            file=sys.stderr,
        )
        sys.exit(1)

    body, moved = move_metalbox_last(body)
    if moved:
        warn("Moved the '### MetalBox' subsection to the end of the body")

    # Collapse consecutive blank lines outside of code fences
    out = []
    in_fence = False
    blank = False
    for line in body:
        if not in_fence and not line.strip():
            blank = True
            continue
        if blank and out:
            out.append("")
        blank = False
        out.append(line)
        if FENCE_RE.match(line):
            in_fence = not in_fence

    sys.stdout.write("\n".join(out) + "\n")


def human_date(date):
    d = datetime.strptime(date, "%Y%m%d")
    return f"{d.day}. {MONTHS[d.month - 1]} {d.year}"


def format_table_row(header_line, version, date):
    """Format a release table row, padded to the header column widths."""
    widths = []
    header_line = header_line.rstrip()
    if header_line.startswith("|") and header_line.endswith("|"):
        widths = [len(cell) for cell in header_line.split("|")[1:-1]]
    cells = []
    for i, value in enumerate((version, human_date(date))):
        cell = f" {value} "
        if i < len(widths):
            cell = cell.ljust(widths[i])
        cells.append(cell)
    return "|" + "|".join(cells) + "|"


def cmd_insert(args):
    with open(args.section_file) as fp:
        section = fp.read().strip()
    with open(args.site_file) as fp:
        site = fp.read()

    expected = f"## {args.version}"
    if section.splitlines()[0] != expected:
        print(
            f"Error: section file must start with '{expected}'",
            file=sys.stderr,
        )
        sys.exit(1)

    lines = site.splitlines()
    in_fence = False
    insert_at = None
    separator_at = None
    for i, line in enumerate(lines):
        if not in_fence:
            m = RELEASE_HEADING_RE.match(line)
            if m and m.group(1) == args.version:
                print(
                    f"Error: {args.site_file} already contains a section "
                    f"for {args.version}",
                    file=sys.stderr,
                )
                sys.exit(1)
            if separator_at is None and re.match(r"^\|\s*:?-+", line):
                separator_at = i
            if insert_at is None and m:
                insert_at = i
        if FENCE_RE.match(line):
            in_fence = not in_fence

    if separator_at is None or (insert_at is not None and separator_at > insert_at):
        print(
            f"Error: no release table found in {args.site_file} before the "
            f"first release section",
            file=sys.stderr,
        )
        sys.exit(1)

    # End of the release table: the first line after the separator that is
    # not a table row
    table_end = separator_at + 1
    while table_end < len(lines) and lines[table_end].lstrip().startswith("|"):
        table_end += 1

    row = format_table_row(lines[separator_at - 1], args.version, args.date)

    # Insert the table row first (newest first, directly after the
    # separator); all indices after the separator shift by one.
    lines.insert(separator_at + 1, row)

    if insert_at is not None:
        target = insert_at + 1
    else:
        # First release section of the page: insert directly after the
        # release table, keeping the blank line that follows it
        target = table_end + 1
        if target < len(lines) and not lines[target].strip():
            target += 1

    block = section.splitlines() + [""]
    if target > 0 and target <= len(lines) and lines[target - 1].strip():
        block = [""] + block
    lines[target:target] = block

    with open(args.site_file, "w") as fp:
        fp.write("\n".join(lines).rstrip() + "\n")

    info(f"Inserted section '{expected}' into {args.site_file}")


def main():
    parser = argparse.ArgumentParser(
        description="Helpers for generating OSISM release notes sections"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    sub = subparsers.add_parser("input")
    sub.add_argument("--previous", required=True, help="previous release, e.g. 10.0.0")
    sub.add_argument("--current", required=True, help="new release, e.g. 10.1.0")
    sub.add_argument("--repositories", default="etc/changelog-repositories.yml")
    sub.add_argument(
        "--date",
        default=datetime.now().strftime("%Y%m%d"),
        help="release date as YYYYMMDD (default: today)",
    )
    sub.add_argument(
        "--site-dir",
        help="osism.github.io checkout to read the security advisories from "
        "(default: fetched from GitHub)",
    )
    sub.add_argument("--output", help="output file (default: stdout)")
    sub.set_defaults(func=cmd_input)

    sub = subparsers.add_parser("sanitize")
    sub.set_defaults(func=cmd_sanitize)

    sub = subparsers.add_parser("insert")
    sub.add_argument("--site-file", required=True)
    sub.add_argument("--section-file", required=True)
    sub.add_argument("--version", required=True)
    sub.add_argument("--date", required=True, help="release date as YYYYMMDD")
    sub.set_defaults(func=cmd_insert)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
