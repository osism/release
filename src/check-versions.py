#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = [
#     "requests",
#     "pyyaml",
# ]
# ///
#
# Helper for scripts/check-versions.sh: checks that latest/ pins the newest
# published versions of the OSISM components, so that no update pull
# request is forgotten before the tags of a release are created.
#
# Checked are:
#
# - the symlinks that name the default series: latest/ceph.yml has to point
#   to the newest active Ceph release (ceph/ceph doc/releases/releases.yml),
#   latest/openstack.yml to the newest released SLURP release of OpenStack
#   (openstack/releases data/series_status.yaml).
# - every pin with a Renovate annotation and an OSISM version (date-based,
#   v0.YYYYMMDD.N) in latest/base.yml and in the Ceph and OpenStack files
#   the symlinks of latest/ point to (ceph.yml, ceph_ansible.yml,
#   openstack.yml): defaults, generics, the playbooks, the osism.*
#   collections, the osism package and the OSISM images. The files of the
#   other series are not checked. The newest version is read from the
#   datasource of the annotation, like Renovate does (GitHub tags, Ansible
#   Galaxy, PyPI, container registry).
# - the components that are pinned in a requirements file of another
#   component ("derived" with a "file" in etc/changelog-repositories.yml,
#   e.g. openstack-image-manager in python-osism): the pin in the version of
#   the source component used by latest/base.yml, compared with the newest
#   release on PyPI.
#
# For an outdated pin the open Renovate pull request that updates it is
# named, if there is one. Exits with 1 if a symlink or a pin is outdated or
# its newest version could not be determined.

import argparse
import collections
import functools
import os
import re
import subprocess
import sys

import requests
import yaml

GALAXY_URL = (
    "https://galaxy.ansible.com/api/v3/plugin/ansible/content/published/"
    "collections/index/{namespace}/{name}/"
)
PYPI_URL = "https://pypi.org/pypi/{package}/json"
RAW_URL = "https://raw.githubusercontent.com/{repo}/{ref}/{path}"
CEPH_RELEASES_URL = RAW_URL.format(
    repo="ceph/ceph", ref="main", path="doc/releases/releases.yml"
)
OPENSTACK_SERIES_URL = RAW_URL.format(
    repo="openstack/releases", ref="master", path="data/series_status.yaml"
)
PULLS_URL = "https://api.github.com/repos/{repo}/pulls"

# The repository whose latest/ directory is checked
RELEASE_REPO = "osism/release"

OSISM_VERSION = re.compile(r"v?0\.\d{8}\.\d+")
ANNOTATION = re.compile(r"\s*# renovate: datasource=(\S+) depName=(\S+)")
SECTION = re.compile(r"([^\s#:][^:]*):\s*")
PIN = re.compile(r"(\s*)([^\s#:][^:]*):\s*['\"]?([^'\"\s]+)")

Pin = collections.namedtuple("Pin", "name datasource dependency version")
Row = collections.namedtuple("Row", "status where name dependency pinned newest note")


def warn(message):
    print(f"Warning: {message}", file=sys.stderr)


def version_key(version):
    """Sort key for OSISM version strings like v0.20260615.0."""
    return tuple(int(part) for part in version.lstrip("v").split("."))


def newest(versions):
    """The newest OSISM version of the given versions, or None."""
    candidates = [v for v in versions if OSISM_VERSION.fullmatch(v)]
    return max(candidates, key=version_key) if candidates else None


def get_json(url, **kwargs):
    response = requests.get(url, timeout=30, **kwargs)
    response.raise_for_status()
    return response.json()


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


def parse_pins(path):
    """Return the Renovate-annotated pins of a version file.

    Like the regex managers in .github/renovate.json, an annotation applies
    to the line directly following it; a disabled annotation ("# # renovate:")
    is no annotation. A pin in a section is named <section>.<key>, e.g.
    docker_images.osism.
    """
    pins = []
    section = None
    annotation = None
    with open(path) as fp:
        for line in fp:
            line = line.rstrip("\n")
            m = ANNOTATION.match(line)
            if m:
                annotation = m.groups()
                continue
            current, annotation = annotation, None
            m = SECTION.fullmatch(line)
            if m:
                section = m.group(1)
                continue
            m = PIN.match(line)
            if not m:
                continue
            indent, key, version = m.groups()
            if not indent:
                section = None
            if current is None:
                continue
            name = f"{section}.{key}" if section else key
            pins.append(Pin(name, current[0], current[1], version))
    return pins


def newest_github_tag(repo):
    # git ls-remote is not subject to the rate limit of the GitHub API
    result = subprocess.run(
        ["git", "ls-remote", "--tags", "--refs", f"https://github.com/{repo}"],
        capture_output=True,
        text=True,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )
    if result.returncode != 0:
        raise LookupError(result.stderr.strip() or "git ls-remote failed")
    return newest(
        line.split("refs/tags/", 1)[-1] for line in result.stdout.splitlines()
    )


def newest_galaxy_collection(collection):
    namespace, name = collection.split(".", 1)
    data = get_json(GALAXY_URL.format(namespace=namespace, name=name))
    return newest([data["highest_version"]["version"]])


def newest_pypi(package):
    return newest([get_json(PYPI_URL.format(package=package))["info"]["version"]])


def registry_token_header(challenge):
    """Anonymous pull token for a registry from its WWW-Authenticate challenge."""
    if not challenge.startswith("Bearer "):
        raise LookupError(f"unsupported registry authentication: {challenge!r}")
    params = dict(re.findall(r'(\w+)="([^"]*)"', challenge))
    realm = params.pop("realm")
    data = get_json(realm, params=params)
    return {"Authorization": f"Bearer {data.get('token') or data['access_token']}"}


def newest_docker(image):
    registry, _, repository = image.partition("/")
    if "." not in registry or not repository:
        raise LookupError(f"no registry host in {image}")
    url = f"https://{registry}/v2/{repository}/tags/list"
    headers = {}
    tags = []
    while url:
        response = requests.get(url, headers=headers, timeout=30)
        if response.status_code == 401 and not headers:
            headers = registry_token_header(
                response.headers.get("WWW-Authenticate", "")
            )
            continue
        response.raise_for_status()
        tags += response.json().get("tags") or []
        url = response.links.get("next", {}).get("url")
        if url and url.startswith("/"):
            url = f"https://{registry}{url}"
    return newest(tags)


LOOKUPS = {
    "docker": newest_docker,
    "galaxy-collection": newest_galaxy_collection,
    "github-tags": newest_github_tag,
    "pypi": newest_pypi,
}


@functools.lru_cache(maxsize=None)
def newest_version(datasource, dependency):
    """Newest OSISM version of a dependency as (version, None) or (None, error)."""
    lookup = LOOKUPS.get(datasource)
    if lookup is None:
        return None, f"datasource {datasource} is not supported"
    try:
        version = lookup(dependency)
    except (LookupError, ValueError, requests.RequestException) as e:
        return None, f"{datasource} lookup failed: {e}"
    if version is None:
        return None, f"no OSISM version found ({datasource})"
    return version, None


def compare(pinned, newest_published, error):
    """Status and note of a pin compared with the newest published version."""
    if newest_published is None:
        return "error", error
    if version_key(pinned) < version_key(newest_published):
        return "outdated", ""
    if version_key(pinned) > version_key(newest_published):
        return "error", "pinned version is not published"
    return "ok", ""


@functools.lru_cache(maxsize=None)
def open_pull_requests(repo):
    """Titles of the open Renovate pull requests as {number: title}, or None."""
    pulls = {}
    url = PULLS_URL.format(repo=repo)
    params = {"state": "open", "per_page": 100}
    try:
        while url:
            response = requests.get(
                url, params=params, headers=github_headers(), timeout=30
            )
            response.raise_for_status()
            for pull in response.json():
                if pull["user"]["login"].startswith("renovate"):
                    pulls[pull["number"]] = pull["title"]
            url = response.links.get("next", {}).get("url")
            params = None
    except (KeyError, ValueError, requests.RequestException) as e:
        warn(f"Listing the open pull requests of {repo} failed: {e}")
        return None
    return pulls


def pull_request_note(repo, names):
    """Name the open Renovate pull request updating one of the given names.

    A name only matches as a whole, so that osism does not match
    osism.commons or registry.osism.tech/osism/osism-ansible.
    """
    pulls = open_pull_requests(repo)
    if pulls is None:
        return ""
    for number, title in sorted(pulls.items()):
        for name in names:
            if re.search(rf"(?<![\w./-]){re.escape(name)}(?![\w./-])", title, re.I):
                return f"open PR {repo}#{number}"
    return f"no open Renovate PR in {repo}"


def version_files(directory):
    """base.yml and the files the symlinks of latest/ point to, as (where, path).

    Only the Ceph and OpenStack series the symlinks (ceph.yml,
    ceph_ansible.yml, openstack.yml) point to are in use; a file several
    symlinks point to is checked once.
    """
    base = os.path.join(directory, "base.yml")
    files = [(base, base)]
    seen = {os.path.realpath(base)}
    for filename in sorted(os.listdir(directory)):
        path = os.path.join(directory, filename)
        if not filename.endswith(".yml") or not os.path.islink(path):
            continue
        if os.path.realpath(path) in seen:
            continue
        seen.add(os.path.realpath(path))
        files.append((f"{path} ({os.readlink(path)})", path))
    return files


def check_latest(directory):
    rows = []
    for where, path in version_files(directory):
        for pin in parse_pins(path):
            if not OSISM_VERSION.fullmatch(pin.version):
                continue
            newest_published, error = newest_version(pin.datasource, pin.dependency)
            status, note = compare(pin.version, newest_published, error)
            if status == "outdated":
                # grouped updates are titled by the group, e.g. "Update osism"
                # for registry.osism.tech/osism/osism and the osism package
                names = (pin.dependency, pin.dependency.rsplit("/", 1)[-1])
                note = pull_request_note(RELEASE_REPO, names)
            rows.append(
                Row(
                    status,
                    where,
                    pin.name,
                    pin.dependency,
                    pin.version,
                    newest_published,
                    note,
                )
            )
    return rows


def base_version(base, name):
    """Version of a component in base.yml, regardless of its section."""
    if f"{name}_version" in base:
        return str(base[f"{name}_version"])
    for value in base.values():
        if isinstance(value, dict) and name in value:
            return str(value[name])
    return None


@functools.lru_cache(maxsize=None)
def requirement_pin(repo, ref, path, package):
    """The ==-pinned version of a package in a requirements file at a ref."""
    response = requests.get(RAW_URL.format(repo=repo, ref=ref, path=path), timeout=30)
    response.raise_for_status()
    for line in response.text.splitlines():
        m = re.match(rf"{re.escape(package)}\s*==\s*(\S+)", line)
        if m:
            return m.group(1)
    raise LookupError(f"no {package}== pin in {path} of {repo} at {ref}")


def tag(version):
    # base.yml pins osism as 0.20260615.0, the python-osism tag is v0.20260615.0
    return f"v{version.lstrip('v')}"


def derived_note(repo, source, path, package, newest_published):
    """Explain where an outdated pin of a derived component has to be fixed."""
    try:
        if version_key(requirement_pin(repo, "main", path, package)) < version_key(
            newest_published
        ):
            return "outdated on main, " + pull_request_note(repo, (package,))
        release, _ = newest_version("github-tags", repo)
        if release and version_key(
            requirement_pin(repo, release, path, package)
        ) == version_key(newest_published):
            return f"in {repo} {release}, update {source} in latest/base.yml"
    except (LookupError, ValueError, requests.RequestException) as e:
        warn(f"Resolving the {package} pin of {repo} failed: {e}")
        return ""
    return f"only on main, needs a new {repo} release"


def check_derived(mapping, base_path):
    with open(base_path) as fp:
        base = yaml.safe_load(fp)
    rows = []
    for name, cfg in sorted(mapping.get("derived", {}).items()):
        # installed from a branch when the source is built, nothing pinned
        if "file" not in cfg:
            continue
        source = cfg["source"]
        repo = mapping["components"][source]
        package = cfg.get("package", name)
        version = base_version(base, source)
        if version is None:
            rows.append(
                Row("error", base_path, "-", package, "-", None, f"no {source} pin")
            )
            continue
        where = f"{repo} {tag(version)}"
        try:
            pinned = requirement_pin(repo, tag(version), cfg["file"], package)
        except (LookupError, requests.RequestException) as e:
            rows.append(Row("error", where, cfg["file"], package, "-", None, str(e)))
            continue
        newest_published, error = newest_version("pypi", package)
        status, note = compare(pinned, newest_published, error)
        if status == "outdated":
            note = derived_note(repo, source, cfg["file"], package, newest_published)
        rows.append(
            Row(status, where, cfg["file"], package, pinned, newest_published, note)
        )
    return rows


def get_yaml(url):
    """A YAML document from a URL with every scalar as a string.

    The BaseLoader keeps a release-id like 2026.1 a string, not a float.
    """
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    return yaml.load(response.text, Loader=yaml.BaseLoader)


def ceph_releases():
    """The released Ceph releases as [(codename, active)], newest first.

    A release counts once it has a stable version (x.2.z; x.0.z are
    development versions, x.1.z release candidates) and is active until
    upstream sets its actual_eol.
    """
    releases = []
    for name, data in get_yaml(CEPH_RELEASES_URL)["releases"].items():
        stable = [
            release["version"]
            for release in data.get("releases") or []
            if int(release["version"].split(".")[1]) >= 2
        ]
        if stable:
            major = int(stable[0].split(".")[0])
            releases.append((major, name, "actual_eol" not in data))
    return [(name, active) for _, name, active in sorted(releases, reverse=True)]


def openstack_releases():
    """The released OpenStack series as [(release-id, slurp)], newest first."""
    series = [
        (entry["release-id"], entry.get("slurp", "").lower() in ("yes", "true"))
        for entry in get_yaml(OPENSTACK_SERIES_URL)
        if "release-id" in entry and entry["status"] != "development"
    ]
    return sorted(series, key=lambda entry: version_key(entry[0]), reverse=True)


# The symlinks of latest/ that name the default series: the prefix of the
# series files, which series the symlink has to point to, and the upstream
# data the series are read from
SYMLINKS = (
    ("ceph.yml", "ceph-", "active Ceph release", "ceph/ceph", ceph_releases),
    (
        "openstack.yml",
        "openstack-",
        "released SLURP release",
        "openstack/releases",
        openstack_releases,
    ),
)


def check_symlinks(directory):
    """Check that the symlinks of latest/ point to the newest default series.

    ceph_ansible.yml is no default series and not checked: it names the
    series that existing clusters are managed with by ceph-ansible, which
    stops at squid.
    """
    rows = []
    for symlink, prefix, criterion, source, releases in SYMLINKS:
        path = os.path.join(directory, symlink)
        target = os.readlink(path) if os.path.islink(path) else "-"
        try:
            series = releases()
        except (
            AttributeError,
            LookupError,
            TypeError,
            ValueError,
            yaml.YAMLError,
            requests.RequestException,
        ) as e:
            rows.append(
                Row(
                    "error",
                    path,
                    "symlink",
                    source,
                    target,
                    None,
                    f"lookup failed: {e}",
                )
            )
            continue
        files = [f"{prefix}{name}.yml" for name, _ in series]
        candidates = [file for file, (_, ok) in zip(files, series) if ok]
        if not candidates:
            rows.append(
                Row("error", path, "symlink", source, target, None, f"no {criterion}")
            )
            continue
        newest_series = candidates[0]
        if target == newest_series:
            status, note = "ok", ""
        elif target in files and files.index(target) > files.index(newest_series):
            status, note = "outdated", f"newest {criterion}"
        else:
            status, note = "error", f"not the newest {criterion}"
        if status != "ok" and not os.path.exists(
            os.path.join(directory, newest_series)
        ):
            note += f", {newest_series} does not exist yet"
        rows.append(Row(status, path, "symlink", source, target, newest_series, note))
    return rows


def print_report(rows, verbose):
    shown = [row for row in rows if verbose or row.status != "ok"]
    if shown:
        table = [("STATUS", "WHERE", "PIN", "DEPENDENCY", "PINNED", "NEWEST", "NOTE")]
        table += [
            (r.status, r.where, r.name, r.dependency, r.pinned, r.newest or "-", r.note)
            for r in shown
        ]
        widths = [max(len(row[i]) for row in table) for i in range(6)]
        for row in table:
            cells = [cell.ljust(width) for cell, width in zip(row, widths)]
            print("  ".join(cells + [row[6]]).rstrip())
        print()
    counts = collections.Counter(row.status for row in rows)
    print(
        f"{len(rows)} pins and symlinks checked: {counts['ok']} current, "
        f"{counts['outdated']} outdated, {counts['error']} unresolved"
    )


def main():
    parser = argparse.ArgumentParser(
        description="Check that latest/ pins the newest OSISM component versions "
        "and points to the newest default Ceph and OpenStack series"
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="also list the current pins"
    )
    parser.add_argument("--latest", default="latest", help="directory to check")
    parser.add_argument(
        "--repositories",
        default="etc/changelog-repositories.yml",
        help="component mapping with the derived components",
    )
    args = parser.parse_args()

    with open(args.repositories) as fp:
        mapping = yaml.safe_load(fp)

    rows = check_symlinks(args.latest)
    rows += check_latest(args.latest)
    rows += check_derived(mapping, os.path.join(args.latest, "base.yml"))
    print_report(rows, args.verbose)
    return 0 if all(row.status == "ok" for row in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
