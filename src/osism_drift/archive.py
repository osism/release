import atexit
import io
import shutil
import tarfile
import tempfile
from pathlib import Path

from osism_drift.http import SourceError, _get


def _repo_slug(repo):
    # GitHub repo names are hyphenated; every remote URL applies this.
    return repo.replace("_", "-")


def _archive_url(github_api, owner, repo, ref):
    return f"{github_api}{owner}/{_repo_slug(repo)}/tarball/{ref}"


def _fetch_archive_bytes(repo, ref, url):
    """GET the tarball via the shared _get (auth + error handling), retry once.
    200 -> bytes; 404 -> SourceError (ref was expected to exist)."""
    last = None
    for attempt in (1, 2):
        try:
            r = _get("fetching archive", url, ok=(404,), timeout=60)
        except SourceError as e:
            last = e
            continue  # transport / non-ok: retry once, then re-raise below
        if r.status_code == 404:
            raise SourceError(f"no archive for {repo}@{ref}")
        return r.content
    raise last


def _safe_extract(data, dest):
    """Extract a .tar.gz into dest, rejecting anything that could escape it."""
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        if hasattr(tarfile, "data_filter"):
            try:
                tar.extractall(dest, filter="data")
            except tarfile.FilterError as e:
                raise SourceError(f"unsafe archive member: {e}") from e
        else:
            root = dest.resolve()

            def _within(p):
                return p == root or root in p.parents

            for m in tar.getmembers():
                target = (dest / m.name).resolve()
                if not _within(target):
                    raise SourceError(f"unsafe archive member path: {m.name}")
                # Emulate the 'data' filter for older Pythons: permit links
                # whose target stays inside the extraction root (the release
                # repo aliases per-version ceph.yml/openstack.yml this way),
                # reject links that escape and any other special member.
                if m.issym():
                    link = (target.parent / m.linkname).resolve()
                    if not _within(link):
                        raise SourceError(
                            f"unsafe archive member (link escapes): {m.name}"
                        )
                elif m.islnk():
                    link = (dest / m.linkname).resolve()
                    if not _within(link):
                        raise SourceError(
                            f"unsafe archive member (link escapes): {m.name}"
                        )
                elif not (m.isfile() or m.isdir()):
                    raise SourceError(f"unsafe archive member (special): {m.name}")
            # Members are already validated above. Old Pythons (no data_filter)
            # extract fully-trusted by default; newer ones need it stated so
            # they don't consult the (absent) default filter.
            try:
                tar.extractall(dest, filter="fully_trusted")
            except TypeError:
                tar.extractall(dest)


def _extract_snapshot(data, repo, ref):
    tmp = Path(tempfile.mkdtemp(prefix="osism-drift-"))
    atexit.register(shutil.rmtree, tmp, ignore_errors=True)
    _safe_extract(data, tmp)
    # GitHub archives contain a single top-level directory; return it.
    entries = list(tmp.iterdir())
    if len(entries) == 1 and entries[0].is_dir():
        return entries[0]
    return tmp


def snapshot_dir(owner, repo, ref, config):
    """Extracted-tree Path for (repo, ref); fetched+extracted once, then memoized."""
    key = (config.remote.github_api, owner, _repo_slug(repo), ref)
    cached = config.snapshot_cache.get(key)
    if cached is not None:
        return cached
    url = _archive_url(config.remote.github_api, owner, repo, ref)
    data = _fetch_archive_bytes(repo, ref, url)
    root = _extract_snapshot(data, repo, ref)
    config.snapshot_cache[key] = root
    return root
