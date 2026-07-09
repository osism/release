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
            for m in tar.getmembers():
                target = (dest / m.name).resolve()
                root = dest.resolve()
                if target != root and root not in target.parents:
                    raise SourceError(f"unsafe archive member path: {m.name}")
                if not (m.isfile() or m.isdir()):
                    raise SourceError(f"unsafe archive member (link/special): {m.name}")
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
