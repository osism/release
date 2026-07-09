import io
import tarfile
import types

import pytest
import responses

from osism_drift import archive
from osism_drift.http import SourceError


def _targz(files, *, top="osism-release-abc123", extra_members=None):
    """files: {relpath: bytes} placed under a single top-level dir."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for rel, data in files.items():
            info = tarfile.TarInfo(f"{top}/{rel}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        for m in extra_members or []:
            tar.addfile(m, io.BytesIO(b""))
    return buf.getvalue()


def _stub():
    return types.SimpleNamespace(
        remote=types.SimpleNamespace(github_api="https://api.github.com/repos/"),
        snapshot_cache={},
    )


def test_repo_slug_and_archive_url():
    assert (
        archive._repo_slug("ansible_collection_services")
        == "ansible-collection-services"
    )
    assert (
        archive._archive_url(
            "https://api.github.com/repos/", "osism", "kolla_ansible", "stable/2025.2"
        )
        == "https://api.github.com/repos/osism/kolla-ansible/tarball/stable/2025.2"
    )


@responses.activate
def test_snapshot_dir_fetches_extracts_and_serves():
    cfg = _stub()
    responses.add(
        responses.GET,
        "https://api.github.com/repos/osism/kolla-ansible/tarball/main",
        body=_targz({"etc/x.yml": b"hi\n"}),
        status=200,
    )
    d = archive.snapshot_dir("osism", "kolla_ansible", "main", cfg)
    assert (d / "etc/x.yml").read_bytes() == b"hi\n"


@responses.activate
def test_snapshot_dir_memoizes_one_http_call():
    cfg = _stub()
    responses.add(
        responses.GET,
        "https://api.github.com/repos/osism/r/tarball/main",
        body=_targz({"f": b"z"}),
        status=200,
    )
    archive.snapshot_dir("osism", "r", "main", cfg)
    archive.snapshot_dir("osism", "r", "main", cfg)
    assert len(responses.calls) == 1  # second call served from snapshot_cache


@responses.activate
def test_snapshot_dir_404_raises():
    cfg = _stub()
    responses.add(
        responses.GET,
        "https://api.github.com/repos/osism/r/tarball/nope",
        status=404,
    )
    with pytest.raises(SourceError, match="no archive for r@nope"):
        archive.snapshot_dir("osism", "r", "nope", cfg)


@responses.activate
def test_fetch_retries_once_then_succeeds():
    cfg = _stub()
    url = "https://api.github.com/repos/osism/r/tarball/main"
    responses.add(responses.GET, url, status=500)  # first attempt fails
    responses.add(responses.GET, url, body=_targz({"f": b"z"}), status=200)
    d = archive.snapshot_dir("osism", "r", "main", cfg)
    assert (d / "f").read_bytes() == b"z"
    assert len(responses.calls) == 2


@responses.activate
def test_fetch_fails_twice_raises():
    cfg = _stub()
    url = "https://api.github.com/repos/osism/r/tarball/main"
    responses.add(responses.GET, url, status=500)
    responses.add(responses.GET, url, status=500)
    with pytest.raises(SourceError):
        archive.snapshot_dir("osism", "r", "main", cfg)


@responses.activate
def test_unsafe_traversal_member_rejected():
    cfg = _stub()
    bad = tarfile.TarInfo("../evil")  # escapes the extraction dir (resolves above it)
    bad.size = 0
    responses.add(
        responses.GET,
        "https://api.github.com/repos/osism/r/tarball/main",
        body=_targz({"ok": b"x"}, extra_members=[bad]),
        status=200,
    )
    with pytest.raises(SourceError):
        archive.snapshot_dir("osism", "r", "main", cfg)


@responses.activate
def test_symlink_member_rejected_on_fallback(monkeypatch):
    # Force the no-data_filter fallback path and confirm it rejects a link member.
    monkeypatch.delattr(tarfile, "data_filter", raising=False)
    cfg = _stub()
    link = tarfile.TarInfo("osism-r-abc/link")
    link.type = tarfile.SYMTYPE
    link.linkname = "/etc/passwd"
    responses.add(
        responses.GET,
        "https://api.github.com/repos/osism/r/tarball/main",
        body=_targz({"ok": b"x"}, extra_members=[link]),
        status=200,
    )
    with pytest.raises(SourceError):
        archive.snapshot_dir("osism", "r", "main", cfg)
