"""Local-or-remote source reads for OSISM repos.

A repo may carry a per-repo override in config.sources (owner and/or branch).
A set `branch` *pins* the repo: it is always read remotely at that ref, so the
result is deterministic regardless of any local checkout's current branch.
"""

import datetime
import os
import subprocess
from pathlib import Path
from urllib.parse import urlparse

import requests


class SourceError(Exception):
    """Raised on any read/list failure that should abort the run."""


# Hosts the GitHub bearer token may be sent to. github_raw/github_api are
# configurable (a mirror, proxy, or test server), so the token is attached only
# when the actual request host is public GitHub -- an ambient GITHUB_TOKEN must
# never leak to a non-GitHub endpoint someone pointed the base URLs at.
_GITHUB_HOSTS = frozenset({"github.com", "api.github.com", "raw.githubusercontent.com"})


def _auth_headers(url: str, extra: dict | None = None) -> dict:
    """Merge a GitHub bearer token into request headers when one is available
    AND `url` targets a public GitHub host.

    Reads GITHUB_TOKEN (then GH_TOKEN) from the environment. When set and the
    request goes to a GitHub host, the read is authenticated, lifting GitHub's
    unauthenticated 60/hr per-IP limit to 5000/hr; otherwise behaviour is
    unchanged (unauthenticated). Gating on the host keeps a token configured for
    github.com from leaking to a non-GitHub github_raw/github_api override. The
    Zuul periodic-daily job carries no token today, so this is a no-op there and
    a win for local/developer and any future authenticated runs.
    """
    headers = dict(extra or {})
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token and (urlparse(url).hostname or "") in _GITHUB_HOSTS:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _rate_limit_hint(r) -> str | None:
    """A helpful hint when response `r` is a GitHub rate-limit rejection, else None.

    GitHub reports its primary rate limit as HTTP 403 (or, more recently, 429)
    with X-RateLimit-Remaining: 0, and secondary limits as 403/429 with a
    Retry-After header. Unauthenticated requests share a low per-IP hourly
    budget that a full remote drift run can exhaust; a token lifts it (see
    _auth_headers), so the actionable advice differs by auth state. The exact
    quotas are not hardcoded here (GitHub changes them, and this string is read
    whenever the script runs, not when it was written): the actual limit in
    effect is echoed from the response's X-RateLimit-Limit header. A 403 without
    those markers is some other refusal (auth/permission), not throttling, and
    gets no hint.
    """
    if r.status_code not in (403, 429):
        return None
    retry_after = r.headers.get("Retry-After")
    if r.headers.get("X-RateLimit-Remaining") != "0" and retry_after is None:
        return None
    parts = ["GitHub API rate limit hit."]
    limit = r.headers.get("X-RateLimit-Limit")
    quota = f" (limit in effect: {limit}/hr)" if limit and limit.isdigit() else ""
    reset = r.headers.get("X-RateLimit-Reset")
    if retry_after and retry_after.isdigit():
        parts.append(f"Retry after {retry_after}s.")
    elif reset and reset.isdigit():
        when = datetime.datetime.fromtimestamp(
            int(reset), datetime.timezone.utc
        ).strftime("%Y-%m-%d %H:%M UTC")
        parts.append(f"Limit resets at {when}.")
    if os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN"):
        parts.append(
            f"This run is authenticated{quota}; wait for the reset or reduce "
            "concurrency."
        )
    else:
        parts.append(
            f"This run is unauthenticated{quota}; set GITHUB_TOKEN (or GH_TOKEN) "
            "to use the much higher authenticated limit."
        )
    return " ".join(parts)


def _http_error(action: str, url: str, r) -> SourceError:
    """SourceError for a non-ok HTTP response, with a rate-limit hint appended
    when the response looks like GitHub throttling rather than a plain failure."""
    msg = f"HTTP {r.status_code} {action} {url}"
    hint = _rate_limit_hint(r)
    if hint:
        msg = f"{msg} — {hint}"
    return SourceError(msg)


def _source(repo: str, config):
    return config.sources.get(repo)


def _owner(repo: str, config) -> str:
    s = _source(repo, config)
    if s is not None and s.owner:
        return s.owner
    return config.remote.default_owner


def _ref(repo: str, config) -> str:
    s = _source(repo, config)
    if s is not None and s.branch:
        return s.branch
    return config.remote.branch


def _is_pinned(repo: str, config) -> bool:
    s = _source(repo, config)
    return s is not None and s.branch is not None


def current_ref(repo: str, config) -> str:
    """The ref `repo` is read at (its source-override branch, else remote branch).

    Public so a plugin can label a finding with the ref it actually compared
    against instead of hardcoding one that drifts when the config pin changes.
    """
    return _ref(repo, config)


def _local_repo_dir(repo: str, config) -> Path | None:
    """First --base-dir (in order) that contains <repo-dir> (hyphenated name)."""
    name = repo.replace("_", "-")
    for base in config.base_dirs:
        cand = Path(base).expanduser() / name
        if cand.is_dir():
            return cand
    return None


def _resolve(repo: str, config):
    """('local', dir) | ('remote', None); raise SourceError on mode-B not-found.

    A pinned (upstream) repo resolves local only if its discovered dir is a git
    checkout — it is read at named refs via git objects, so a non-git dir cannot
    serve it (it falls to --remote-fallback / mode B). Unpinned (consumer) repos
    resolve local from any discovered dir (read as the working tree).
    """
    if not config.base_dirs:
        return ("remote", None)
    d = _local_repo_dir(repo, config)
    usable = d is not None and (not _is_pinned(repo, config) or (d / ".git").exists())
    if usable:
        return ("local", d)
    if config.remote_fallback:
        return ("remote", None)
    raise SourceError(
        f"repo {repo!r} not found under any --base-dir "
        f"({', '.join(config.base_dirs)}); pass --remote-fallback to fetch it remotely"
    )


def _git(d, *args):
    return subprocess.run(
        ["git", "-C", str(d), *args], capture_output=True, check=False
    )


def _resolve_local_ref(d, ref):
    """Clone-local name that resolves `ref` to a commit, or None. Tries the ref
    as-given, then <remote>/<ref> for EVERY configured remote, so a ref held only
    under a non-origin remote (e.g. gerrit/unmaintained/2024.1) still resolves."""
    cands = [ref]
    cands += [f"{r}/{ref}" for r in _git(d, "remote").stdout.decode().split()]
    for cand in cands:
        if (
            _git(d, "rev-parse", "--verify", "--quiet", f"{cand}^{{commit}}").returncode
            == 0
        ):
            return cand
    return None


def _git_show(d, ref, rel_path, optional=False):
    rref = _resolve_local_ref(d, ref)
    if rref is None:
        raise SourceError(
            f"ref {ref!r} not found in {d} — fetch it "
            f"(this repo is read at named refs via git)"
        )
    r = _git(d, "show", f"{rref}:{rel_path}")
    if r.returncode != 0:
        if optional:
            return None
        raise SourceError(f"{rel_path} absent at {ref} in {d}")
    return r.stdout


def _git_ls_tree(d, ref, rel_path, dirs_only=False):
    rref = _resolve_local_ref(d, ref)
    if rref is None:
        raise SourceError(
            f"ref {ref!r} not found in {d} — fetch it "
            f"(this repo is read at named refs via git)"
        )
    # Colon (subtree) form lists DIRECT CHILDREN by BASENAME (not full paths).
    r = _git(d, "ls-tree", f"{rref}:{rel_path}")
    if r.returncode != 0:
        raise SourceError(f"cannot list {rel_path} at {ref} in {d}")
    out = []
    for line in r.stdout.decode().splitlines():
        meta, _, name = line.partition("\t")  # "<mode> <type> <sha>\t<basename>"
        if not dirs_only or meta.split()[1] == "tree":
            out.append(name)
    return out


def _git_ref_exists(d, ref):
    return _resolve_local_ref(d, ref) is not None


def _remote_url(repo: str, rel_path: str, config) -> str:
    owner = _owner(repo, config)
    return (
        f"{config.remote.github_raw}{owner}/{repo.replace('_', '-')}/"
        f"{_ref(repo, config)}/{rel_path}"
    )


def read(repo: str, rel_path: str, config) -> bytes:
    """Read `rel_path` from `repo`; raise SourceError if it is absent."""
    where, d = _resolve(repo, config)
    if where == "local":
        if _is_pinned(repo, config):
            return _git_show(d, _ref(repo, config), rel_path)
        p = d / rel_path
        if not p.exists():
            raise SourceError(f"{rel_path} not found in local {repo} ({d})")
        return p.read_bytes()
    url = _remote_url(repo, rel_path, config)
    try:
        r = requests.get(url, timeout=30, headers=_auth_headers(url))
    except requests.RequestException as e:
        raise SourceError(f"network error fetching {url}: {e}") from e
    if r.status_code == 404:
        raise SourceError(f"404 not found: {url}")
    if not r.ok:
        raise _http_error("fetching", url, r)
    return r.content


def read_optional(repo: str, rel_path: str, config) -> bytes | None:
    """Like read(), but return None instead of raising when absent."""
    where, d = _resolve(repo, config)
    if where == "local":
        if _is_pinned(repo, config):
            return _git_show(d, _ref(repo, config), rel_path, optional=True)
        p = d / rel_path
        return p.read_bytes() if p.exists() else None
    url = _remote_url(repo, rel_path, config)
    try:
        r = requests.get(url, timeout=30, headers=_auth_headers(url))
    except requests.RequestException as e:
        raise SourceError(f"network error fetching {url}: {e}") from e
    if r.status_code == 404:
        return None
    if not r.ok:
        raise _http_error("fetching", url, r)
    return r.content


def list_tree(repo: str, rel_path: str, config, missing_ok: bool = False) -> list[str]:
    """Recursively list file paths (repo-relative) under `rel_path` in `repo`.

    Absent `rel_path` raises SourceError by default; with missing_ok=True returns [].
    """
    where, d = _resolve(repo, config)
    if where == "local":
        if _is_pinned(repo, config):
            rref = _resolve_local_ref(d, _ref(repo, config))
            if rref is None:
                raise SourceError(
                    f"ref {_ref(repo, config)!r} not found in {d} — fetch it"
                )
            r = _git(d, "ls-tree", "-r", rref, rel_path)
            if r.returncode != 0:
                if missing_ok:
                    return []
                raise SourceError(
                    f"cannot list {rel_path} at {_ref(repo, config)} in {d}"
                )
            out = []
            for line in r.stdout.decode().splitlines():
                _meta, _, path = line.partition("\t")
                out.append(path)
            return out
        p = d / rel_path
        if not p.is_dir():
            if missing_ok:
                return []
            raise SourceError(f"{rel_path} not a directory in local {repo} ({d})")
        return sorted(str(f.relative_to(d)) for f in p.rglob("*") if f.is_file())
    # Remote: GitHub git trees API — one request, recursive
    owner = _owner(repo, config)
    url = (
        f"{config.remote.github_api}{owner}/{repo.replace('_', '-')}/"
        f"git/trees/{_ref(repo, config)}?recursive=1"
    )
    try:
        r = requests.get(
            url,
            timeout=30,
            headers=_auth_headers(url, {"Accept": "application/vnd.github.v3+json"}),
        )
    except requests.RequestException as e:
        raise SourceError(f"network error listing tree {url}: {e}") from e
    if r.status_code == 404:
        if missing_ok:
            return []
        raise SourceError(f"404 not found: {url}")
    if not r.ok:
        raise _http_error("listing tree", url, r)
    prefix = rel_path.rstrip("/") + "/"
    return [
        item["path"]
        for item in r.json().get("tree", [])
        if item.get("type") == "blob" and item["path"].startswith(prefix)
    ]


def list_dir(repo: str, rel_path: str, config, dirs_only: bool = False) -> list[str]:
    """List entries under `rel_path` in `repo` (directories only if `dirs_only`)."""
    where, d = _resolve(repo, config)
    if where == "local":
        if _is_pinned(repo, config):
            return _git_ls_tree(d, _ref(repo, config), rel_path, dirs_only)
        p = d / rel_path
        if not p.is_dir():
            raise SourceError(f"{rel_path} not a directory in local {repo} ({d})")
        return [x.name for x in p.iterdir() if (not dirs_only or x.is_dir())]
    owner = _owner(repo, config)
    url = (
        f"{config.remote.github_api}{owner}/{repo.replace('_', '-')}/"
        f"contents/{rel_path}?ref={_ref(repo, config)}"
    )
    try:
        r = requests.get(
            url,
            timeout=30,
            headers=_auth_headers(url, {"Accept": "application/vnd.github.v3+json"}),
        )
    except requests.RequestException as e:
        raise SourceError(f"network error listing {url}: {e}") from e
    if r.status_code == 404:
        raise SourceError(f"404 not found: {url}")
    if not r.ok:
        raise _http_error("listing", url, r)
    items = r.json()
    if dirs_only:
        items = [it for it in items if it.get("type") == "dir"]
    return [item["name"] for item in items]


def list_dir_at_ref(
    repo: str, rel_path: str, ref: str, config, dirs_only: bool = False
) -> list[str]:
    """List a repo directory at an explicit git ref.

    A pinned repo resolving to a git checkout under a --base-dir is listed from
    the local git tree at `ref` (objects, never the working tree); every other
    case (an unpinned repo, or no local checkout) uses the GitHub contents API
    at `ref`. This mirrors read_at_ref/ref_exists. Either way the explicit `ref`
    is read, not the per-repo pin's branch, so a range check is deterministic.
    """
    where, d = _resolve(repo, config)
    if where == "local" and _is_pinned(repo, config):
        return _git_ls_tree(d, ref, rel_path, dirs_only)
    owner = _owner(repo, config)
    url = (
        f"{config.remote.github_api}{owner}/{repo.replace('_', '-')}/"
        f"contents/{rel_path}?ref={ref}"
    )
    try:
        r = requests.get(
            url,
            timeout=30,
            headers=_auth_headers(url, {"Accept": "application/vnd.github.v3+json"}),
        )
    except requests.RequestException as e:
        raise SourceError(f"network error listing {url}: {e}") from e
    if r.status_code == 404:
        raise SourceError(f"404 not found: {url}")
    if not r.ok:
        raise _http_error("listing", url, r)
    items = r.json()
    if dirs_only:
        items = [it for it in items if it.get("type") == "dir"]
    return [item["name"] for item in items]


def ref_exists(repo: str, ref: str, config) -> bool:
    """True if `ref` (branch/tag/sha) resolves in the upstream repo (local clone
    when it resolves under a --base-dir, else the GitHub commits API)."""
    where, d = _resolve(repo, config)
    if where == "local" and _is_pinned(repo, config):
        return _git_ref_exists(d, ref)
    owner = _owner(repo, config)
    url = f"{config.remote.github_api}{owner}/{repo.replace('_', '-')}/commits/{ref}"
    try:
        r = requests.get(
            url,
            timeout=30,
            headers=_auth_headers(url, {"Accept": "application/vnd.github.v3+json"}),
        )
    except requests.RequestException as e:
        raise SourceError(f"network error checking ref {url}: {e}") from e
    # GitHub's commits API returns 422 (not 404) for a ref that does not
    # resolve; treat both as "absent" so the resolver probes the next candidate.
    if r.status_code in (404, 422):
        return False
    if not r.ok:
        raise _http_error("checking ref", url, r)
    return True


_REF_CANDIDATES = ("stable/{r}", "unmaintained/{r}", "{r}-eol", "{r}-eom")


def release_to_ref(repo: str, release: str, config) -> str:
    """Resolve an OSISM release (e.g. '2024.2') to an existing upstream ref.

    OSISM builds releases upstream has moved past EOL, so ref naming is
    non-uniform: a release_refs override wins, else probe stable/ ->
    unmaintained/ -> <r>-eol -> <r>-eom and take the first that exists. None
    exists -> SourceError (loud, never a silent 404 mid-listing). Results are
    memoized on config.ref_cache so repeated resolves across plugins do not
    re-probe (each (repo, release) costs at most one probe sequence per run).
    """
    override = (config.release_refs.get(repo) or {}).get(release)
    if override:
        return override
    cached = config.ref_cache.get((repo, release))
    if cached is not None:
        return cached
    for tmpl in _REF_CANDIDATES:
        cand = tmpl.format(r=release)
        if ref_exists(repo, cand, config):
            config.ref_cache[(repo, release)] = cand
            return cand
    tried = ", ".join(t.format(r=release) for t in _REF_CANDIDATES)
    raise SourceError(
        f"no upstream ref for {repo} release {release}: tried {tried} "
        f"(set release_refs to override)"
    )


def read_at_ref(
    repo: str, rel_path: str, ref: str, config, optional: bool = False
) -> bytes | None:
    """Read a repo file at an explicit git ref. Always remote.

    Local (the repo resolves under a --base-dir): read the git object at `ref`.
    Remote: github_raw at `ref`. The explicit ref is read either way, ignoring
    any per-repo pin. optional=True maps an absent path (local) or a 404 (remote)
    to None so the caller can probe an alternative (e.g. monolithic all.yml ->
    split all/ dir).
    """
    where, d = _resolve(repo, config)
    if where == "local" and _is_pinned(repo, config):
        return _git_show(d, ref, rel_path, optional=optional)
    owner = _owner(repo, config)
    url = (
        f"{config.remote.github_raw}{owner}/{repo.replace('_', '-')}/"
        f"{ref}/{rel_path}"
    )
    try:
        r = requests.get(url, timeout=30, headers=_auth_headers(url))
    except requests.RequestException as e:
        raise SourceError(f"network error fetching {url}: {e}") from e
    if r.status_code == 404:
        if optional:
            return None
        raise SourceError(f"404 not found: {url}")
    if not r.ok:
        raise _http_error("fetching", url, r)
    return r.content


def describe_resolution(repos, config) -> list[str]:
    """One human log line per repo (sorted). Raises SourceError listing *every*
    mode-B not-found repo (not just the first), so the driver can abort before
    any comparison runs and the user sees all missing repos at once."""
    lines = []
    missing = []
    for repo in sorted(repos):
        try:
            where, d = _resolve(repo, config)
        except SourceError:
            missing.append(repo)
            continue
        if where == "local" and _is_pinned(repo, config):
            lines.append(
                f"  {repo:<32} local  {d} @ {_ref(repo, config)} "
                f"(+per-release range refs)  [git refs, must be current]"
            )
        elif where == "local":
            lines.append(f"  {repo:<32} local  {d}  [working tree, as-is]")
        elif _is_pinned(repo, config):
            owner = _owner(repo, config)
            lines.append(
                f"  {repo:<32} remote {owner}/{repo.replace('_', '-')} "
                f"@ {_ref(repo, config)} (+per-release range refs)  [remote]"
            )
        else:
            owner = _owner(repo, config)
            tail = ", not found locally" if config.base_dirs else ""
            lines.append(
                f"  {repo:<32} remote {owner}/{repo.replace('_', '-')} "
                f"@ {config.remote.branch}  [remote{tail}]"
            )
    if missing:
        bases = ", ".join(str(b) for b in config.base_dirs)
        names = ", ".join(missing)
        raise SourceError(
            f"{len(missing)} repo(s) not found under any --base-dir ({bases}): "
            f"{names}; pass --remote-fallback to fetch them remotely"
        )
    return lines
