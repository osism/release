import importlib.util
import pathlib

import pytest
import responses

# check-versions.py is hyphenated -> not importable by name; load it by path.
_SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "check-versions.py"
_spec = importlib.util.spec_from_file_location("check_versions", _SRC)
cv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cv)

PULLS = "https://api.github.com/repos/{repo}/pulls"
RAW = "https://raw.githubusercontent.com/osism/python-osism/{ref}/requirements.openstack-image-manager.txt"

BASE_YML = """---
manager_version: latest

# renovate: datasource=github-tags depName=osism/generics
generics_version: 'v0.20261005.0'

osism_projects:
  # renovate: datasource=pypi depName=osism
  osism: '0.20261007.0'
  # renovate: datasource=github-releases depName=k3s-io/k3s
  k3s: 'v1.36.4+k3s1'

docker_images:
  # renovate: datasource=docker depName=registry.osism.tech/osism/osism
  osism: '0.20261007.0'
  # yamllint disable-line rule:line-length
  # renovate: datasource=docker depName=registry.osism.tech/osism/tempest
  tempest: 'latest'

ansible_collections:
  # renovate: datasource=galaxy-collection depName=osism.commons
  osism.commons: '0.20261005.0'
"""

MAPPING = {
    "components": {"osism": "osism/python-osism"},
    "derived": {
        "openstack-image-manager": {
            "repository": "osism/openstack-image-manager",
            "source": "osism",
            "file": "requirements.openstack-image-manager.txt",
        },
        "openstack-project-manager": {
            "repository": "osism/openstack-project-manager",
            "source": "osism",
            "branch": "main",
        },
    },
}


@pytest.fixture(autouse=True)
def _clear_caches():
    for cached in (cv.newest_version, cv.open_pull_requests, cv.requirement_pin):
        cached.cache_clear()
    cv.github_headers.cached = {}


def _newest(versions):
    def lookup(datasource, dependency):
        version = versions.get((datasource, dependency))
        return (version, None) if version else (None, "lookup failed")

    return lookup


def _pulls(repo, pulls):
    responses.add(
        responses.GET,
        PULLS.format(repo=repo),
        json=[
            {"number": number, "title": title, "user": {"login": login}}
            for number, title, login in pulls
        ],
        status=200,
    )


def _image_manager_pin(ref, version):
    responses.add(
        responses.GET,
        RAW.format(ref=ref),
        body=f"openstack-image-manager=={version}\n",
        status=200,
    )


def test_parse_pins_names_section_pins_and_skips_disabled_annotations(tmp_path):
    path = tmp_path / "openstack-2024.1.yml"
    path.write_text(
        "---\n"
        "# # renovate: datasource=pypi depName=ansible\n"
        "ansible_version: '10.7.0'\n"
        "\n"
        "docker_images:\n"
        "  # renovate: datasource=docker depName=registry.osism.tech/osism/osism\n"
        "  osism: '0.20261007.0'\n"
        "  # renovate: datasource=docker depName=redis\n"
        "\n"
        "  redis: '7.4.10-alpine'\n"
        "# renovate: datasource=github-tags depName=osism/defaults\n"
        "defaults_version: 'v0.20261005.0'\n"
    )
    assert cv.parse_pins(path) == [
        cv.Pin(
            "docker_images.osism",
            "docker",
            "registry.osism.tech/osism/osism",
            "0.20261007.0",
        ),
        cv.Pin("defaults_version", "github-tags", "osism/defaults", "v0.20261005.0"),
    ]


@responses.activate
def test_newest_docker_fetches_an_anonymous_token_and_ignores_other_tags():
    tags = "https://registry.osism.tech/v2/osism/osism/tags/list"
    responses.add(
        responses.GET,
        tags,
        status=401,
        headers={
            "WWW-Authenticate": 'Bearer realm="https://registry.osism.tech/service/token",'
            'service="harbor-registry",scope="repository:osism/osism:pull"'
        },
    )
    responses.add(
        responses.GET,
        "https://registry.osism.tech/service/token",
        json={"token": "secret"},
        status=200,
    )
    responses.add(
        responses.GET,
        tags,
        json={"tags": ["latest", "0.20261001.1", "0.20260930.0", "0.20261001.0"]},
        status=200,
    )
    assert cv.newest_docker("registry.osism.tech/osism/osism") == "0.20261001.1"
    assert responses.calls[1].request.params == {
        "service": "harbor-registry",
        "scope": "repository:osism/osism:pull",
    }
    assert responses.calls[2].request.headers["Authorization"] == "Bearer secret"


@responses.activate
def test_pull_request_note_matches_whole_names_of_renovate_pull_requests():
    _pulls(
        "osism/release",
        [
            (1, "Update dependency osism.commons to v0.20261007.0", "renovate[bot]"),
            (
                2,
                "Update registry.osism.tech/osism/osism-ansible Docker tag to v0.20261008.0",
                "renovate[bot]",
            ),
            (3, "Bump osism in the docs", "someone"),
            (4, "Update osism to v0.20261007.0", "renovate[bot]"),
        ],
    )
    assert cv.pull_request_note("osism/release", ("osism",)) == (
        "open PR osism/release#4"
    )
    assert cv.pull_request_note("osism/release", ("osism.commons",)) == (
        "open PR osism/release#1"
    )
    assert cv.pull_request_note("osism/release", ("osism/generics", "generics")) == (
        "no open Renovate PR in osism/release"
    )


SERIES_YML = """---
# renovate: datasource=github-tags depName=osism/generics
generics_version: 'v0.20261005.0'
"""


@responses.activate
def test_check_latest_reports_osism_pins_of_base_and_the_symlinked_series(
    tmp_path, monkeypatch
):
    (tmp_path / "base.yml").write_text(BASE_YML)
    (tmp_path / "ceph-quincy.yml").write_text(SERIES_YML)
    (tmp_path / "ceph-reef.yml").write_text(SERIES_YML)
    (tmp_path / "ceph.yml").symlink_to("ceph-reef.yml")
    (tmp_path / "ceph_ansible.yml").symlink_to("ceph-reef.yml")
    monkeypatch.setattr(
        cv,
        "newest_version",
        _newest(
            {
                ("github-tags", "osism/generics"): "v0.20261007.0",
                ("pypi", "osism"): "0.20261008.0",
                ("docker", "registry.osism.tech/osism/osism"): "0.20261007.0",
                ("galaxy-collection", "osism.commons"): "0.20261001.0",
            }
        ),
    )
    _pulls("osism/release", [(7, "Update osism to v0.20261008.0", "renovate[bot]")])

    rows = cv.check_latest(str(tmp_path))

    # ceph-quincy.yml is no default series, ceph_ansible.yml points to the
    # file already checked via ceph.yml
    base = str(tmp_path / "base.yml")
    assert rows == [
        cv.Row(
            "outdated",
            base,
            "generics_version",
            "osism/generics",
            "v0.20261005.0",
            "v0.20261007.0",
            "no open Renovate PR in osism/release",
        ),
        cv.Row(
            "outdated",
            base,
            "osism_projects.osism",
            "osism",
            "0.20261007.0",
            "0.20261008.0",
            "open PR osism/release#7",
        ),
        cv.Row(
            "ok",
            base,
            "docker_images.osism",
            "registry.osism.tech/osism/osism",
            "0.20261007.0",
            "0.20261007.0",
            "",
        ),
        cv.Row(
            "error",
            base,
            "ansible_collections.osism.commons",
            "osism.commons",
            "0.20261005.0",
            "0.20261001.0",
            "pinned version is not published",
        ),
        cv.Row(
            "outdated",
            f"{tmp_path / 'ceph.yml'} (ceph-reef.yml)",
            "generics_version",
            "osism/generics",
            "v0.20261005.0",
            "v0.20261007.0",
            "no open Renovate PR in osism/release",
        ),
    ]


def _check_derived(tmp_path, monkeypatch, newest):
    (tmp_path / "base.yml").write_text(BASE_YML)
    monkeypatch.setattr(cv, "newest_version", _newest(newest))
    return cv.check_derived(MAPPING, str(tmp_path / "base.yml"))


@responses.activate
def test_check_derived_accepts_the_newest_pin(tmp_path, monkeypatch):
    _image_manager_pin("v0.20261007.0", "0.20261007.0")
    rows = _check_derived(
        tmp_path,
        monkeypatch,
        {("pypi", "openstack-image-manager"): "0.20261007.0"},
    )
    assert rows == [
        cv.Row(
            "ok",
            "osism/python-osism v0.20261007.0",
            "requirements.openstack-image-manager.txt",
            "openstack-image-manager",
            "0.20261007.0",
            "0.20261007.0",
            "",
        )
    ]


@responses.activate
def test_check_derived_names_the_unmerged_pull_request(tmp_path, monkeypatch):
    _image_manager_pin("v0.20261007.0", "0.20261001.0")
    _image_manager_pin("main", "0.20261001.0")
    _pulls(
        "osism/python-osism",
        [
            (
                9,
                "chore(deps): update dependency openstack-image-manager to v0.20261008.0",
                "renovate[bot]",
            )
        ],
    )
    rows = _check_derived(
        tmp_path,
        monkeypatch,
        {("pypi", "openstack-image-manager"): "0.20261008.0"},
    )
    assert rows[0].status == "outdated"
    assert rows[0].note == "outdated on main, open PR osism/python-osism#9"


@responses.activate
def test_check_derived_asks_for_a_python_osism_release(tmp_path, monkeypatch):
    _image_manager_pin("v0.20261007.0", "0.20261001.0")
    _image_manager_pin("main", "0.20261008.0")
    rows = _check_derived(
        tmp_path,
        monkeypatch,
        {
            ("pypi", "openstack-image-manager"): "0.20261008.0",
            ("github-tags", "osism/python-osism"): "v0.20261007.0",
        },
    )
    assert rows[0].note == "only on main, needs a new osism/python-osism release"


@responses.activate
def test_check_derived_points_to_the_newer_python_osism_release(tmp_path, monkeypatch):
    _image_manager_pin("v0.20261007.0", "0.20261001.0")
    _image_manager_pin("main", "0.20261008.0")
    _image_manager_pin("v0.20261009.0", "0.20261008.0")
    rows = _check_derived(
        tmp_path,
        monkeypatch,
        {
            ("pypi", "openstack-image-manager"): "0.20261008.0",
            ("github-tags", "osism/python-osism"): "v0.20261009.0",
        },
    )
    assert rows[0].note == (
        "in osism/python-osism v0.20261009.0, update osism in latest/base.yml"
    )
