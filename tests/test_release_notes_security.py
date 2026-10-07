import importlib.util
import pathlib

import responses

# release-notes.py is hyphenated -> not importable by name; load it by path.
_SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "release-notes.py"
_spec = importlib.util.spec_from_file_location("release_notes", _SRC)
rn = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rn)

API = "https://api.github.com/repos/osism/container-images-kolla"

PAGE = """---
sidebar_label: OSSA-2026-037
---

# OSSA-2026-037: Inconsistent scope enforcement for delegated tokens in Keystone

| Property         | Value |
|:-----------------|:------|
| Date             | 2026-08-25 (Errata: 2026-08-26) |
| CVE              | [CVE-2026-80182](https://www.cve.org/CVERecord?id=CVE-2026-80182), [CVE-2026-80184 |
| Severity         | High |
| Affected Project | Keystone |

## Summary

Two related vulnerabilities were discovered in OpenStack Keystone.

Tokens could escape their project scope.

## Affected Versions

| Epoxy (2025.1) | Patch ([PR #776](https://github.com/osism/container-images-kolla/pull/776)) |

## References

- [OSISM Fix](https://github.com/osism/container-images-kolla/commit/01ae42cdce2dc6c86927116fcce10c90a930122b)
- [OSISM Fix (PR #776)](https://github.com/osism/container-images-kolla/pull/776)
"""


def test_parse_advisory():
    advisory = rn.parse_advisory("ossa-2026-037", PAGE)
    assert advisory["id"] == "OSSA-2026-037"
    assert advisory["link"] == "../appendix/security/ossa-2026-037.md"
    assert (
        advisory["title"]
        == "Inconsistent scope enforcement for delegated tokens in Keystone"
    )
    assert advisory["date"] == "2026-08-25"
    assert advisory["cves"] == ["CVE-2026-80182", "CVE-2026-80184"]
    assert advisory["project"] == "Keystone"
    assert advisory["summary"] == (
        "Two related vulnerabilities were discovered in OpenStack Keystone.\n\n"
        "Tokens could escape their project scope."
    )
    assert advisory["commits"] == ["01ae42cdce2dc6c86927116fcce10c90a930122b"]
    assert advisory["pulls"] == ["776"]


def _advisory(date="2026-09-21", commits=(), pulls=()):
    return {
        "id": "OSSA-2026-039",
        "date": date,
        "commits": list(commits),
        "pulls": list(pulls),
    }


def _commit(sha, *files):
    responses.add(
        responses.GET,
        f"{API}/commits/{sha}",
        json={"files": [{"filename": f} for f in files]},
        status=200,
    )


@responses.activate
def test_patch_in_range_for_the_release_version():
    _commit("01ae42cdce", "patches/2025.1/keystone/0001.patch")
    responses.add(
        responses.GET,
        f"{API}/pulls/776",
        json={"merge_commit_sha": "01ae42cdce2dc6c86927116fcce10c90a930122b"},
        status=200,
    )
    # the short commit ref and the PR resolve to the same commit: one lookup
    advisory = _advisory(date="2026-01-01", commits=["01ae42cdce"], pulls=["776"])
    assert rn.advisory_fix_in_release(
        advisory,
        ["01ae42cdce2dc6c86927116fcce10c90a930122b"],
        "2025.1",
        "2026-08-14",
        "2026-10-01",
    ) == ("patch", "01ae42cdce")
    assert len([c for c in responses.calls if "/commits/" in c.request.url]) == 1


@responses.activate
def test_patch_for_the_release_version_outside_the_range_is_not_new():
    # The 2025.1 patch landed before the previous image; only a 2024.1
    # backport is in range. Neither the range nor the date makes it new.
    _commit("aaaa111", "patches/2025.1/glance/0001.patch")
    _commit("bbbb222", "patches/2024.1/glance/0001.patch")
    advisory = _advisory(date="2026-09-21", commits=["aaaa111", "bbbb222"])
    assert (
        rn.advisory_fix_in_release(
            advisory, ["bbbb222000"], "2025.1", "2026-08-14", "2026-10-01"
        )
        is None
    )


@responses.activate
def test_upstream_fix_is_dated_between_the_builds():
    # Only backports for older releases are referenced: the 2025.1 fix
    # came with the upstream sources, so the advisory date decides
    _commit("efaa873eeb", "patches/2024.1/octavia/0001.patch")
    advisory = _advisory(date="2026-09-21", commits=["efaa873eeb"])
    assert rn.advisory_fix_in_release(
        advisory, ["efaa873eeb0"], "2025.1", "2026-08-14", "2026-10-01"
    ) == ("upstream", None)
    assert (
        rn.advisory_fix_in_release(
            advisory, ["efaa873eeb0"], "2025.1", "2026-09-21", "2026-10-01"
        )
        is None
    )
    assert (
        rn.advisory_fix_in_release(
            advisory, ["efaa873eeb0"], "2025.1", "2026-08-14", "2026-09-20"
        )
        is None
    )


@responses.activate
def test_failed_commit_lookup_claims_nothing():
    responses.add(responses.GET, f"{API}/commits/dead", status=500)
    advisory = _advisory(date="2026-09-21", commits=["dead"])
    assert (
        rn.advisory_fix_in_release(
            advisory, ["dead"], "2025.1", "2026-08-14", "2026-10-01"
        )
        is None
    )


@responses.activate
def test_range_commits_are_paginated():
    page1 = [{"sha": f"{i:040x}"} for i in range(rn.COMMITS_PER_PAGE)]
    page2 = [{"sha": "f" * 40}]
    responses.add(
        responses.GET,
        f"{API}/compare/v0.20260814.0...v0.20261001.0",
        json={"commits": page1},
        status=200,
        match=[
            responses.matchers.query_param_matcher({"per_page": "100", "page": "1"})
        ],
    )
    responses.add(
        responses.GET,
        f"{API}/compare/v0.20260814.0...v0.20261001.0",
        json={"commits": page2},
        status=200,
        match=[
            responses.matchers.query_param_matcher({"per_page": "100", "page": "2"})
        ],
    )
    shas = rn.list_range_commits(
        "osism/container-images-kolla", "0.20260814.0", "0.20261001.0"
    )
    assert len(shas) == rn.COMMITS_PER_PAGE + 1
    assert shas[-1] == "f" * 40


@responses.activate
def test_security_section_from_a_site_checkout(tmp_path, monkeypatch):
    site = tmp_path / "docs" / "appendix" / "security"
    site.mkdir(parents=True)
    (site / "ossa-2026-037.md").write_text(PAGE)
    (site / "index.md").write_text("# Security\n")
    _commit(
        "01ae42cdce2dc6c86927116fcce10c90a930122b", "patches/2025.1/keystone/0001.patch"
    )
    responses.add(
        responses.GET,
        f"{API}/pulls/776",
        json={"merge_commit_sha": "01ae42cdce2dc6c86927116fcce10c90a930122b"},
        status=200,
    )
    responses.add(
        responses.GET,
        f"{API}/compare/v0.20260814.0...v0.20261001.0",
        json={"commits": [{"sha": "01ae42cdce2dc6c86927116fcce10c90a930122b"}]},
        status=200,
    )
    monkeypatch.setattr(rn, "release_openstack_version", lambda prefix, v: "2025.1")

    previous = {("docker_images", "kolla"): "0.20260814.0"}
    current = {("docker_images", "kolla"): "0.20261001.0"}
    section = rn.security_advisory_section(
        previous, current, "10.2.0", "20261001", str(tmp_path)
    )
    assert section.startswith("## Security advisories fixed in this release\n")
    assert (
        "This release includes all security fixes that were known and patched "
        "up to 1 October 2026. Compared to OSISM 10.2.0, the container images "
        "additionally contain the fixes for the following advisories:" in section
    )
    assert "[Security](../appendix/security/index.md)" in section
    assert (
        "- Bullet prefix: [OSSA-2026-037](../appendix/security/ossa-2026-037.md) "
        "(Keystone, CVE-2026-80182, CVE-2026-80184):" in section
    )
    assert (
        "- Fix: downstream patch, osism/container-images-kolla commit 01ae42cdce"
        in section
    )
    assert "Tokens could escape their project scope." in section

    # unchanged kolla images: nothing to check
    assert rn.security_advisory_section(current, current, "10.2.0", "20261001") is None


def test_metalbox_subsection_moves_last():
    body = [
        "### Security fixes",
        "",
        "- a",
        "",
        "### MetalBox",
        "",
        "Intro.",
        "",
        "#### SONiC",
        "",
        "- b",
        "",
        "### Notable changes",
        "",
        "- c",
    ]
    moved, flag = rn.move_metalbox_last(body)
    assert flag
    assert moved == [
        "### Security fixes",
        "",
        "- a",
        "",
        "### Notable changes",
        "",
        "- c",
        "",
        "### MetalBox",
        "",
        "Intro.",
        "",
        "#### SONiC",
        "",
        "- b",
        "",
    ]
    # already last (or absent): untouched
    assert rn.move_metalbox_last(moved) == (moved, False)
    assert rn.move_metalbox_last(body[:4]) == (body[:4], False)


def test_metalbox_heading_inside_a_code_fence_is_ignored():
    body = ["### A", "", "```", "### MetalBox", "```", "", "### B", "", "- x"]
    assert rn.move_metalbox_last(body) == (body, False)


def test_long_date():
    assert rn.long_date("20261001") == "1 October 2026"
    assert rn.long_date("20260814") == "14 August 2026"
