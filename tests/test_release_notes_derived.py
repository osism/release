import importlib.util
import pathlib

import responses

# release-notes.py is hyphenated -> not importable by name; load it by path.
_SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "release-notes.py"
_spec = importlib.util.spec_from_file_location("release_notes", _SRC)
rn = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rn)

API = "https://api.github.com/repos"
PM = "osism/openstack-project-manager"
PM_COMMITS = f"{API}/{PM}/commits"


def _commit(sha, message, merge=False):
    return {
        "sha": sha,
        "commit": {"message": message},
        "parents": [{"sha": "p1"}, {"sha": "p2"}] if merge else [{"sha": "p1"}],
    }


def _commit_time(repo, ref, date):
    responses.add(
        responses.GET,
        f"{API}/{repo}/commits/{ref}",
        json={"commit": {"committer": {"date": date}}},
        status=200,
    )


@responses.activate
def test_requirement_pin_adds_the_v_prefix_of_the_source_tag():
    # base.yml pins osism as 0.20260615.0, the python-osism tag is v0.20260615.0
    responses.add(
        responses.GET,
        "https://raw.githubusercontent.com/osism/python-osism/v0.20260615.0/"
        "requirements.openstack-image-manager.txt",
        body="openstack-image-manager==0.20260601.0\n",
        status=200,
    )
    assert (
        rn.resolve_requirement_pin(
            "osism/python-osism",
            "0.20260615.0",
            "requirements.openstack-image-manager.txt",
            "openstack-image-manager",
        )
        == "0.20260601.0"
    )


@responses.activate
def test_source_build_time_uses_the_tag_and_falls_back_to_the_version_date():
    _commit_time("osism/python-osism", "v0.20260615.0", "2026-06-15T10:00:00Z")
    assert (
        rn.source_build_time("osism/python-osism", "0.20260615.0")
        == "2026-06-15T10:00:00Z"
    )
    responses.add(
        responses.GET, f"{API}/osism/python-osism/commits/v0.20260701.0", status=404
    )
    assert (
        rn.source_build_time("osism/python-osism", "0.20260701.0")
        == "2026-07-01T23:59:59+00:00"
    )


@responses.activate
def test_branch_commits_skip_merges_and_paginate():
    page1 = [_commit(f"{i:040x}", f"commit {i}") for i in range(rn.COMMITS_PER_PAGE)]
    page1[3] = _commit("m" * 40, "Merge pull request #3", merge=True)
    page2 = [_commit("f" * 40, "first\n\nbody")]
    responses.add(responses.GET, PM_COMMITS, json=page1, status=200)
    responses.add(responses.GET, PM_COMMITS, json=page2, status=200)

    commits = rn.list_branch_commits(PM, "main", "2026-01-01T00:00:00Z", None)
    assert len(commits) == rn.COMMITS_PER_PAGE  # 100 - 1 merge + 1 on page 2
    assert all(sha != "m" * 40 for sha, _ in commits)
    assert commits[-1] == ("f" * 40, "first")
    assert len(responses.calls) == 2
    assert "since=" in responses.calls[0].request.url
    assert "until=" not in responses.calls[0].request.url


@responses.activate
def test_branch_commits_limit_stops_after_the_first_match():
    responses.add(
        responses.GET,
        PM_COMMITS,
        json=[_commit("a" * 40, "newest"), _commit("b" * 40, "older")],
        status=200,
    )
    assert rn.list_branch_commits(PM, "main", None, "2026-01-01T00:00:00Z", limit=1) == [
        ("a" * 40, "newest")
    ]


@responses.activate
def test_branch_commits_none_on_http_error():
    responses.add(responses.GET, PM_COMMITS, status=403)
    assert rn.list_branch_commits(PM, "main", None, None) is None


@responses.activate
def test_branch_component_change_builds_row_and_section():
    _commit_time("osism/python-osism", "v0.20260615.0", "2026-06-15T10:00:00Z")
    _commit_time("osism/python-osism", "v0.20260701.0", "2026-07-01T10:00:00Z")
    # the range listing (since and until), then the base lookup (until only)
    responses.add(
        responses.GET,
        PM_COMMITS,
        json=[
            _commit("c" * 40, "Add quota handling (#42)"),
            _commit("b" * 40, "Fix typo"),
        ],
        status=200,
    )
    responses.add(
        responses.GET, PM_COMMITS, json=[_commit("a" * 40, "before")], status=200
    )

    cfg = {"repository": PM, "source": "osism", "branch": "main"}
    row, lines = rn.branch_component_change(
        "openstack-project-manager", cfg, "osism/python-osism", "0.20260615.0", "0.20260701.0"
    )
    assert row == (
        "openstack-project-manager (via osism, main)",
        PM,
        "a" * 10,
        "c" * 10,
    )
    assert lines[0] == f"### {PM} (main, {'a' * 10} -> {'c' * 10})"
    assert f"- Add quota handling ({PM}#42)" in lines
    assert "- Fix typo" in lines
    assert "must not be mentioned in the release notes" in lines[2]


@responses.activate
def test_branch_component_change_none_without_commits():
    _commit_time("osism/python-osism", "v0.20260615.0", "2026-06-15T10:00:00Z")
    _commit_time("osism/python-osism", "v0.20260701.0", "2026-07-01T10:00:00Z")
    responses.add(responses.GET, PM_COMMITS, json=[], status=200)
    cfg = {"repository": PM, "source": "osism", "branch": "main"}
    assert (
        rn.branch_component_change(
            "openstack-project-manager", cfg, "osism/python-osism", "0.20260615.0", "0.20260701.0"
        )
        is None
    )
