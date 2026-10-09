"""Per-run HTTP request tally and GitHub rate-limit pickup in osism_drift.http."""

import pytest
import requests
import responses
from osism_drift import http
from osism_drift.http import SourceError

API = "https://api.github.com/repos/osism/release/tarball/main"
CODELOAD = "https://codeload.github.com/osism/release/legacy.tar.gz/main"
RAW = "https://raw.githubusercontent.com/osism/release/main/latest/base.yml"
CONTENTS = "https://api.github.com/repos/osism/release/contents/latest"
OPENDEV = "https://tarballs.opendev.org/openstack/nova/nova-stable-2025.1.tar.gz"
_LIMIT = {
    "X-RateLimit-Limit": "60",
    "X-RateLimit-Remaining": "57",
    "X-RateLimit-Used": "3",
    "X-RateLimit-Reset": "1791547920",
}


@pytest.fixture(autouse=True)
def _fresh_stats():
    http.reset_stats()
    yield
    http.reset_stats()


def test_no_requests_no_summary():
    assert http.summary() is None


@responses.activate
def test_get_counts_per_host_and_reads_the_github_limit():
    responses.add(responses.GET, CONTENTS, status=200, headers=_LIMIT)
    responses.add(responses.GET, RAW, status=200)
    http._get("listing", CONTENTS)
    http._get("fetching", RAW)
    http._get("fetching", RAW)
    line = http.summary()
    assert "api.github.com 1" in line
    assert "raw.githubusercontent.com 2" in line
    assert "GitHub API rate limit: 57 of 60 left" in line
    assert "resets" in line and "UTC" in line


@responses.activate
def test_archive_downloads_are_counted_apart_and_do_not_set_the_limit():
    """An archive download is a 302 from api.github.com to codeload. GitHub does
    not charge it to the REST rate limit (measured 2026-10-09: three anonymous
    tarball downloads left /rate_limit unchanged), and the redirect's own
    X-RateLimit-* headers do not reflect what was used. So it is counted under
    its own label, and its headers are ignored."""
    responses.add(
        responses.GET,
        API,
        status=302,
        headers={"Location": CODELOAD, **{**_LIMIT, "X-RateLimit-Remaining": "60"}},
    )
    responses.add(responses.GET, CODELOAD, status=200, body=b"x")
    http._get("fetching archive", API)
    line = http.summary()
    assert "api.github.com archives 1" in line
    assert "codeload.github.com 1" in line
    assert "rate limit" not in line


@responses.activate
def test_failed_and_retried_requests_are_counted():
    responses.add(responses.HEAD, OPENDEV, status=503)
    responses.add(responses.HEAD, OPENDEV, status=200)
    http.head("probing", OPENDEV, sleep=lambda _s: None)
    responses.add(responses.GET, RAW, body=requests.ConnectionError("down"))
    with pytest.raises(SourceError):
        http._get("fetching", RAW)
    line = http.summary()
    assert "tarballs.opendev.org 2" in line
    assert "raw.githubusercontent.com 1" in line
    assert "rate limit" not in line  # no api.github.com response seen
