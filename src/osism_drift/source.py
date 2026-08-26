"""Local-or-remote source reads for OSISM repos.

A repo may carry a per-repo override in config.sources (owner and/or branch).
A set `branch` *pins* the repo: it is always read remotely at that ref, so the
result is deterministic regardless of any local checkout's current branch.
"""

import re
import subprocess
from pathlib import Path

from osism_drift import archive
from osism_drift.http import SourceError, _get

# Re-exported so callers/tests reach the transport layer via osism_drift.source
# unchanged after the http.py split.
from osism_drift.http import (  # noqa: F401
    _auth_headers,
    _rate_limit_hint,
    _http_error,
    _GITHUB_HOSTS,
    _GH_JSON,
)

# Progress sink for remote reads. A remote drift run makes dozens of GitHub
# requests over minutes with no other output, which reads as "hung" and invites
# an impatient Ctrl-C. The driver installs a sink (unless --quiet) so each read
# prints a short activity line; the default is a no-op, keeping library and test
# callers silent. Only remote branches reach _note(), so local runs stay quiet.
_progress = None
_calls = 0


def set_progress(sink) -> None:
    """Install a callable(line:str) to receive one line per remote read."""
    global _progress, _calls
    _progress = sink
    _calls = 0


def _note(kind: str, repo: str, ref: str, detail: str = "") -> None:
    """Emit one short progress line for a remote read (no-op without a sink)."""
    global _calls
    _calls += 1
    if _progress is None:
        return
    loc = f"{repo}:{detail}" if detail else repo
    _progress(f"  [{_calls:>3}] {kind:<5} {loc} @ {ref}")


def _dir_read(d, rel_path, where):
    p = d / rel_path
    if not p.exists():
        raise SourceError(f"{rel_path} not found in {where}")
    return p.read_bytes()


def _dir_read_optional(d, rel_path):
    p = d / rel_path
    return p.read_bytes() if p.exists() else None


def _dir_list_tree(d, rel_path, where, missing_ok=False):
    p = d / rel_path
    if not p.is_dir():
        if missing_ok:
            return []
        raise SourceError(f"{rel_path} not a directory in {where}")
    return sorted(str(f.relative_to(d)) for f in p.rglob("*") if f.is_file())


def _dir_list(d, rel_path, where, dirs_only=False):
    p = d / rel_path
    if not p.is_dir():
        raise SourceError(f"{rel_path} not a directory in {where}")
    return [x.name for x in p.iterdir() if (not dirs_only or x.is_dir())]


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
        return _dir_read(d, rel_path, f"local {repo} ({d})")
    _note("raw", repo, _ref(repo, config), rel_path)
    if config.archive:
        ref = _ref(repo, config)
        d = archive.snapshot_dir(_owner(repo, config), repo, ref, config)
        return _dir_read(d, rel_path, f"{repo}@{ref} snapshot")
    url = _remote_url(repo, rel_path, config)
    r = _get("fetching", url, ok=(404,))
    if r.status_code == 404:
        raise SourceError(f"404 not found: {url}")
    return r.content


def read_optional(repo: str, rel_path: str, config) -> bytes | None:
    """Like read(), but return None instead of raising when absent."""
    where, d = _resolve(repo, config)
    if where == "local":
        if _is_pinned(repo, config):
            return _git_show(d, _ref(repo, config), rel_path, optional=True)
        return _dir_read_optional(d, rel_path)
    _note("raw", repo, _ref(repo, config), rel_path)
    if config.archive:
        ref = _ref(repo, config)
        d = archive.snapshot_dir(_owner(repo, config), repo, ref, config)
        return _dir_read_optional(d, rel_path)
    url = _remote_url(repo, rel_path, config)
    r = _get("fetching", url, ok=(404,))
    if r.status_code == 404:
        return None
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
        return _dir_list_tree(d, rel_path, f"local {repo} ({d})", missing_ok)
    # Remote: GitHub git trees API — one request, recursive
    owner = _owner(repo, config)
    _note("tree", repo, _ref(repo, config), rel_path)
    if config.archive:
        ref = _ref(repo, config)
        d = archive.snapshot_dir(owner, repo, ref, config)
        return _dir_list_tree(d, rel_path, f"{repo}@{ref} snapshot", missing_ok)
    url = (
        f"{config.remote.github_api}{owner}/{repo.replace('_', '-')}/"
        f"git/trees/{_ref(repo, config)}?recursive=1"
    )
    r = _get("listing tree", url, json_api=True, ok=(404,))
    if r.status_code == 404:
        if missing_ok:
            return []
        raise SourceError(f"404 not found: {url}")
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
        return _dir_list(d, rel_path, f"local {repo} ({d})", dirs_only)
    owner = _owner(repo, config)
    _note("list", repo, _ref(repo, config), rel_path)
    if config.archive:
        ref = _ref(repo, config)
        d = archive.snapshot_dir(owner, repo, ref, config)
        return _dir_list(d, rel_path, f"{repo}@{ref} snapshot", dirs_only)
    url = (
        f"{config.remote.github_api}{owner}/{repo.replace('_', '-')}/"
        f"contents/{rel_path}?ref={_ref(repo, config)}"
    )
    r = _get("listing", url, json_api=True, ok=(404,))
    if r.status_code == 404:
        raise SourceError(f"404 not found: {url}")
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
    _note("list", repo, ref, rel_path)
    if config.archive:
        d = archive.snapshot_dir(owner, repo, ref, config)
        return _dir_list(d, rel_path, f"{repo}@{ref} snapshot", dirs_only)
    url = (
        f"{config.remote.github_api}{owner}/{repo.replace('_', '-')}/"
        f"contents/{rel_path}?ref={ref}"
    )
    r = _get("listing", url, json_api=True, ok=(404,))
    if r.status_code == 404:
        raise SourceError(f"404 not found: {url}")
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
    _note("ref?", repo, ref)
    r = _get("checking ref", url, json_api=True, ok=(404, 422))
    # GitHub's commits API returns 422 (not 404) for a ref that does not
    # resolve; treat both as "absent" so the resolver probes the next candidate.
    if r.status_code in (404, 422):
        return False
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


_SHA40 = re.compile(r"^[0-9a-f]{40}$")


def _checked_sha(repo: str, ref: str, sha: str) -> str:
    """Enforce resolve_commit's documented contract at its own boundary.

    Callers pin refs from this value, so an abbreviated or malformed id must not
    escape just because the current call sites happen to re-validate later.
    """
    if not _SHA40.match(sha):
        raise SourceError(
            f"{repo}: resolving {ref!r} gave {sha!r}, not a 40-hex commit"
        )
    return sha


def local_checkout(repo: str, config):
    """The local git dir backing `repo`, or None when this run reads remotely.

    Lets callers branch on locality (attribution needs `git log`) without reaching
    into _resolve from another module.
    """
    where, d = _resolve(repo, config)
    return d if (where == "local" and _is_pinned(repo, config)) else None


def _resolve_upstream_ref(repo, d, ref):
    """Resolve an upstream release ref, preferring remote-tracking over local.

    _resolve_local_ref() tries the bare ref first, which is right for a consumer
    repo read as a working tree but wrong for a pinned upstream read at a release
    ref: a stale local branch of the same name then silently wins. Observed in a
    real checkout -- refs/heads/stable/2025.2 sat five commits behind
    refs/remotes/origin/stable/2025.2, and sync-mirror proposed reverting an
    upstream fix that had landed in between.

    Remotes are tried in name order for determinism. A local head is used only
    when no remote carries the ref, and a disagreement is announced rather than
    resolved quietly.
    """
    # Enumerate the refs that exist rather than the configured remotes: a clone can
    # carry refs/remotes/<r>/<ref> without <r> being listed by `git remote`.
    found = (
        _git(d, "for-each-ref", "--format=%(refname)", f"refs/remotes/*/{ref}")
        .stdout.decode()
        .split()
    )
    if found:
        cand = sorted(found)[0]
        local = _git(d, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
        if local.returncode == 0:
            lo = local.stdout.decode().strip()
            hi = _git(d, "rev-parse", f"{cand}^{{commit}}").stdout.decode().strip()
            if lo != hi:
                _note(
                    "stale local ref",
                    repo,
                    ref,
                    f"local {lo[:12]} != {cand} {hi[:12]}; using {cand}",
                )
        return cand
    if _git(d, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}").returncode == 0:
        return ref
    raise SourceError(f"cannot resolve {ref!r} to a commit in {repo}")


def resolve_commit(repo: str, ref: str, config) -> str:
    """Peel `ref` to a 40-hex commit id in the upstream repo.

    release_to_ref() yields a ref NAME (stable/<r>, unmaintained/<r>, <r>-eol,
    <r>-eom); a verbatim mirror has to record the commit, because those branches
    take backports and a name is not a reproducible snapshot. Local reads use the
    clone's own resolution (which also tries <remote>/<ref>); remote reads take
    .sha from the same commits endpoint ref_exists() already calls.
    """
    d = local_checkout(repo, config)
    if d is not None:
        cand = _resolve_upstream_ref(repo, d, ref)
        out = _git(d, "rev-parse", "--verify", "--quiet", f"{cand}^{{commit}}")
        if out.returncode != 0:
            raise SourceError(f"cannot resolve {ref!r} to a commit in {repo}")
        return _checked_sha(repo, ref, out.stdout.decode().strip())
    owner = _owner(repo, config)
    url = f"{config.remote.github_api}{owner}/{repo.replace('_', '-')}/commits/{ref}"
    _note("commit?", repo, ref)
    r = _get("resolving commit", url, json_api=True, ok=(404, 422))
    if r.status_code in (404, 422):
        raise SourceError(f"cannot resolve {ref!r} to a commit in {repo}")
    sha = (r.json() or {}).get("sha")
    if not sha:
        raise SourceError(f"commits API returned no sha for {repo} {ref!r}")
    return _checked_sha(repo, ref, sha)


def _require_objects(repo, d, *shas):
    for sha in shas:
        if _git(d, "cat-file", "-e", f"{sha}^{{commit}}").returncode != 0:
            raise SourceError(
                f"{repo}: {sha[:12]} not present locally (shallow clone?); "
                "cannot compare history"
            )


def is_ancestor(repo: str, ancestor: str, descendant: str, config) -> bool:
    """True if `ancestor` is reachable from `descendant`.

    `git merge-base --is-ancestor` exits 0 for yes, 1 for a valid "no", and
    anything else (128 for an unknown commit) for an operational failure. Mapping
    every non-zero to False would report "not an ancestor" for a broken comparison
    and let a wrong pin through, so only 0 and 1 are treated as answers.
    """
    d = local_checkout(repo, config)
    if d is not None:
        _require_objects(repo, d, ancestor, descendant)
        rc = _git(d, "merge-base", "--is-ancestor", ancestor, descendant).returncode
        if rc == 0:
            return True
        if rc == 1:
            return False
        raise SourceError(
            f"{repo}: cannot compare {ancestor[:12]}..{descendant[:12]} "
            f"(git exit {rc})"
        )
    owner = _owner(repo, config)
    url = (
        f"{config.remote.github_api}{owner}/{repo.replace('_', '-')}"
        f"/compare/{ancestor}...{descendant}"
    )
    _note("compare", repo, f"{ancestor[:12]}..{descendant[:12]}")
    r = _get("comparing refs", url, json_api=True, ok=(404, 422))
    if r.status_code in (404, 422):
        raise SourceError(f"{repo}: cannot compare {ancestor[:12]}..{descendant[:12]}")
    return (r.json() or {}).get("status") in ("identical", "ahead")


def merge_base(repo: str, a: str, b: str, config):
    """Best common ancestor of `a` and `b`, or None if they share no history.

    Public because sync-mirror needs it in both modes: locally `git merge-base`,
    remotely merge_base_commit.sha from the same compare endpoint is_ancestor uses.
    Only exit 1 means "no merge base"; anything else is a failure, because a caller
    that reads a failure as "none" would silently skip a bound it depends on.
    """
    d = local_checkout(repo, config)
    if d is not None:
        _require_objects(repo, d, a, b)
        r = _git(d, "merge-base", a, b)
        if r.returncode == 0:
            return r.stdout.decode().strip()
        if r.returncode == 1:
            return None
        raise SourceError(
            f"{repo}: merge-base {a[:12]}..{b[:12]} failed (git exit {r.returncode})"
        )
    owner = _owner(repo, config)
    url = (
        f"{config.remote.github_api}{owner}/{repo.replace('_', '-')}/compare/{a}...{b}"
    )
    _note("merge-base", repo, f"{a[:12]}..{b[:12]}")
    r = _get("finding merge base", url, json_api=True, ok=(404, 422))
    if r.status_code in (404, 422):
        raise SourceError(f"{repo}: cannot compare {a[:12]}..{b[:12]}")
    sha = ((r.json() or {}).get("merge_base_commit") or {}).get("sha")
    if sha is not None and not _SHA40.match(sha):
        raise SourceError(f"{repo}: compare returned a bad merge base {sha!r}")
    return sha


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
    _note("raw", repo, ref, rel_path)
    if config.archive:
        d = archive.snapshot_dir(owner, repo, ref, config)
        return (
            _dir_read_optional(d, rel_path)
            if optional
            else _dir_read(d, rel_path, f"{repo}@{ref} snapshot")
        )
    url = (
        f"{config.remote.github_raw}{owner}/{repo.replace('_', '-')}/"
        f"{ref}/{rel_path}"
    )
    r = _get("fetching", url, ok=(404,))
    if r.status_code == 404:
        if optional:
            return None
        raise SourceError(f"404 not found: {url}")
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
